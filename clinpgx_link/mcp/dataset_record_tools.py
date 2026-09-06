"""MCP tools for indexed rows in the immutable ClinPGx snapshot."""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from pydantic import Field

from clinpgx_link.content.store import ContentStore
from clinpgx_link.data.repository import DatasetRepository
from clinpgx_link.exceptions import (
    ClinPGxError,
    InvalidInputError,
    ResponseTooLargeError,
    UpstreamUnavailableError,
)
from clinpgx_link.mcp.admission import run_sync
from clinpgx_link.mcp.dataset_record_selection import (
    ResponseMode,
    pointer_selected_row,
    profiled_row,
    select_dataset_record_value,
    validate_include_fields,
)
from clinpgx_link.mcp.dataset_row_shaping import _asset_reference, _json, shape_dataset_row
from clinpgx_link.mcp.envelope import error_result, success_result
from clinpgx_link.mcp.pagination import CursorCodec
from clinpgx_link.mcp.parent_context import (
    render_parent_context,
    validate_parent_fields,
    validate_parent_pointer_compatibility,
)
from clinpgx_link.mcp.search_diagnostics import (
    dataset_search_error_result,
    presented_search_diagnostics,
)
from clinpgx_link.mcp.selection import validate_pointers

MatchMode = Literal["exact", "member"]
_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def register_dataset_record_tools(
    server: FastMCP, repository: DatasetRepository | None, store: ContentStore
) -> None:
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
        include_fields: Annotated[
            list[str] | None,
            Field(description="Ordered profiled source fields to return.", max_length=16),
        ] = None,
        parent_fields: Annotated[
            list[Literal["gene", "version"]] | None,
            Field(
                description="Ordered enclosing PharmCAT parent fields: gene and/or version.",
                min_length=1,
                max_length=2,
            ),
        ] = None,
    ) -> ToolResult:
        """Search indexed rows within an installed snapshot dataset. Rows include member_sha256."""
        began = time.monotonic()
        try:
            selected_field_names = validate_include_fields(include_fields)
            selected_parent_names = validate_parent_fields(parent_fields)
            if repository is None:
                raise UpstreamUnavailableError(
                    "Local dataset snapshot is not configured.", subtype="dataset_unavailable"
                )
            if type(limit) is not int or not 1 <= limit <= 100:
                raise InvalidInputError("Limit must be between 1 and 100.", field="limit")
            if type(offset) is not int or offset < 0:
                raise InvalidInputError("Offset must be non-negative.", field="offset")
            snapshot_id = str((await run_sync(repository.status))["snapshot_id"])
            selected_filters = filters or {}
            selectors = {
                "tool": "search_dataset",
                "dataset_id": dataset_id,
                "member": member,
                "query": query,
                "filters": selected_filters,
                "match": match,
                "include_fields": list(selected_field_names) if selected_field_names else None,
            }
            if selected_parent_names is not None:
                selectors["parent_fields"] = list(selected_parent_names)
            if cursor is not None:
                position = cursors.decode(cursor, selectors, offset=offset)
                if position.identity != snapshot_id:
                    raise UpstreamUnavailableError(
                        "The cursor belongs to a different local snapshot.",
                        subtype="snapshot_mismatch",
                    )
                offset = position.offset
            response = await run_sync(
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
                    run_sync(repository.get_record, row["record_id"], expected_snapshot=snapshot_id)
                    for row in response.value
                )
            )
            parent_contexts: list[dict[str, Any] | None] = [None] * len(response.value)
            if selected_parent_names is not None and response.value:
                parent_response = await run_sync(
                    repository.parent_contexts,
                    response.value,
                    selected_parent_names,
                    expected_snapshot=snapshot_id,
                )
                if parent_response.details.get("snapshot_id") != snapshot_id:
                    raise UpstreamUnavailableError(
                        "The local snapshot identity changed.", subtype="snapshot_mismatch"
                    )
                parent_contexts = list(parent_response.value)
            row_inputs = list(zip(response.value, asset_responses, parent_contexts, strict=True))
            visible_count = len(row_inputs)
            force_defer_fields = False
            total = int(response.details["total_count"])
            search_diagnostics = presented_search_diagnostics(response)
            while True:
                visible_inputs = row_inputs[:visible_count]
                rows: list[dict[str, Any]] = []
                for raw_row, asset_response, parent_context in visible_inputs:
                    shaped = await run_sync(
                        profiled_row,
                        raw_row,
                        response,
                        snapshot_id,
                        store,
                        repository,
                        response_mode,
                        selected_field_names,
                        shape_dataset_row,
                        asset_response=asset_response,
                        force_defer_fields=force_defer_fields,
                    )
                    if parent_context is not None:
                        shaped["parent_context"] = render_parent_context(
                            raw_row, parent_context, asset_response, snapshot_id
                        )
                    if len(_json(shaped)) > 70_000:
                        shaped = await run_sync(
                            profiled_row,
                            raw_row,
                            response,
                            snapshot_id,
                            store,
                            repository,
                            response_mode,
                            selected_field_names,
                            shape_dataset_row,
                            asset_response=asset_response,
                            force_defer_fields=True,
                        )
                        if parent_context is not None:
                            shaped["parent_context"] = render_parent_context(
                                raw_row, parent_context, asset_response, snapshot_id
                            )
                    if len(_json(shaped)) > 70_000:
                        return error_result(
                            ResponseTooLargeError(
                                "The indexed row exceeds its bounded descriptor size."
                            ),
                            content_ref=_asset_reference(asset_response, snapshot_id),
                        )
                    rows.append(shaped)
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
                        search_diagnostics=search_diagnostics,
                    )
                except ResponseTooLargeError:
                    if visible_count > 1:
                        visible_count = max(1, visible_count // 2)
                    elif not force_defer_fields:
                        force_defer_fields = True
                    else:
                        raise
        except ClinPGxError as exc:
            return dataset_search_error_result(exc)
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
        pointers: Annotated[
            list[str] | None,
            Field(description="Ordered scalar RFC 6901 pointers.", max_length=12),
        ] = None,
        response_mode: Annotated[
            ResponseMode, Field(description="Response detail mode.")
        ] = "compact",
        include_fields: Annotated[
            list[str] | None,
            Field(description="Ordered profiled source fields to return.", max_length=16),
        ] = None,
        parent_fields: Annotated[
            list[Literal["gene", "version"]] | None,
            Field(
                description="Ordered enclosing PharmCAT parent fields: gene and/or version.",
                min_length=1,
                max_length=2,
            ),
        ] = None,
    ) -> ToolResult:
        """Get one indexed dataset row by record ID with optional pointer or parent context."""
        began = time.monotonic()
        try:
            selected_field_names = validate_include_fields(include_fields)
            selected_parent_names = validate_parent_fields(parent_fields)
            validate_parent_pointer_compatibility(
                selected_parent_names, pointer=pointer, pointers=pointers
            )
            selected_pointers = validate_pointers(
                pointers, pointer=pointer, include_fields=selected_field_names
            )
            if repository is None:
                raise UpstreamUnavailableError(
                    "Local dataset snapshot is not configured.", subtype="dataset_unavailable"
                )
            snapshot_id = str((await run_sync(repository.status))["snapshot_id"])
            response = await run_sync(
                repository.get_record, record_id, expected_snapshot=snapshot_id
            )
            if response.details.get("snapshot_id") != snapshot_id:
                raise UpstreamUnavailableError(
                    "The local snapshot identity changed.", subtype="snapshot_mismatch"
                )
            parent_context: dict[str, Any] | None = None
            if selected_parent_names is not None:
                parent_response = await run_sync(
                    repository.parent_contexts,
                    [response.value],
                    selected_parent_names,
                    expected_snapshot=snapshot_id,
                )
                if parent_response.details.get("snapshot_id") != snapshot_id:
                    raise UpstreamUnavailableError(
                        "The local snapshot identity changed.", subtype="snapshot_mismatch"
                    )
                parent_context = parent_response.value[0]
            if selected_pointers is None:
                result = await run_sync(
                    profiled_row,
                    response.value,
                    response,
                    snapshot_id,
                    store,
                    repository,
                    response_mode,
                    selected_field_names,
                    shape_dataset_row,
                )
            else:
                result = await run_sync(
                    pointer_selected_row,
                    response.value,
                    response,
                    snapshot_id,
                    store,
                    selected_pointers,
                    shape_dataset_row,
                )
            if parent_context is not None:
                result["parent_context"] = render_parent_context(
                    response.value, parent_context, response, snapshot_id
                )
            result["snapshot_id"] = snapshot_id
            result["response_mode"] = response_mode
            if pointer:
                result["selected"] = await run_sync(
                    select_dataset_record_value, response.value, pointer, response, store
                )
            try:
                return success_result(
                    result,
                    source=response.source,
                    snapshot_id=snapshot_id,
                    elapsed_ms=(time.monotonic() - began) * 1000,
                )
            except ResponseTooLargeError:
                if selected_pointers is None:
                    result = await run_sync(
                        profiled_row,
                        response.value,
                        response,
                        snapshot_id,
                        store,
                        repository,
                        response_mode,
                        selected_field_names,
                        shape_dataset_row,
                        force_defer_fields=True,
                    )
                else:
                    result = await run_sync(
                        pointer_selected_row,
                        response.value,
                        response,
                        snapshot_id,
                        store,
                        selected_pointers,
                        shape_dataset_row,
                    )
                if parent_context is not None:
                    result["parent_context"] = render_parent_context(
                        response.value, parent_context, response, snapshot_id
                    )
                result["snapshot_id"] = snapshot_id
                result["response_mode"] = response_mode
                if pointer:
                    result["selected"] = await run_sync(
                        select_dataset_record_value, response.value, pointer, response, store
                    )
                return success_result(
                    result,
                    source=response.source,
                    snapshot_id=snapshot_id,
                    elapsed_ms=(time.monotonic() - began) * 1000,
                )
        except ClinPGxError as exc:
            return error_result(
                exc,
                content_ref=getattr(exc, "content_ref", None),
                recovery_pointer=getattr(exc, "recovery_pointer", None),
            )
        except Exception:
            return error_result(ClinPGxError("Dataset record retrieval failed."))


__all__ = [
    "register_dataset_record_tools",
    "select_dataset_record_value",
    "shape_dataset_row",
]
