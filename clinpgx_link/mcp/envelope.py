"""One flat, mirrored fleet response boundary."""

from __future__ import annotations

import json
import re
import uuid
from contextvars import ContextVar
from typing import Any, cast

from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from clinpgx_link.exceptions import ClinPGxError, ResponseTooLargeError
from clinpgx_link.mcp.untrusted_content import UntrustedText, enforce_limits, fence_text
from clinpgx_link.models import SourceInfo

REQUEST_ID: ContextVar[str] = ContextVar("clinpgx_request_id", default="")
_MESSAGES = {
    "invalid_input": "The request is outside the supported input contract.",
    "not_found": "The requested record or tool is unavailable.",
    "ambiguous_query": "The request needs a more specific selector.",
    "upstream_unavailable": "The required source is currently unavailable.",
    "rate_limited": "The source or retained-content cache is at capacity.",
    "internal": "The server could not complete this request.",
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
    if len(serialized.encode("utf-8")) > 100_000:
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
    elapsed_ms: float = 0,
    collection: bool = False,
    pagination: dict[str, Any] | None = None,
    content_ref: str | None = None,
) -> ToolResult:
    """Build provenance without confusing source time with the time of a cache hit."""
    request_id = REQUEST_ID.get() or str(uuid.uuid4())
    return wire_result(
        {
            "success": True,
            "results" if collection else "result": value,
            "_meta": {
                "request_id": request_id,
                "elapsed_ms": round(elapsed_ms, 3),
                "source": source.source,
                "source_url": source.url,
                "data_source": source.data_source,
                "retrieved_at": source.retrieved_at,
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
            },
            "recommended_citation": f"ClinPGx source evidence. {source.url} Retrieved {source.retrieved_at}.",
            "unsafe_for_clinical_use": True,
        }
    )


def error_result(error: ClinPGxError, *, content_ref: str | None = None) -> ToolResult:
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
            "unsafe_for_clinical_use": True,
            "next_commands": [{"tool": "get_server_capabilities", "arguments": {}}],
        },
    }
    for key, value in (("field", error.field), ("subtype", error.subtype)):
        if value and re.fullmatch(r"[a-z_]{1,40}", value):
            result[key] = value
    if content_ref and re.fullmatch(r"content:[0-9a-f]{64}", content_ref):
        arguments = {"content_ref": content_ref, "pointer": "", "representation": "base64"}
        result["recovery_action"] = "read_original_bytes"
        result["fallback_tool"] = "get_source_content"
        result["fallback_args"] = arguments
        result["_meta"]["next_commands"] = [{"tool": "get_source_content", "arguments": arguments}]
    return wire_result(result, is_error=True)
