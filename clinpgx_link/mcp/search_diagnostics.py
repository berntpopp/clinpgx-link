"""MCP presentation for code-owned search diagnostics."""

from __future__ import annotations

from typing import Any

from fastmcp.tools.base import ToolResult

from clinpgx_link.exceptions import ClinPGxError, DatasetFilterError
from clinpgx_link.mcp.envelope import error_result
from clinpgx_link.mcp.recovery import (
    RecoveryPlan,
    dataset_cursor_plan,
    dataset_filter_plan,
    dataset_query_plan,
)
from clinpgx_link.mcp.untrusted_content import fence_text
from clinpgx_link.models import SourceInfo, SourceResponse


def fenced_search_diagnostics(diagnostics: object, *, source: SourceInfo) -> dict[str, Any] | None:
    """Fence only source-derived examples; diagnostic keys and prose are code-owned."""
    if not isinstance(diagnostics, dict):
        return None
    status = diagnostics.get("status")
    limitation = diagnostics.get("limitation")
    raw_filters = diagnostics.get("filters")
    if status not in {"available", "diagnostics_unavailable"} or not isinstance(limitation, str):
        return None
    if not isinstance(raw_filters, list):
        return None
    filters: list[dict[str, Any]] = []
    for item in raw_filters:
        if not isinstance(item, dict):
            return None
        key = item.get("filter")
        item_status = item.get("status")
        if not isinstance(key, str) or item_status not in {
            "available",
            "diagnostics_unavailable",
        }:
            return None
        shaped: dict[str, Any] = {"filter": key, "status": item_status}
        if item_status == "available":
            count = item.get("count_without_filter")
            examples = item.get("examples")
            if not isinstance(count, int) or count < 0 or not isinstance(examples, list):
                return None
            if len(examples) > 3 or not all(isinstance(value, str) for value in examples):
                return None
            shaped["count_without_filter"] = count
            shaped["examples"] = [
                fence_text(value, source=source, record_id=source.sha256) for value in examples
            ]
        filters.append(shaped)
    return {"status": status, "limitation": limitation, "filters": filters}


def presented_search_diagnostics(response: SourceResponse) -> dict[str, Any] | None:
    return fenced_search_diagnostics(
        response.details.get("search_diagnostics"), source=response.source
    )


def _recovery(error: ClinPGxError) -> RecoveryPlan | None:
    if type(error) is DatasetFilterError:
        return dataset_filter_plan(
            getattr(error, "dataset_id", None),
            getattr(error, "known_filters", None),
        )
    if error.subtype == "wildcard_query_unsupported":
        return dataset_query_plan()
    if error.field == "cursor" or error.subtype == "cursor_with_offset":
        return dataset_cursor_plan()
    return None


def dataset_search_error_result(error: ClinPGxError) -> ToolResult:
    return error_result(
        error,
        content_ref=getattr(error, "content_ref", None),
        recovery=_recovery(error),
        recovery_pointer=getattr(error, "recovery_pointer", None),
    )


__all__ = [
    "dataset_search_error_result",
    "fenced_search_diagnostics",
    "presented_search_diagnostics",
]
