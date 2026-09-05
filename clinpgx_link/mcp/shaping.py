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
from typing import Any

from fastmcp.tools.base import ToolResult

from clinpgx_link.content.store import ContentStore
from clinpgx_link.exceptions import DataValidationError, InvalidInputError
from clinpgx_link.mcp.envelope import success_result
from clinpgx_link.mcp.pagination import CursorCodec
from clinpgx_link.mcp.untrusted_content import fence_text
from clinpgx_link.models import SourceInfo, SourceResponse


def _json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
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

    def _row(self, value: Any, response: SourceResponse, index: int | None) -> dict[str, Any]:
        raw = _json(value)
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
        }
        if isinstance(value, dict):
            record_id = value.get("id", value.get("record_id"))
            if isinstance(record_id, str) and re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", record_id):
                row["id"] = record_id
        license_info = response.details.get("license", {})
        if isinstance(license_info, dict) and license_info.get("spdx") in {
            "CC-BY-SA-4.0",
            "CC0-1.0",
        }:
            row["source_details"] = {"license": {"spdx": license_info["spdx"]}}
        if len(raw) <= 12000:
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
        return row

    def present(
        self,
        response: SourceResponse,
        *,
        selectors: dict[str, Any],
        limit: int = 20,
        offset: int = 0,
        state_ref: str | None = None,
    ) -> ToolResult:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise InvalidInputError("Invalid source page bounds.")
        if not isinstance(response.value, list):
            if offset:
                raise InvalidInputError("A scalar result has no row continuation.", field="offset")
            return success_result(
                self._row(response.value, response, None),
                source=response.source,
                content_ref=response.details["content_ref"],
            )
        total = len(response.value)
        if offset > total:
            raise InvalidInputError("Offset exceeds returned source set.", field="offset")
        rows: list[dict[str, Any]] = []
        fence_count = len(response.source.warnings)
        for index in range(offset, min(offset + limit, total)):
            row = self._row(response.value[index], response, index)
            row_fences = int("data" in row) + int(isinstance(row["source_pointer"], dict))
            if rows and (len(_json([*rows, row])) > 70000 or fence_count + row_fences > 120):
                break
            rows.append(row)
            fence_count += row_fences
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
        )
