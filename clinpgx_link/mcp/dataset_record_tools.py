"""MCP tools for indexed rows in the immutable ClinPGx snapshot."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from pydantic import Field

from clinpgx_link.content.assets import AssetReference
from clinpgx_link.content.reader import select_value
from clinpgx_link.content.store import ContentStore
from clinpgx_link.data.repository import DatasetRepository
from clinpgx_link.exceptions import (
    ClinPGxError,
    InvalidInputError,
    ResponseTooLargeError,
    UpstreamUnavailableError,
)
from clinpgx_link.mcp.dataset_record_fields import (
    profiled_nested_fields_are_safe,
    trusted_fields_for_row,
)
from clinpgx_link.mcp.envelope import error_result, success_result
from clinpgx_link.mcp.pagination import CursorCodec
from clinpgx_link.mcp.untrusted_content import fence_text
from clinpgx_link.models import SourceInfo, SourceResponse

ResponseMode = Literal["minimal", "compact", "standard", "full"]
MatchMode = Literal["exact", "member"]
_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_MAX_INLINE_FIELD_BYTES = 12_000
_MAX_POINTER_CHARACTERS = 4096
_MAX_POINTER_SEGMENTS = 128


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _derived_source(source: SourceInfo, record_id: str, raw: bytes) -> SourceInfo:
    return SourceInfo(
        "ClinPGx Link derived dataset record",
        "clinpgx://dataset-record/" + record_id,
        source.retrieved_at,
        hashlib.sha256(raw).hexdigest(),
        "derived",
        published_at=source.published_at,
        release_tag=source.release_tag,
        coverage="derived_not_original",
        warnings=source.warnings,
    )


def _retain_row(store: ContentStore, row: dict[str, Any], source: SourceInfo) -> str:
    raw = _json(row)
    return store.put(raw, _derived_source(source, str(row["record_id"]), raw), "application/json")


def _has_oversized_string(value: Any) -> bool:
    if isinstance(value, str):
        return len(value.encode("utf-8")) > _MAX_INLINE_FIELD_BYTES
    if isinstance(value, dict):
        return any(_has_oversized_string(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_oversized_string(item) for item in value)
    return False


def _pointer_is_safe(pointer: str) -> bool:
    return (
        pointer.startswith("/")
        and len(pointer) <= _MAX_POINTER_CHARACTERS
        and pointer.count("/") <= _MAX_POINTER_SEGMENTS
        and re.search(r"~(?![01])", pointer) is None
    )


def _has_unsafe_field_key(
    value: Any, *, trusted_field_names: frozenset[str] | None = None, depth: int = 0
) -> bool:
    if isinstance(value, dict):
        if depth >= _MAX_POINTER_SEGMENTS:
            return True
        for key, item in value.items():
            key_text = str(key)
            if (
                depth == 0
                and trusted_field_names is not None
                and key_text not in trusted_field_names
            ):
                return True
            if (
                not key_text
                or len(key_text) > 512
                or any(ord(char) < 0x20 or ord(char) == 0x7F for char in key_text)
                or "/" in key_text
                or "~" in key_text
            ):
                return True
            if _has_unsafe_field_key(
                item, trusted_field_names=trusted_field_names, depth=depth + 1
            ):
                return True
    elif isinstance(value, list):
        if depth >= _MAX_POINTER_SEGMENTS:
            return True
        return any(_has_unsafe_field_key(item, depth=depth + 1) for item in value)
    return False


def _has_overlong_field_pointer(value: Any, pointer: str = "/fields") -> bool:
    if not _pointer_is_safe(pointer):
        return True
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{pointer}/{_escape_pointer_token(str(key))}"
            if _has_overlong_field_pointer(item, child):
                return True
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if _has_overlong_field_pointer(item, f"{pointer}/{index}"):
                return True
    return False


def _needs_deferred_fields(
    value: Any, *, trusted_field_names: frozenset[str] | None = None
) -> bool:
    return _has_unsafe_field_key(value, trusted_field_names=trusted_field_names)


def _escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _deferred_field(
    value: str, pointer: str, derived_ref: str, source: SourceInfo, record_id: str
) -> dict[str, Any]:
    if not _pointer_is_safe(pointer):
        return _fields_descriptor(derived_ref, pointer="", representation="base64")
    return {
        "deferred_content": True,
        "content_ref": derived_ref,
        "pointer": pointer,
        "representation": "normalized_record_json",
        "derived": True,
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "length": len(value),
        "unit": "characters",
        "fallback_tool": "get_source_content",
        "fallback_args": {
            "content_ref": derived_ref,
            "pointer": pointer,
            "representation": "text",
        },
        "provenance": {
            "source": source.source,
            "record_id": record_id,
            "retrieved_at": source.retrieved_at,
        },
    }


def _fields_descriptor(
    derived_ref: str, *, pointer: str = "/fields", representation: str = "structure"
) -> dict[str, Any]:
    return {
        "deferred_content": True,
        "content_ref": derived_ref,
        "pointer": pointer,
        "representation": "normalized_record_json",
        "derived": True,
        "recovery_representation": representation,
        "fallback_tool": "get_source_content",
        "fallback_args": {
            "content_ref": derived_ref,
            "pointer": pointer,
            "representation": representation,
        },
    }


def _shape_fields(
    value: Any,
    *,
    pointer: str,
    derived_ref: str | None,
    source: SourceInfo,
    record_id: str,
) -> Any:
    if isinstance(value, dict):
        return {
            key: _shape_fields(
                item,
                pointer=f"{pointer}/{_escape_pointer_token(str(key))}",
                derived_ref=derived_ref,
                source=source,
                record_id=record_id,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _shape_fields(
                item,
                pointer=f"{pointer}/{index}",
                derived_ref=derived_ref,
                source=source,
                record_id=record_id,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_INLINE_FIELD_BYTES and derived_ref is not None:
            return _deferred_field(value, pointer, derived_ref, source, record_id)
        return fence_text(value, source=source, record_id=record_id)
    return value


def _asset_reference(response: SourceResponse, snapshot_id: str) -> str:
    asset = response.details.get("asset")
    if not isinstance(asset, dict):
        raise UpstreamUnavailableError(
            "The indexed row has no retained source member.", subtype="asset_missing"
        )
    try:
        return AssetReference(
            snapshot_id,
            str(asset["dataset_id"]),
            str(asset["member"]),
            str(asset["sha256"]),
        ).encode()
    except (KeyError, TypeError, InvalidInputError) as exc:
        raise UpstreamUnavailableError(
            "The indexed row source member is invalid.", subtype="asset_invalid"
        ) from exc


def _shape_row(
    row: dict[str, Any],
    response: SourceResponse,
    snapshot_id: str,
    store: ContentStore,
    *,
    asset_response: SourceResponse | None = None,
    trusted_field_names: frozenset[str] | None = None,
    force_defer_fields: bool = False,
) -> dict[str, Any]:
    source_ref = _asset_reference(asset_response or response, snapshot_id)
    fields = row.get("fields", {})
    owned_names = trusted_fields_for_row(row)
    trusted_names = (
        owned_names
        if trusted_field_names is None
        else frozenset(trusted_field_names).intersection(owned_names)
    )
    defer_fields = (
        force_defer_fields
        or not profiled_nested_fields_are_safe(row)
        or _needs_deferred_fields(fields, trusted_field_names=trusted_names)
    )
    derived_ref = (
        _retain_row(store, row, response.source)
        if defer_fields or _has_oversized_string(row)
        else None
    )
    result = dict(row)
    result["content_ref"] = source_ref
    record_id = str(row["record_id"])
    result["member"] = fence_text(str(row["member"]), source=response.source, record_id=record_id)
    for pointer_key in ("json_pointer", "parent_pointer"):
        if pointer_key in result and result[pointer_key] is not None:
            result[pointer_key] = fence_text(
                str(result[pointer_key]), source=response.source, record_id=record_id
            )
    if defer_fields:
        root_ref = derived_ref or _retain_row(store, row, response.source)
        result["fields"] = _fields_descriptor(
            root_ref,
            pointer="" if _has_overlong_field_pointer(fields) else "/fields",
            representation="base64" if _has_overlong_field_pointer(fields) else "structure",
        )
    else:
        result["fields"] = _shape_fields(
            fields,
            pointer="/fields",
            derived_ref=derived_ref,
            source=response.source,
            record_id=record_id,
        )
        if isinstance(fields, dict):
            result["field_names"] = [
                fence_text(str(name), source=response.source, record_id=record_id)
                for name in fields
            ]
    return result


def shape_dataset_row(
    row: dict[str, Any],
    response: SourceResponse,
    snapshot_id: str,
    store: ContentStore,
    *,
    asset_response: SourceResponse | None = None,
    trusted_field_names: frozenset[str] | None = None,
    force_defer_fields: bool = False,
) -> dict[str, Any]:
    """Shape one indexed row for MCP consumers with explicit recovery metadata."""
    return _shape_row(
        row,
        response,
        snapshot_id,
        store,
        asset_response=asset_response,
        trusted_field_names=trusted_field_names,
        force_defer_fields=force_defer_fields,
    )


def _selected_value(
    row: dict[str, Any], pointer: str, response: SourceResponse, store: ContentStore
) -> dict[str, Any]:
    selected = select_value(row, pointer)
    raw = _json(selected)
    derived_ref = _retain_row(store, row, response.source)
    if len(raw) > _MAX_INLINE_FIELD_BYTES:
        return {
            "deferred_content": True,
            "content_ref": derived_ref,
            "pointer": pointer,
            "representation": "normalized_record_json",
            "derived": True,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "length": len(raw),
            "unit": "bytes",
            "fallback_tool": "get_source_content",
            "fallback_args": {
                "content_ref": derived_ref,
                "pointer": pointer,
                "representation": "structure",
            },
        }
    return {
        "pointer": pointer,
        "representation": "normalized_record_json",
        "derived": True,
        "content_ref": derived_ref,
        "data": fence_text(
            raw.decode("utf-8"), source=response.source, record_id=str(row["record_id"])
        ),
    }


def select_dataset_record_value(
    row: dict[str, Any], pointer: str, response: SourceResponse, store: ContentStore
) -> dict[str, Any]:
    """Select a validated normalized-record pointer with a derived retained ref."""
    return _selected_value(row, pointer, response, store)


def register_dataset_record_tools(
    server: FastMCP, repository: DatasetRepository | None, store: ContentStore
) -> None:
    """Register indexed-row tools, retaining registration when local data is absent."""
    cursors = CursorCodec(clock=store.now)

    @server.tool(annotations=_ANNOTATIONS, tags={"dataset", "search"}, output_schema=None)
    async def search_dataset(
        dataset_id: Annotated[
            str,
            Field(
                description="Exact installed dataset identifier.",
                min_length=1,
                max_length=512,
                examples=["data/genes.zip"],
            ),
        ],
        member: Annotated[
            str | None, Field(description="Exact installed member path.", max_length=4096)
        ] = None,
        query: Annotated[
            str | None, Field(description="Literal token query.", max_length=512)
        ] = None,
        filters: Annotated[
            dict[str, str] | None,
            Field(
                description="ANDed filters: reserved lowercase canonical keys, or exact advertised source fields when member is explicit."
                " For PharmCAT diplotype children, combine exact canonical gene and name"
                " (the diplotype string) filters.",
            ),
        ] = None,
        match: Annotated[
            MatchMode, Field(description="Exact cells or declared member tokenization.")
        ] = "exact",
        limit: Annotated[int, Field(description="Maximum rows to return.", ge=1, le=100)] = 20,
        offset: Annotated[int, Field(description="Zero-based row offset.", ge=0)] = 0,
        cursor: Annotated[
            str | None, Field(description="Snapshot-bound continuation cursor.", max_length=2048)
        ] = None,
        response_mode: Annotated[
            ResponseMode, Field(description="Response detail mode.")
        ] = "compact",
    ) -> ToolResult:
        began = time.monotonic()
        try:
            if repository is None:
                raise UpstreamUnavailableError(
                    "Local dataset snapshot is not configured.", subtype="dataset_unavailable"
                )
            if type(limit) is not int or not 1 <= limit <= 100:
                raise InvalidInputError("Limit must be between 1 and 100.", field="limit")
            if type(offset) is not int or offset < 0:
                raise InvalidInputError("Offset must be non-negative.", field="offset")
            if cursor is not None and offset:
                raise InvalidInputError("Cursor and offset cannot be combined.", field="offset")
            snapshot_id = str((await asyncio.to_thread(repository.status))["snapshot_id"])
            selected_filters = filters or {}
            selectors = {
                "tool": "search_dataset",
                "dataset_id": dataset_id,
                "member": member,
                "query": query,
                "filters": selected_filters,
                "match": match,
            }
            if cursor is not None:
                position = cursors.decode(cursor, selectors)
                if position.identity != snapshot_id:
                    raise UpstreamUnavailableError(
                        "The cursor belongs to a different local snapshot.",
                        subtype="snapshot_mismatch",
                    )
                offset = position.offset
            response = await asyncio.to_thread(
                repository.search,
                dataset_id,
                member=member,
                query=query,
                filters=selected_filters,
                limit=limit,
                offset=offset,
                match=match,
                expected_snapshot=snapshot_id,
            )
            if response.details.get("snapshot_id") != snapshot_id:
                raise UpstreamUnavailableError(
                    "The local snapshot identity changed.", subtype="snapshot_mismatch"
                )
            asset_responses = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        repository.get_record, row["record_id"], expected_snapshot=snapshot_id
                    )
                    for row in response.value
                )
            )
            row_inputs = list(zip(response.value, asset_responses, strict=True))
            visible_count = len(row_inputs)
            force_defer_fields = False
            total = int(response.details["total_count"])
            while True:
                visible_inputs = row_inputs[:visible_count]
                rows = [
                    shape_dataset_row(
                        row,
                        response,
                        snapshot_id,
                        store,
                        asset_response=asset_response,
                        trusted_field_names=trusted_fields_for_row(row),
                        force_defer_fields=force_defer_fields,
                    )
                    for row, asset_response in visible_inputs
                ]
                next_offset = offset + len(rows)
                next_cursor = (
                    cursors.encode(selectors, identity=snapshot_id, offset=next_offset)
                    if next_offset < total
                    else None
                )
                try:
                    return success_result(
                        rows,
                        source=response.source,
                        collection=True,
                        pagination={
                            "offset": offset,
                            "returned": len(rows),
                            "total_count": total,
                            "has_more": next_cursor is not None,
                            "next_cursor": next_cursor,
                            "snapshot_id": snapshot_id,
                        },
                        elapsed_ms=(time.monotonic() - began) * 1000,
                    )
                except ResponseTooLargeError:
                    if visible_count > 1:
                        visible_count = max(1, visible_count // 2)
                    elif not force_defer_fields:
                        force_defer_fields = True
                    else:
                        raise
        except ClinPGxError as exc:
            return error_result(exc)
        except Exception:
            return error_result(ClinPGxError("Dataset search failed."))

    @server.tool(annotations=_ANNOTATIONS, tags={"dataset", "record"}, output_schema=None)
    async def get_dataset_record(
        record_id: Annotated[
            str,
            Field(
                description="Snapshot-bound record identity discovered with search_dataset.",
                min_length=1,
                max_length=512,
                examples=["record:" + "0" * 64],
            ),
        ],
        pointer: Annotated[
            str, Field(description="RFC 6901 pointer in the normalized record.", max_length=4096)
        ] = "",
        response_mode: Annotated[
            ResponseMode, Field(description="Response detail mode.")
        ] = "compact",
    ) -> ToolResult:
        began = time.monotonic()
        try:
            if repository is None:
                raise UpstreamUnavailableError(
                    "Local dataset snapshot is not configured.", subtype="dataset_unavailable"
                )
            snapshot_id = str((await asyncio.to_thread(repository.status))["snapshot_id"])
            response = await asyncio.to_thread(
                repository.get_record, record_id, expected_snapshot=snapshot_id
            )
            if response.details.get("snapshot_id") != snapshot_id:
                raise UpstreamUnavailableError(
                    "The local snapshot identity changed.", subtype="snapshot_mismatch"
                )
            result = shape_dataset_row(
                response.value,
                response,
                snapshot_id,
                store,
                trusted_field_names=trusted_fields_for_row(response.value),
            )
            result["snapshot_id"] = snapshot_id
            result["response_mode"] = response_mode
            if pointer:
                result["selected"] = _selected_value(response.value, pointer, response, store)
            try:
                return success_result(
                    result,
                    source=response.source,
                    snapshot_id=snapshot_id,
                    elapsed_ms=(time.monotonic() - began) * 1000,
                )
            except ResponseTooLargeError:
                result = shape_dataset_row(
                    response.value,
                    response,
                    snapshot_id,
                    store,
                    trusted_field_names=trusted_fields_for_row(response.value),
                    force_defer_fields=True,
                )
                result["snapshot_id"] = snapshot_id
                result["response_mode"] = response_mode
                if pointer:
                    result["selected"] = select_dataset_record_value(
                        response.value, pointer, response, store
                    )
                return success_result(
                    result,
                    source=response.source,
                    snapshot_id=snapshot_id,
                    elapsed_ms=(time.monotonic() - began) * 1000,
                )
        except ClinPGxError as exc:
            return error_result(exc)
        except Exception:
            return error_result(ClinPGxError("Dataset record retrieval failed."))


__all__ = [
    "register_dataset_record_tools",
    "select_dataset_record_value",
    "shape_dataset_row",
]
