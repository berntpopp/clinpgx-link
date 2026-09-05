"""Entity-oriented tools over exact live operations and immutable local rows."""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from pydantic import Field

from clinpgx_link.api.website import WebsiteClient
from clinpgx_link.content.reader import select_value
from clinpgx_link.content.store import ContentStore
from clinpgx_link.data.repository import DatasetRepository
from clinpgx_link.exceptions import (
    AmbiguousQueryError,
    ClinPGxError,
    InvalidInputError,
    NotFoundError,
    ResponseTooLargeError,
    UpstreamUnavailableError,
)
from clinpgx_link.mcp.dataset_record_tools import (
    select_dataset_record_value,
    shape_dataset_row,
)
from clinpgx_link.mcp.envelope import error_result, success_result
from clinpgx_link.mcp.pagination import CursorCodec
from clinpgx_link.mcp.shaping import SourcePresenter, source_pointer
from clinpgx_link.models import SourceResponse
from clinpgx_link.services.api import ApiService

SearchEntity = Literal[
    "allele",
    "annotation_id",
    "chemical",
    "connection",
    "data_annotation",
    "disease",
    "gene",
    "guideline_annotation",
    "label",
    "literature",
    "ontology_term",
    "pathway",
    "summary_annotation",
    "variant",
    "variant_annotation",
]
DetailEntity = Literal[
    "allele",
    "annotation_id",
    "chemical",
    "disease",
    "gene",
    "guideline",
    "guideline_annotation",
    "haplotype",
    "label",
    "literature",
    "pathway",
    "summary_annotation",
    "variant",
    "variant_annotation",
    "vip",
]
ResultType = Literal[
    "allele",
    "evidence",
    "guideline_annotation",
    "label",
    "literature",
    "literature_annotation",
    "multilink_annotation",
    "pathway",
    "relationship",
    "summary_annotation",
    "variant_annotation",
    "vip",
    "vip_variant",
]
ObjectType = Literal["Gene", "Chemical", "Disease", "Variant"]
View = Literal["min", "base", "max"]
ResponseMode = Literal["minimal", "compact", "standard", "full"]
_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_LOCAL_ENTITIES = frozenset(
    {"allele", "annotation_id", "chemical", "disease", "gene", "literature", "variant"}
)
_WEBSITE_GET = {
    "allele": "GET /site/allele/{id}",
    "gene": "GET /site/gene/{id}",
    "guideline": "GET /site/guideline/{id}",
    "guideline_annotation": "GET /site/guidelineAnnotation/{id}",
    "haplotype": "GET /site/haplotype/{id}",
    "label": "GET /site/labelAnnotation/{id}",
    "pathway": "GET /site/pathway/{id}",
    "vip": "GET /site/vip/{id}",
}
_API_RESULT = {
    "guideline_annotation": "guidelineAnnotation",
    "label": "label",
    "literature_annotation": "literatureAnnotation",
    "multilink_annotation": "multilinkAnnotation",
    "pathway": "pathway",
    "summary_annotation": "summaryAnnotation",
    "variant_annotation": "variantAnnotation",
    "vip": "vip",
    "vip_variant": "vipVariant",
}


def _api_filters(entity_type: str, filters: dict[str, str]) -> dict[str, str] | None:
    """Translate only canonical filters with equivalent documented API semantics."""
    mapping: dict[str, dict[str, str]] = {
        "pathway": {"id": "accessionId"},
        "gene": {"id": "accessionId", "gene": "symbol"},
        "chemical": {"id": "accessionId", "chemical": "name"},
        "disease": {"id": "accessionId"},
        "variant": {"variant": "symbol"},
        "literature": {"id": "id"},
        "guideline_annotation": {"source": "source"},
        "label": {
            "source": "source",
            "gene": "relatedGenes.symbol",
            "chemical": "relatedChemicals.name",
        },
        "summary_annotation": {
            "id": "id",
            "annotation_id": "id",
            "gene": "location.genes.symbol",
            "chemical": "relatedChemicals.name",
            "variant": "location.fingerprint",
        },
        "variant_annotation": {
            "gene": "location.genes.symbol",
            "variant": "location.fingerprint",
        },
    }
    declared = mapping.get(entity_type)
    if declared is None or not filters or not set(filters) <= set(declared):
        return None
    return {declared[key]: value for key, value in filters.items()}


