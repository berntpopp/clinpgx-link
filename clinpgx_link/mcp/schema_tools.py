"""Paged, source-labelled discovery for the frozen operation registries."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from pydantic import Field

from clinpgx_link.api.registry import ApiRegistry
from clinpgx_link.api.website_operations import WebsiteRegistry
from clinpgx_link.content.store import ContentStore
from clinpgx_link.exceptions import ClinPGxError, InvalidInputError
from clinpgx_link.mcp.admission import run_sync
from clinpgx_link.mcp.envelope import error_result, success_result
from clinpgx_link.mcp.pagination import CursorCodec
from clinpgx_link.mcp.untrusted_content import fence_text
from clinpgx_link.models import SourceInfo


def register_schema_tool(server: FastMCP, store: ContentStore) -> None:
    api = ApiRegistry()
    website = WebsiteRegistry()
    entries = [
        {
            "operation": item["operation"],
            "namespace": "api",
            "source": "clinpgx_api",
            "callable": True,
            "schema": api.describe(item["operation"]),
        }
        for item in api.list_operations()
    ] + [
        {
            "operation": item["id"],
            "namespace": "website",
            "source": item["source"],
            "callable": item["callable"],
            "schema": website.describe(item["id"]),
        }
        for item in website.list_operations()
    ]
    for entry in entries:
        entry["availability"] = "not_guaranteed_live" if entry["callable"] else "inventory_only"
        if entry["operation"] == "GET /data/vip/{id}":
            entry["callable"] = False
            entry["availability"] = "documented_broken"
            entry["fallback_operation"] = "GET /site/vip/{id}"
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    identity = hashlib.sha256(encoded).hexdigest()
    retrieved_at = datetime.now(UTC).isoformat()
    codec = CursorCodec()

    def source(digest: str) -> SourceInfo:
        return SourceInfo(
            "ClinPGx Link vendored operation registry",
            "clinpgx://operation-registry",
            retrieved_at,
            digest,
            "server",
            coverage="inventory_not_live_validation",
        )

    @server.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        tags={"metadata"},
        output_schema=None,
    )
    async def get_api_schema(
        operation: Annotated[
            str | None,
            Field(
                description="Exact operation ID; omit to list.", examples=["GET /data/gene/{id}"]
            ),
        ] = None,
        namespace: Annotated[
            Literal["all", "api", "website"],
            Field(description="Website includes linked CPIC reference routes."),
        ] = "all",
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum operation entries.")] = 20,
        offset: Annotated[int, Field(ge=0, description="Zero-based entry offset.")] = 0,
        cursor: Annotated[
            str | None, Field(max_length=2048, description="Previous page cursor.")
        ] = None,
        response_mode: Annotated[
            Literal["minimal", "compact", "standard", "full"],
            Field(description="Detail mode; schemas remain reachable."),
        ] = "compact",
    ) -> ToolResult:
        """Discover exact allowlisted contracts; inventory-only routes are not callable."""
        try:
            selectors = {"namespace": namespace, "operation": operation}
            start = offset
            if cursor:
                position = codec.decode(cursor, selectors, offset=offset)
                if position.identity != identity:
                    raise InvalidInputError("Registry cursor identity changed.", field="cursor")
                start = position.offset
            selected = [item for item in entries if namespace in {"all", item["namespace"]}]
            if operation is not None:
                if cursor or offset:
                    raise InvalidInputError("Schema detail is not paged.", field="cursor")
                entry = next((item for item in selected if item["operation"] == operation), None)
                if entry is None:
                    raise InvalidInputError(
                        "Unknown operation in this namespace.", field="operation"
                    )
                raw = json.dumps(
                    entry["schema"], ensure_ascii=False, separators=(",", ":")
                ).encode()
                provenance = source(hashlib.sha256(raw).hexdigest())
                ref = await run_sync(store.put, raw, provenance, "application/json")
                result: dict[str, Any] = {key: val for key, val in entry.items() if key != "schema"}
                result.update(
                    content_ref=ref,
                    byte_count=len(raw),
                    derived=True,
                    representation="normalized_vendored_schema",
                    response_mode=response_mode,
                )
                if len(raw) <= 12000:
                    result["schema"] = fence_text(raw.decode(), source=provenance, record_id=ref)
                else:
                    result["schema"] = {"deferred_content": True, "content_ref": ref, "pointer": ""}
                return success_result(result, source=provenance)
            if start > len(selected):
                raise InvalidInputError("Offset exceeds registry size.", field="offset")
            page = selected[start : start + limit]
            stop = start + len(page)
            next_cursor = (
                codec.encode(selectors, identity=identity, offset=stop)
                if stop < len(selected)
                else None
            )
            return success_result(
                [{key: value for key, value in item.items() if key != "schema"} for item in page],
                source=source(identity),
                collection=True,
                pagination={
                    "offset": start,
                    "returned": len(page),
                    "total": len(selected),
                    "has_more": next_cursor is not None,
                    "next_cursor": next_cursor,
                },
            )
        except ClinPGxError as exc:
            return error_result(exc)
        except Exception:
            return error_result(ClinPGxError("Schema retrieval failed."))
