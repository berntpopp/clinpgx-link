"""One flat, mirrored fleet response boundary."""

from __future__ import annotations

import json
import re
import uuid
from contextvars import ContextVar
from typing import Any, cast

from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from clinpgx_link.content.assets import AssetReference
from clinpgx_link.exceptions import (
    PUBLIC_ERROR_SUBTYPES,
    SELECTION_FAILURE_REASONS,
    ClinPGxError,
    ResponseTooLargeError,
)
from clinpgx_link.mcp.recovery import RecoveryPlan, recovery_payload
from clinpgx_link.mcp.untrusted_content import UntrustedText, enforce_limits, fence_text
from clinpgx_link.models import SourceInfo

REQUEST_ID: ContextVar[str] = ContextVar("clinpgx_request_id", default="")
MAX_ENVELOPE_BYTES = 100_000
_MESSAGES = {
    "invalid_input": "The request is outside the supported input contract.",
    "not_found": "The requested record or tool is unavailable.",
    "ambiguous_query": "The request needs a more specific selector.",
    "upstream_unavailable": "The required source is currently unavailable.",
    "rate_limited": "The source or retained-content cache is at capacity.",
    "internal": "The server could not complete this request.",
}
_SELECTION_FAILURES = {
    "array_index_invalid": frozenset({"invalid_array_index"}),
    "pointer_selection_invalid": frozenset({"duplicate_pointer"}),
    "pointer_syntax_invalid": frozenset({"malformed_rfc6901_pointer", "pointer_not_string"}),
    "scalar_selection_required": frozenset({"container_selected"}),
}


def wire_result(payload: dict[str, Any], *, is_error: bool = False) -> ToolResult:
    """Match TextContent and structuredContent exactly, including execution errors."""
    pending: list[Any] = [payload]
    fences: list[UntrustedText] = []
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if item.get("kind") == "untrusted_text":
                fences.append(cast(UntrustedText, item))
            else:
                pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    enforce_limits(fences)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(serialized.encode("utf-8")) > MAX_ENVELOPE_BYTES:
        raise ResponseTooLargeError("The response exceeds its token budget.")
    return ToolResult(
        content=[TextContent(type="text", text=serialized)],
        structured_content=payload,
        is_error=is_error,
        meta={"request_id": payload["_meta"]["request_id"]},
    )


def success_result(
    value: Any,
    *,
    source: SourceInfo,
    elapsed_ms: float | None = None,
    collection: bool = False,
    pagination: dict[str, Any] | None = None,
    content_ref: str | None = None,
    snapshot_id: str | None = None,
    search_diagnostics: dict[str, Any] | None = None,
) -> ToolResult:
    """Build provenance without confusing source time with the time of a cache hit."""
    request_id = REQUEST_ID.get() or str(uuid.uuid4())
    page_snapshot = pagination.get("snapshot_id") if pagination is not None else None
    if snapshot_id is None:
        snapshot_id = page_snapshot
    if snapshot_id is not None and (
        not isinstance(snapshot_id, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot_id) is None
        or (page_snapshot is not None and page_snapshot != snapshot_id)
    ):
        raise ClinPGxError("Invalid snapshot provenance.")
    return wire_result(
        {
            "success": True,
            "results" if collection else "result": value,
            "_meta": {
                "request_id": request_id,
                "elapsed_ms": round(elapsed_ms, 3) if elapsed_ms is not None else None,
                "timing_scope": "tool_body" if elapsed_ms is not None else None,
                **(
                    {"timing_unavailable_reason": "no_boundary_timer"} if elapsed_ms is None else {}
                ),
                "source": source.source,
                "source_url": source.url,
                "data_source": source.data_source,
                "retrieved_at": source.retrieved_at,
                "retrieval_time_kind": source.retrieval_time_kind,
                "acquired_at": source.acquired_at,
                "admitted_at": source.admitted_at,
                "source_scope": source.source_scope,
                "retrieval_time_scope": source.retrieval_time_scope,
                "published_at": source.published_at,
                "release_tag": source.release_tag,
                "source_sha256": source.sha256,
                "coverage": source.coverage,
                "warnings": [
                    fence_text(warning, source=source, record_id=source.sha256)
                    for warning in source.warnings
                ],
                "unsafe_for_clinical_use": True,
                "next_commands": (
                    [
                        {
                            "tool": "get_source_content",
                            "arguments": {
                                "content_ref": content_ref,
                                "representation": "structure",
                            },
                        }
                    ]
                    if content_ref is not None
                    else []
                ),
                **({"content_ref": content_ref} if content_ref is not None else {}),
                **({"pagination": pagination} if pagination is not None else {}),
                **({"snapshot_id": snapshot_id} if snapshot_id is not None else {}),
                **(
                    {"search_diagnostics": search_diagnostics}
                    if search_diagnostics is not None
                    else {}
                ),
            },
            "recommended_citation": (
                f"ClinPGx source evidence. {source.url} "
                f"Recorded source timestamp {source.retrieved_at}."
            ),
            "unsafe_for_clinical_use": True,
        }
    )


