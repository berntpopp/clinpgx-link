"""MCP tools for the immutable local ClinPGx dataset snapshot."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from pydantic import Field

from clinpgx_link.content.assets import AssetReference
from clinpgx_link.content.store import ContentStore
from clinpgx_link.data.repository import DatasetRepository
from clinpgx_link.exceptions import (
    ClinPGxError,
    InvalidInputError,
    ResponseTooLargeError,
    UpstreamUnavailableError,
)
from clinpgx_link.mcp.admission import run_sync
from clinpgx_link.mcp.dataset_description_modes import ResponseMode, project_dataset_description
from clinpgx_link.mcp.envelope import error_result, success_result
from clinpgx_link.mcp.pagination import CursorCodec
from clinpgx_link.mcp.row_provenance import row_provenance
from clinpgx_link.mcp.untrusted_content import fence_text
from clinpgx_link.models import SourceInfo, SourceResponse

_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def _fence(value: Any, source: SourceInfo, record_id: str) -> Any:
    return fence_text(str(value), source=source, record_id=record_id)


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _metadata_ref(
    store: ContentStore, response: SourceResponse, *, force: bool = False
) -> str | None:
    """Retain the complete repository description as a derived, queryable body."""
    raw = _json(response.value)
    if not force and len(raw) <= 16_000:
        return None
    source = SourceInfo(
        "ClinPGx local snapshot metadata",
        "clinpgx://dataset-metadata/" + str(response.value["dataset_id"]),
        response.source.retrieved_at,
        hashlib.sha256(raw).hexdigest(),
        "derived",
        published_at=response.source.published_at,
        release_tag=response.source.release_tag,
        coverage="derived_not_original",
        warnings=response.source.warnings,
    )
    return store.put(raw, source, "application/json")


def _decorate_member(member: dict[str, Any], source: SourceInfo, dataset_id: str) -> dict[str, Any]:
    def decorate_sheet(value: Any, key: str, record_id: str) -> Any:
        if isinstance(value, dict):
            return {name: decorate_sheet(item, name, record_id) for name, item in value.items()}
        if isinstance(value, list):
            return [decorate_sheet(item, key, record_id) for item in value]
        if isinstance(value, str) and (
            key
            in {"name", "path", "title", "description", "limitation", "sheet_name", "column_name"}
            or key == "sheets"
        ):
            return _fence(value, source, record_id)
        return value

    value = dict(member)
    path = str(value["path"])
    value["path"] = _fence(path, source, f"{dataset_id}:{path}")
    if value.get("limitation"):
        value["limitation"] = _fence(value["limitation"], source, f"{dataset_id}:{path}")
    if "fields" in value:
        fields = []
        for field in value["fields"]:
            item = dict(field)
            item["name"] = _fence(item["name"], source, f"{dataset_id}:{path}")
            fields.append(item)
        value["fields"] = fields
    if isinstance(value.get("sheets"), list):
        value["sheets"] = [
            decorate_sheet(sheet, "sheets", f"{dataset_id}:{path}") for sheet in value["sheets"]
        ]
    return value


def _decorate_dataset(
    description: dict[str, Any],
    source: SourceInfo,
    snapshot_id: str,
    store: ContentStore,
    *,
    complete_description: dict[str, Any] | None = None,
    member_start: int = 0,
    member_stop: int | None = None,
) -> dict[str, Any]:
    value = dict(description)
    dataset_id = str(value["dataset_id"])
    archive_ref = AssetReference(snapshot_id, dataset_id, None, str(value["sha256"])).encode()
    value["archive_ref"] = archive_ref
    value["limitations"] = [
        _fence(item, source, dataset_id) for item in value.get("limitations", [])
    ]
    value["warnings"] = [_fence(item, source, dataset_id) for item in value.get("warnings", [])]
    raw_members = description.get("members", [])
    decorated_members = []
    stop = len(raw_members) if member_stop is None else min(member_stop, len(raw_members))
    for member in raw_members[member_start:stop]:
        path = str(member["path"])
        item = _decorate_member(member, source, dataset_id)
        item["content_ref"] = AssetReference(
            snapshot_id, dataset_id, path, str(member["sha256"])
        ).encode()
        item["member_sha256"] = str(member["sha256"])
        decorated_members.append(item)
    value["members"] = decorated_members
    metadata_source = complete_description if complete_description is not None else description
    metadata_ref = _metadata_ref(store, SourceResponse(metadata_source, source))
    if metadata_ref is not None:
        value["metadata_ref"] = metadata_ref
        value["metadata_deferred"] = False
    return value


def _catalog_value(
    value: dict[str, Any], source: SourceInfo, *, response_mode: ResponseMode = "compact"
) -> dict[str, Any]:
    result = dict(value)
    result["provenance"] = row_provenance(source)
    limitations = value.get("limitations", [])
    warnings = value.get("warnings", [])
    if response_mode == "minimal":
        archive_limitations = [
            item for item in limitations if not item.startswith("unparsed_member:")
        ]
        result["limitations"] = [
            _fence(item, source, str(value["dataset_id"])) for item in archive_limitations
        ]
        result["warnings"] = [_fence(item, source, str(value["dataset_id"])) for item in warnings]
        unparsed_count = len(limitations) - len(archive_limitations)
        if unparsed_count > 0:
            result["unparsed_member_count"] = unparsed_count
    else:
        result["limitations"] = [
            _fence(item, source, str(value["dataset_id"])) for item in limitations
        ]
        result["warnings"] = [_fence(item, source, str(value["dataset_id"])) for item in warnings]
    return result


def register_dataset_tools(
    server: FastMCP, repository: DatasetRepository | None, store: ContentStore
) -> None:
    """Register local snapshot catalog and description tools, even if unavailable."""
    cursors = CursorCodec(clock=store.now)

    @server.tool(annotations=_ANNOTATIONS, tags={"catalog"}, output_schema=None)
    async def list_datasets(
        query: Annotated[
            str | None,
            Field(description="Case-insensitive dataset ID or file-name filter.", max_length=256),
        ] = None,
        include_legacy: Annotated[
            bool, Field(description="Include datasets marked legacy or deprecated.")
        ] = True,
        limit: Annotated[int, Field(description="Maximum datasets to return.", ge=1, le=100)] = 20,
        offset: Annotated[int, Field(description="Zero-based dataset offset.", ge=0)] = 0,
        cursor: Annotated[
            str | None, Field(description="Snapshot-bound continuation cursor.", max_length=2048)
        ] = None,
        response_mode: Annotated[
            ResponseMode, Field(description="Response detail mode.")
        ] = "compact",
    ) -> ToolResult:
        """List datasets available in the installed snapshot catalog. Use response_mode='minimal' for concise overview."""
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
            snapshot_id = str((await run_sync(repository.status))["snapshot_id"])
            selectors = {
                "tool": "list_datasets",
                "query": query,
                "include_legacy": include_legacy,
            }
            if cursor is not None:
                position = cursors.decode(cursor, selectors)
                if position.identity != snapshot_id:
                    raise UpstreamUnavailableError(
                        "The cursor belongs to a different local snapshot.",
                        subtype="snapshot_mismatch",
                    )
                offset = position.offset
            response = await run_sync(repository.list_datasets)
            if response.source.sha256 != snapshot_id.removeprefix("sha256:"):
                raise UpstreamUnavailableError(
                    "The local snapshot identity changed.", subtype="snapshot_mismatch"
                )
            needle = query.casefold() if query else None
            dataset_sources = response.details.get("dataset_sources")
            if not isinstance(dataset_sources, dict):
                raise UpstreamUnavailableError(
                    "Installed dataset provenance is incomplete.", subtype="snapshot_invalid"
                )
            values = []
            for item in response.value:
                if not include_legacy and str(item.get("tier", "")).casefold() in {
                    "legacy",
                    "deprecated",
                }:
                    continue
                if needle and needle not in f"{item['dataset_id']} {item['file_name']}".casefold():
                    continue
                row_source = dataset_sources.get(item["dataset_id"])
                if not isinstance(row_source, SourceInfo):
                    raise UpstreamUnavailableError(
                        "Installed dataset provenance is incomplete.", subtype="snapshot_invalid"
                    )
                values.append(_catalog_value(item, row_source, response_mode=response_mode))
            total = len(values)
            if offset > total:
                raise InvalidInputError("Offset exceeds dataset catalog.", field="offset")
            selected = values[offset : offset + limit]
            while True:
                stop = offset + len(selected)
                next_cursor = (
                    cursors.encode(selectors, identity=snapshot_id, offset=stop)
                    if stop < total
                    else None
                )
                pagination = {
                    "offset": offset,
                    "returned": len(selected),
                    "total_count": total,
                    "has_more": next_cursor is not None,
                    "next_cursor": next_cursor,
                    "snapshot_id": snapshot_id,
                }
                try:
                    return success_result(
                        selected,
                        source=response.source,
                        collection=True,
                        pagination=pagination,
                        elapsed_ms=(time.monotonic() - began) * 1000,
                    )
                except ResponseTooLargeError:
                    if len(selected) <= 1:
                        raise
                    selected = selected[: max(1, len(selected) // 2)]
        except ClinPGxError as exc:
            return error_result(exc)
        except Exception:
            return error_result(ClinPGxError("Dataset catalog retrieval failed."))

    @server.tool(annotations=_ANNOTATIONS, tags={"catalog"}, output_schema=None)
    async def get_dataset(
        dataset_id: Annotated[
            str,
            Field(
                description="Exact installed dataset identifier.",
                min_length=1,
                max_length=512,
                examples=["data/genes.zip"],
            ),
        ],
        limit: Annotated[int, Field(description="Maximum members to return.", ge=1, le=100)] = 20,
        offset: Annotated[int, Field(description="Zero-based member offset.", ge=0)] = 0,
        cursor: Annotated[
            str | None, Field(description="Snapshot-bound continuation cursor.", max_length=2048)
        ] = None,
        response_mode: Annotated[
            ResponseMode, Field(description="Response detail mode.")
        ] = "compact",
    ) -> ToolResult:
        """Inspect one installed dataset snapshot record. Member items include member_sha256."""
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
            snapshot_id = str((await run_sync(repository.status))["snapshot_id"])
            selectors = {"tool": "get_dataset", "dataset_id": dataset_id}
            if cursor is not None:
                position = cursors.decode(cursor, selectors)
                if position.identity != snapshot_id:
                    raise UpstreamUnavailableError(
                        "The cursor belongs to a different local snapshot.",
                        subtype="snapshot_mismatch",
                    )
                offset = position.offset
            response = await run_sync(repository.describe, dataset_id)
            if response.details.get("snapshot_id") != snapshot_id:
                raise UpstreamUnavailableError(
                    "The local snapshot identity changed.", subtype="snapshot_mismatch"
                )
            total = len(response.value.get("members", []))
            if offset > total:
                raise InvalidInputError("Offset exceeds dataset members.", field="offset")
            page_description = dict(response.value)
            page_description["members"] = response.value.get("members", [])[offset : offset + limit]
            projected, detail_omitted = project_dataset_description(page_description, response_mode)
            value = await run_sync(
                _decorate_dataset,
                projected,
                response.source,
                snapshot_id,
                store,
                complete_description=response.value,
            )
            selected = value["members"]
            value["response_mode"] = response_mode
            if detail_omitted:
                value["metadata_projection"] = {
                    "omitted_optional_detail": True,
                    "note": "Optional presentation detail is omitted; source metadata is unchanged.",
                    "full_retrieval": {
                        "tool": "get_dataset",
                        "arguments": {
                            "dataset_id": str(response.value["dataset_id"]),
                            "limit": limit,
                            "offset": offset,
                            "response_mode": "full",
                        },
                    },
                }
            stop = offset + len(selected)
            next_cursor = (
                cursors.encode(selectors, identity=snapshot_id, offset=stop)
                if stop < total
                else None
            )
            pagination = {
                "offset": offset,
                "returned": len(selected),
                "total_count": total,
                "has_more": next_cursor is not None,
                "next_cursor": next_cursor,
                "snapshot_id": snapshot_id,
            }
            try:
                return success_result(
                    value,
                    source=response.source,
                    pagination=pagination,
                    elapsed_ms=(time.monotonic() - began) * 1000,
                )
            except ResponseTooLargeError:
                while len(selected) > 1:
                    selected = selected[: max(1, len(selected) // 2)]
                    value["members"] = selected
                    stop = offset + len(selected)
                    pagination["returned"] = len(selected)
                    pagination["has_more"] = stop < total
                    pagination["next_cursor"] = (
                        cursors.encode(selectors, identity=snapshot_id, offset=stop)
                        if stop < total
                        else None
                    )
                    try:
                        return success_result(value, source=response.source, pagination=pagination)
                    except ResponseTooLargeError:
                        continue
                if "metadata_ref" not in value:
                    value["metadata_ref"] = _metadata_ref(store, response, force=True)
                value["members"] = [
                    {
                        "path": member["path"],
                        "content_ref": member["content_ref"],
                        "deferred_metadata": True,
                        "metadata_ref": value["metadata_ref"],
                        "metadata_pointer": f"/members/{offset + index}",
                        "fallback_args": {
                            "content_ref": value["metadata_ref"],
                            "pointer": f"/members/{offset + index}",
                            "representation": "structure",
                        },
                    }
                    for index, member in enumerate(selected)
                ]
                pagination["returned"] = len(selected)
                return success_result(value, source=response.source, pagination=pagination)
        except ClinPGxError as exc:
            return error_result(exc)
        except Exception:
            return error_result(ClinPGxError("Dataset description retrieval failed."))


__all__ = ["register_dataset_tools"]
