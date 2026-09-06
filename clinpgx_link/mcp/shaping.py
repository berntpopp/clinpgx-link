"""Bound source pages, fencing source JSON as data and preserving original bytes.

Paged adapter values are retained as an explicitly derived representation, separate
from the original HTTP body. This preserves decoding and response metadata across
continuations without another upstream request or a second unbounded memory cache.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from typing import Any, Literal

from fastmcp.tools.base import ToolResult

from clinpgx_link.content.store import ContentStore
from clinpgx_link.exceptions import DataValidationError, InvalidInputError, ResponseTooLargeError
from clinpgx_link.mcp.envelope import success_result
from clinpgx_link.mcp.pagination import CursorCodec
from clinpgx_link.mcp.untrusted_content import fence_text
from clinpgx_link.models import SourceInfo, SourceResponse

ResponseMode = Literal["minimal", "compact", "standard", "full"]


def _json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise DataValidationError("Adapter result is not finite UTF-8 JSON.") from exc


def source_pointer(base: Any, suffix: str) -> str | None:
    """Compose original-body paths without advertising an unusable selector."""
    if not isinstance(base, str):
        return None
    pointer = base + suffix
    if (
        (pointer and not pointer.startswith("/"))
        or len(pointer) > 4096
        or pointer.count("/") > 128
        or re.search(r"~(?![01])", pointer)
    ):
        return None
    return pointer


class SourcePresenter:
    def __init__(self, store: ContentStore) -> None:
        self.store = store
        self.cursors = CursorCodec(clock=store.now)

    def _retain(self, response: SourceResponse) -> str:
        raw = _json(
            {
                "version": 1,
                "value": response.value,
                "source": asdict(response.source),
                "details": response.details,
            }
        )
        provenance = SourceInfo(
            "ClinPGx Link retained adapter result",
            "clinpgx://retained-adapter-result",
            response.source.retrieved_at,
            hashlib.sha256(raw).hexdigest(),
            "derived",
            coverage="derived_not_original",
        )
        return self.store.put(raw, provenance, "application/json")

    def resume(
        self, cursor: str, selectors: dict[str, Any], *, offset: int = 0
    ) -> tuple[SourceResponse, int, str]:
        position = self.cursors.decode(cursor, selectors, offset=offset)
        retained = self.store.get(position.identity)
        state = json.loads(retained.raw)
        if state["version"] != 1:
            raise DataValidationError("Unsupported retained adapter state.")
        source_fields = state["source"]
        source_fields["warnings"] = tuple(source_fields["warnings"])
        response = SourceResponse(state["value"], SourceInfo(**source_fields), state["details"])
        original = self.store.get(response.details["content_ref"])
        if original.source.sha256 != response.source.sha256:
            raise DataValidationError("Original source identity changed.")
        return response, position.offset, position.identity

    def _row(
        self,
        value: Any,
        response: SourceResponse,
        selectors: dict[str, Any],
        index: int | None,
        response_mode: ResponseMode,
        profile: str | None,
        *,
        force_defer: bool = False,
    ) -> dict[str, Any]:
        # Import lazily because adapter selection reuses source_pointer from this module.
        from clinpgx_link.identity_contracts import detail_identity_metadata
        from clinpgx_link.mcp.adapter_selection import (
            adapter_profile_status,
            adapter_projection_is_unprofiled,
            project_adapter_value,
        )

        profile_status = adapter_profile_status(value, profile)
        projected = project_adapter_value(value, profile, response_mode)
        unprofiled = adapter_projection_is_unprofiled(projected)
        raw = _json(value if unprofiled else projected)
        reference = response.details["content_ref"]
        pointer = source_pointer(
            response.details.get("source_pointer"), f"/{index}" if index is not None else ""
        )
        row: dict[str, Any] = {
            "content_ref": reference,
            "source_pointer": (
                fence_text(pointer, source=response.source, record_id=reference)
                if pointer
                else pointer
            ),
            "row_index": index,
            "representation": "adapter_value_json",
            "derived": True,
            "response_mode": response_mode,
        }
        if isinstance(value, dict):
            record_id = value.get("id", value.get("record_id"))
            if not record_id and isinstance(value.get("guideline"), dict):
                record_id = value["guideline"].get("id")
            elif not record_id and isinstance(value.get("cpicGuideline"), dict):
                record_id = value["cpicGuideline"].get("id")
            if isinstance(record_id, str) and re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", record_id):
                row["id"] = record_id
        if profile_status == "active":
            row.update(detail_identity_metadata(value, profile, selectors))
        license_info = response.details.get("license", {})
        if isinstance(license_info, dict) and license_info.get("spdx") in {
            "CC-BY-SA-4.0",
            "CC0-1.0",
        }:
            row["source_details"] = {"license": {"spdx": license_info["spdx"]}}
        if profile_status == "unprofiled":
            row["record_profile_status"] = "unprofiled"
        if unprofiled:
            row.update(
                deferred_content=True,
                recovery_action="read_original_source_structure",
                fallback_tool="get_source_content",
                fallback_args={
                    "content_ref": reference,
                    "representation": "structure" if pointer is not None else "base64",
                    "pointer": pointer if pointer is not None else "",
                },
            )
        elif not force_defer and len(raw) <= 12000:
            row["data"] = fence_text(raw.decode(), source=response.source, record_id=reference)
        else:
            row.update(
                deferred_content=True,
                recovery_action="read_original_source_structure",
                fallback_tool="get_source_content",
                fallback_args={
                    "content_ref": reference,
                    "representation": "structure" if pointer is not None else "base64",
                    "pointer": pointer if pointer is not None else "",
                },
            )
        if profile_status == "active":
            row["source_profile"] = profile
        return row

    def present(
        self,
        response: SourceResponse,
        *,
        selectors: dict[str, Any],
        limit: int = 20,
        offset: int = 0,
        state_ref: str | None = None,
        response_mode: ResponseMode = "compact",
        profile: str | None = None,
        next_commands: list[dict[str, Any]] | None = None,
    ) -> ToolResult:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise InvalidInputError("Invalid source page bounds.")
        if not isinstance(response.value, list):
            if offset:
                raise InvalidInputError("A scalar result has no row continuation.", field="offset")
            return success_result(
                self._bounded_row(
                    response.value, response, selectors, None, response_mode, profile
                ),
                source=response.source,
                content_ref=response.details["content_ref"],
                next_commands=next_commands,
            )
        total = len(response.value)
        if offset > total:
            raise InvalidInputError("Offset exceeds returned source set.", field="offset")
        rows: list[dict[str, Any]] = []
        fence_count = len(response.source.warnings)
        for index in range(offset, min(offset + limit, total)):
            row = self._bounded_row(
                response.value[index], response, selectors, index, response_mode, profile
            )
            row_fences = int("data" in row) + int(isinstance(row["source_pointer"], dict))
            if rows and (len(_json([*rows, row])) > 70000 or fence_count + row_fences > 120):
                break
            rows.append(row)
            fence_count += row_fences
        while True:
            stop = offset + len(rows)
            next_cursor = None
            if stop < total:
                state_ref = state_ref or self._retain(response)
                deadline = min(
                    self.store.get(response.details["content_ref"]).expires_at,
                    self.store.get(state_ref).expires_at,
                )
                next_cursor = self.cursors.encode(
                    selectors,
                    identity=state_ref,
                    offset=stop,
                    expires_at=deadline,
                )
            try:
                return success_result(
                    rows,
                    source=response.source,
                    collection=True,
                    content_ref=response.details["content_ref"],
                    pagination={
                        "offset": offset,
                        "returned": len(rows),
                        "total": total,
                        "total_scope": "retained_response",
                        "upstream_completeness": "unknown",
                        "has_more": next_cursor is not None,
                        "next_cursor": next_cursor,
                    },
                    next_commands=next_commands,
                )
            except ResponseTooLargeError:
                if len(rows) > 1:
                    rows.pop()
                    continue
                if rows and "data" in rows[0]:
                    rows[0] = self._bounded_row(
                        response.value[offset],
                        response,
                        selectors,
                        offset,
                        response_mode,
                        profile,
                        force_defer=True,
                    )
                    continue
                raise

    def _bounded_row(
        self,
        value: Any,
        response: SourceResponse,
        selectors: dict[str, Any],
        index: int | None,
        response_mode: ResponseMode,
        profile: str | None,
        *,
        force_defer: bool = False,
    ) -> dict[str, Any]:
        row = self._row(
            value,
            response,
            selectors,
            index,
            response_mode,
            profile,
            force_defer=force_defer,
        )
        if len(_json(row)) <= 70_000:
            return row
        deferred = self._row(
            value,
            response,
            selectors,
            index,
            response_mode,
            profile,
            force_defer=True,
        )
        if len(_json(deferred)) > 70_000:
            raise ResponseTooLargeError("The source row exceeds its bounded descriptor size.")
        return deferred