def error_result(
    error: ClinPGxError,
    *,
    content_ref: str | None = None,
    recovery: RecoveryPlan | None = None,
    recovery_pointer: str | None = None,
) -> ToolResult:
    """Do not reflect exception text, foreign exception names or caller values."""
    code = error.error_code if error.error_code in _MESSAGES else "internal"
    result: dict[str, Any] = {
        "success": False,
        "error_code": code,
        "message": _MESSAGES[code],
        "retryable": error.retryable,
        "recovery_action": "inspect_capabilities",
        "fallback_tool": "get_server_capabilities",
        "fallback_args": {},
        "recommended_citation": "ClinPGx Link research-data retrieval service.",
        "unsafe_for_clinical_use": True,
        "_meta": {
            "request_id": REQUEST_ID.get() or str(uuid.uuid4()),
            "elapsed_ms": None,
            "timing_scope": None,
            "timing_unavailable_reason": "no_boundary_timer",
            "unsafe_for_clinical_use": True,
            "next_commands": [{"tool": "get_server_capabilities", "arguments": {}}],
        },
    }
    if error.field and re.fullmatch(r"[a-z_]{1,40}", error.field):
        result["field"] = error.field
    public_subtype = (
        error.subtype
        if isinstance(error.subtype, str) and error.subtype in PUBLIC_ERROR_SUBTYPES
        else None
    )
    if public_subtype is not None:
        result["subtype"] = public_subtype
    if (
        type(error.selection_index) is int
        and 0 <= error.selection_index <= 11
        and isinstance(error.reason, str)
        and error.reason in SELECTION_FAILURE_REASONS
        and error.reason in _SELECTION_FAILURES.get(public_subtype or "", ())
    ):
        result["selection_index"] = error.selection_index
        result["reason"] = error.reason
    guidance = {
        "admission_capacity": (
            "The server's active work capacity is full. Retry after 1 second.",
            1,
        ),
        "upstream_throttle": (
            "The upstream source throttled this request. Retry after 30 seconds.",
            30,
        ),
        "execution_deadline": (
            "The tool execution deadline was reached. Work may still be terminating. Retry after 5 seconds with a narrower request.",
            5,
        ),
        "base64_pointer_unsupported": (
            "Base64 retrieval requires an empty pointer; retry to retrieve the exact original bytes.",
            None,
        ),
        "pointer_syntax_invalid": (
            "The pointer is not valid bounded RFC 6901 syntax; use a pointer from structure discovery.",
            None,
        ),
        "array_index_invalid": (
            "The pointer has an invalid array index; use a canonical index from structure discovery.",
            None,
        ),
        "scalar_selection_required": (
            "The selected value is a container; inspect its structure and select scalar children.",
            None,
        ),
        "text_selection_required": (
            "Text retrieval requires a string; use structure or the owning read tool for other values.",
            None,
        ),
        "json_pointer_required": (
            "Pointers require a JSON representation; retry without a pointer for non-JSON content.",
            None,
        ),
        "membership_profile_mismatch": (
            "Gene alias member search is unavailable for this installed snapshot. Rebuild and install a compatible immutable snapshot; raw records and exact source-field search remain available.",
            None,
        ),
        "numeric_detail_id_required": (
            "This detail route requires the internal numeric id returned by supported discovery.",
            None,
        ),
        "unsupported_related_mode": (
            "The relationship arguments do not match a supported connected-object or pair mode.",
            None,
        ),
    }
    if public_subtype in guidance:
        message, retry_after_seconds = guidance[public_subtype]
        result["message"] = message
        if retry_after_seconds is not None:
            result["retry_after_seconds"] = retry_after_seconds
    recoverable_ref = bool(content_ref and re.fullmatch(r"content:[0-9a-f]{64}", content_ref))
    if content_ref and not recoverable_ref:
        try:
            AssetReference.decode(content_ref)
            recoverable_ref = True
        except ClinPGxError:
            pass
    if content_ref and recoverable_ref:
        structure_pointer = recovery_pointer is not None and (
            recovery_pointer == ""
            or (
                recovery_pointer.startswith("/")
                and len(recovery_pointer) <= 4096
                and recovery_pointer.count("/") <= 128
                and re.search(r"~(?![01])", recovery_pointer) is None
            )
        )
        arguments: dict[str, Any] = {
            "content_ref": content_ref,
            "pointer": recovery_pointer if structure_pointer else "",
            "representation": "structure" if structure_pointer else "base64",
        }
        if public_subtype == "base64_pointer_unsupported":
            arguments.update(start=0, length=256)
        result["recovery_action"] = "read_original_bytes"
        result["fallback_tool"] = "get_source_content"
        result["fallback_args"] = arguments
        result["_meta"]["next_commands"] = [{"tool": "get_source_content", "arguments": arguments}]
    elif recovery is not None:
        recovery_data = recovery_payload(recovery)
        commands = recovery_data["next_commands"]
        first = commands[0]
        result["recovery_action"] = recovery_data["action"]
        result["fallback_tool"] = first["tool"]
        result["fallback_args"] = first["arguments"]
        result["recovery"] = recovery_data
        result["_meta"]["next_commands"] = commands
    return wire_result(result, is_error=True)