def _validate_filters(filters: dict[str, str]) -> None:
    allowed = {"id", "name", "gene", "chemical", "variant", "source", "annotation_id"}
    for key, value in filters.items():
        if key not in allowed:
            raise InvalidInputError("Unknown canonical entity filter.", field=key)
        if not isinstance(value, str) or not value:
            raise InvalidInputError("Entity filters must be nonempty strings.", field=key)


def _snapshot(repository: DatasetRepository | None) -> str:
    if repository is None:
        raise UpstreamUnavailableError(
            "Local dataset snapshot is not configured.", subtype="dataset_unavailable"
        )
    return str(repository.status()["snapshot_id"])


def _select(response: SourceResponse, pointer: str) -> SourceResponse:
    selected = SourceResponse(
        select_value(response.value, pointer), response.source, dict(response.details)
    )
    selected.details["source_pointer"] = source_pointer(
        selected.details.get("source_pointer"), pointer
    )
    return selected


def _local_rows(
    response: SourceResponse, snapshot_id: str, repository: DatasetRepository, store: ContentStore
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    trusted: dict[tuple[str, str], frozenset[str]] = {}
    for row in response.value:
        key = (str(row["dataset_id"]), str(row["member"]))
        if key not in trusted:
            trusted[key] = _trusted_fields(repository, *key)
        asset = repository.get_record(row["record_id"], expected_snapshot=snapshot_id)
        rows.append(
            shape_dataset_row(
                row,
                response,
                snapshot_id,
                store,
                asset_response=asset,
                trusted_field_names=trusted[key],
            )
        )
    return rows


def _trusted_fields(
    repository: DatasetRepository, dataset_id: str, member_path: str
) -> frozenset[str]:
    description = repository.describe(dataset_id)
    member = next(
        (
            candidate
            for candidate in description.value["members"]
            if candidate["path"] == member_path
        ),
        None,
    )
    if member is None:
        return frozenset()
    return frozenset(
        str(field["name"])
        for field in member.get("fields", [])
        if isinstance(field, dict) and isinstance(field.get("name"), str)
    )


def _local_page(
    response: SourceResponse,
    rows: list[dict[str, Any]],
    selectors: dict[str, Any],
    snapshot_id: str,
    cursors: CursorCodec,
    offset: int,
    began: float,
) -> ToolResult:
    total = int(response.details["total_count"])
    while True:
        stop = offset + len(rows)
        next_cursor = (
            cursors.encode(selectors, identity=snapshot_id, offset=stop) if stop < total else None
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
            if len(rows) <= 1:
                raise
            rows = rows[: max(1, len(rows) // 2)]


def register_record_tools(
    server: FastMCP,
    repository: DatasetRepository | None,
    store: ContentStore,
    api: ApiService | None,
    website: WebsiteClient | None,
) -> None:
    """Register stable entity tools even when one or more sources are absent."""
    presenter = SourcePresenter(store)
    cursors = CursorCodec(clock=store.now)

    @server.tool(annotations=_ANNOTATIONS, tags={"entity", "search"}, output_schema=None)
    async def search_records(
        entity_type: SearchEntity,
        query: Annotated[str | None, Field(max_length=512)] = None,
        filters: dict[str, str] | None = None,
        source: Literal["auto", "api", "download"] = "auto",
        view: View = "base",
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        offset: Annotated[int, Field(ge=0)] = 0,
        cursor: Annotated[str | None, Field(max_length=2048)] = None,
        response_mode: ResponseMode = "compact",
    ) -> ToolResult:
        """Search exact live fields or broad installed entity memberships."""
        began = time.monotonic()
        response: SourceResponse | None = None
        try:
            selected_filters = filters or {}
            _validate_filters(selected_filters)
            if cursor is not None and offset:
                raise InvalidInputError("Cursor and offset cannot be combined.", field="offset")
            translated = None if query is not None else _api_filters(entity_type, selected_filters)
            selected_source = source
            if source == "auto":
                selected_source = "api" if translated is not None else "download"
            selectors = {
                "tool": "search_records",
                "entity_type": entity_type,
                "query": query,
                "filters": selected_filters,
                "source": source,
                "selected_source": selected_source,
                "view": view,
            }
            if selected_source == "api":
                if query is not None or translated is None:
                    raise InvalidInputError(
                        "The API supports only declared exact filters for this entity.",
                        field="filters" if query is None else "query",
                    )
                state_ref = None
                if cursor is not None:
                    response, offset, state_ref = await asyncio.to_thread(
                        presenter.resume, cursor, selectors, offset=offset
                    )
                else:
                    if api is None:
                        raise UpstreamUnavailableError("API service is not configured.")
                    response = await api.search(entity_type, translated, view)
                assert response is not None
                return await asyncio.to_thread(
                    presenter.present,
                    response,
                    selectors=selectors,
                    limit=limit,
                    offset=offset,
                    state_ref=state_ref,
                )
            snapshot_id = await asyncio.to_thread(_snapshot, repository)
            if entity_type not in _LOCAL_ENTITIES:
                raise InvalidInputError(
                    "This entity type is not indexed in the local snapshot.", field="entity_type"
                )
            if cursor is not None:
                position = cursors.decode(cursor, selectors)
                if position.identity != snapshot_id:
                    raise UpstreamUnavailableError(
                        "The cursor belongs to another snapshot.", subtype="snapshot_mismatch"
                    )
                offset = position.offset
            assert repository is not None
            response = await asyncio.to_thread(
                repository.search_entities,
                entity_type,
                query=query,
                filters=selected_filters,
                limit=limit,
                offset=offset,
                expected_snapshot=snapshot_id,
            )
            rows = await asyncio.to_thread(_local_rows, response, snapshot_id, repository, store)
            return _local_page(response, rows, selectors, snapshot_id, cursors, offset, began)
        except ClinPGxError as exc:
            return error_result(
                exc, content_ref=response.details.get("content_ref") if response else None
            )
        except Exception:
            return error_result(ClinPGxError("Entity search failed."))

    @server.tool(annotations=_ANNOTATIONS, tags={"entity", "record"}, output_schema=None)
    async def get_record(
        entity_type: DetailEntity,
        record_id: Annotated[str, Field(min_length=1, max_length=512)],
        source: Literal["api", "website", "download"] = "api",
        view: View = "max",
        pointer: Annotated[str, Field(max_length=4096)] = "",
        response_mode: ResponseMode = "compact",
    ) -> ToolResult:
        """Get one exact entity without changing the requested source."""
        began = time.monotonic()
        response: SourceResponse | None = None
        try:
            if source == "api":
                if api is None:
                    raise UpstreamUnavailableError("API service is not configured.")
                response = await api.get(entity_type, record_id, view)
                return await asyncio.to_thread(
                    presenter.present,
                    _select(response, pointer),
                    selectors={
                        "tool": "get_record",
                        "entity_type": entity_type,
                        "record_id": record_id,
                        "source": source,
                        "view": view,
                        "pointer": pointer,
                    },
                )
            if source == "website":
                operation = _WEBSITE_GET.get(entity_type)
                if operation is None:
                    raise InvalidInputError(
                        "No verified website detail route exists for this entity.",
                        field="entity_type",
                    )
                if website is None:
                    raise UpstreamUnavailableError("Website service is not configured.")
                response = await website.call(
                    operation,
                    {"id": record_id},
                    {"view": view} if entity_type == "guideline" else {},
                )
                return await asyncio.to_thread(
                    presenter.present,
                    _select(response, pointer),
                    selectors={
                        "tool": "get_record",
                        "entity_type": entity_type,
                        "record_id": record_id,
                        "source": source,
                        "view": view,
                        "pointer": pointer,
                    },
                )
            snapshot_id = await asyncio.to_thread(_snapshot, repository)
            if entity_type not in _LOCAL_ENTITIES:
                raise InvalidInputError(
                    "This entity type is not indexed in the local snapshot.", field="entity_type"
                )
            assert repository is not None
            matches = await asyncio.to_thread(
                repository.search_entities,
                entity_type,
                filters={"id": record_id},
                limit=2,
                offset=0,
                expected_snapshot=snapshot_id,
            )
            total = int(matches.details["total_count"])
            if total == 0:
                raise NotFoundError("No exact installed entity matched.", field="record_id")
            if total != 1:
                raise AmbiguousQueryError(
                    "The external identity matches more than one installed row.", field="record_id"
                )
            response = await asyncio.to_thread(
                repository.get_record,
                matches.value[0]["record_id"],
                expected_snapshot=snapshot_id,
            )
            trusted = await asyncio.to_thread(
                _trusted_fields,
                repository,
                str(response.value["dataset_id"]),
                str(response.value["member"]),
            )
            result = shape_dataset_row(
                response.value,
                response,
                snapshot_id,
                store,
                trusted_field_names=trusted,
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
            return error_result(
                exc, content_ref=response.details.get("content_ref") if response else None
            )
        except Exception:
            return error_result(ClinPGxError("Entity retrieval failed."))

    @server.tool(annotations=_ANNOTATIONS, tags={"entity", "relationship"}, output_schema=None)
    async def get_related_records(
        record_id: Annotated[str, Field(min_length=1, max_length=512)],
        result_type: ResultType,
        other_id: Annotated[str | None, Field(max_length=512)] = None,
        entity_type: ObjectType = "Gene",
        other_type: ObjectType = "Chemical",
        source: Literal["api", "download"] = "api",
        view: View = "base",
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        offset: Annotated[int, Field(ge=0)] = 0,
        cursor: Annotated[str | None, Field(max_length=2048)] = None,
        response_mode: ResponseMode = "compact",
    ) -> ToolResult:
        """Read validated live pairs or loss-preserving installed joins."""
        began = time.monotonic()
        response: SourceResponse | None = None
        try:
            if cursor is not None and offset:
                raise InvalidInputError("Cursor and offset cannot be combined.", field="offset")
            selectors = {
                "tool": "get_related_records",
                "record_id": record_id,
                "result_type": result_type,
                "other_id": other_id,
                "entity_type": entity_type,
                "other_type": other_type,
                "source": source,
                "view": view,
            }
            if source == "api":
                api_type = _API_RESULT.get(result_type)
                if api_type is None:
                    raise InvalidInputError(
                        "This result type has no documented API pair route.", field="result_type"
                    )
                if other_id is None:
                    raise InvalidInputError(
                        "The documented API pair route requires other_id.", field="other_id"
                    )
                state_ref = None
                if cursor is not None:
                    response, offset, state_ref = await asyncio.to_thread(
                        presenter.resume, cursor, selectors, offset=offset
                    )
                else:
                    if api is None:
                        raise UpstreamUnavailableError("API service is not configured.")
                    response = await api.call(
                        "GET /report/pair/{firstObjId}/{secondObjId}/{resultType}",
                        path_parameters={
                            "firstObjId": record_id,
                            "secondObjId": other_id,
                            "resultType": api_type,
                        },
                        query_parameters={"view": view},
                    )
                assert response is not None
                return await asyncio.to_thread(
                    presenter.present,
                    response,
                    selectors=selectors,
                    limit=limit,
                    offset=offset,
                    state_ref=state_ref,
                )
            snapshot_id = await asyncio.to_thread(_snapshot, repository)
            if cursor is not None:
                position = cursors.decode(cursor, selectors)
                if position.identity != snapshot_id:
                    raise UpstreamUnavailableError(
                        "The cursor belongs to another snapshot.", subtype="snapshot_mismatch"
                    )
                offset = position.offset
            assert repository is not None
            response = await asyncio.to_thread(
                repository.related,
                record_id,
                result_type=result_type,
                other_id=other_id,
                limit=limit,
                offset=offset,
                expected_snapshot=snapshot_id,
            )
            rows = await asyncio.to_thread(_local_rows, response, snapshot_id, repository, store)
            return _local_page(response, rows, selectors, snapshot_id, cursors, offset, began)
        except ClinPGxError as exc:
            return error_result(
                exc, content_ref=response.details.get("content_ref") if response else None
            )
        except Exception:
            return error_result(ClinPGxError("Related-record retrieval failed."))


__all__ = ["register_record_tools"]
