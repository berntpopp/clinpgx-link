"""Shared local entity-row presentation with envelope-driven overflow recovery."""

from __future__ import annotations

import time
from typing import Any

from fastmcp.tools.base import ToolResult

from clinpgx_link.content.store import ContentStore
from clinpgx_link.data.repository import DatasetRepository
from clinpgx_link.exceptions import ResponseTooLargeError, UpstreamUnavailableError
from clinpgx_link.mcp.dataset_record_selection import (
    ResponseMode,
    pointer_selected_row,
    profiled_row,
)
from clinpgx_link.mcp.dataset_record_tools import (
    select_dataset_record_value,
    shape_dataset_row,
)
from clinpgx_link.mcp.envelope import success_result
from clinpgx_link.mcp.pagination import CursorCodec
from clinpgx_link.models import SourceResponse


def snapshot_id(repository: DatasetRepository | None) -> str:
    """Return the identity of the already-open immutable local handle."""
    if repository is None:
        raise UpstreamUnavailableError(
            "Local dataset snapshot is not configured.", subtype="dataset_unavailable"
        )
    return str(repository.status()["snapshot_id"])


def local_collection_result(
    response: SourceResponse,
    *,
    snapshot: str,
    repository: DatasetRepository,
    store: ContentStore,
    selectors: dict[str, Any],
    cursors: CursorCodec,
    offset: int,
    began: float,
    response_mode: ResponseMode,
) -> ToolResult:
    """Shape a local page and retry the actual envelope with recoverable fields."""
    inputs = [
        (
            row,
            repository.get_record(row["record_id"], expected_snapshot=snapshot),
        )
        for row in response.value
    ]
    force_defer = False
    total = int(response.details["total_count"])
    while True:
        rows = [
            profiled_row(
                row,
                response,
                snapshot,
                store,
                repository,
                response_mode,
                None,
                shape_dataset_row,
                asset_response=asset,
                force_defer_fields=force_defer,
            )
            for row, asset in inputs
        ]
        stop = offset + len(rows)
        next_cursor = (
            cursors.encode(selectors, identity=snapshot, offset=stop) if stop < total else None
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
                    "snapshot_id": snapshot,
                },
                elapsed_ms=(time.monotonic() - began) * 1000,
            )
        except ResponseTooLargeError:
            if len(inputs) > 1:
                inputs = inputs[: max(1, len(inputs) // 2)]
            elif not force_defer:
                force_defer = True
            else:
                raise


def local_singleton_result(
    response: SourceResponse,
    *,
    snapshot: str,
    store: ContentStore,
    pointer: str,
    response_mode: ResponseMode,
    began: float,
    repository: DatasetRepository,
    pointers: tuple[str, ...] | None,
) -> ToolResult:
    """Present one local row, retaining pointer selection across overflow fallback."""
    force_defer = False
    while True:
        if pointers is None:
            result = profiled_row(
                response.value,
                response,
                snapshot,
                store,
                repository,
                response_mode,
                None,
                shape_dataset_row,
                force_defer_fields=force_defer,
            )
        else:
            result = pointer_selected_row(
                response.value,
                response,
                snapshot,
                store,
                pointers,
                shape_dataset_row,
            )
        result["snapshot_id"] = snapshot
        result["response_mode"] = response_mode
        if pointer:
            result["selected"] = select_dataset_record_value(
                response.value, pointer, response, store
            )
        try:
            return success_result(
                result,
                source=response.source,
                snapshot_id=snapshot,
                elapsed_ms=(time.monotonic() - began) * 1000,
            )
        except ResponseTooLargeError:
            if force_defer:
                raise
            force_defer = True


__all__ = ["local_collection_result", "local_singleton_result", "snapshot_id"]
