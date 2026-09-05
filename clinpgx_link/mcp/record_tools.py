"""Entity-oriented tools over exact live operations and immutable local rows."""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Literal

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
    UpstreamUnavailableError,
)
from clinpgx_link.mcp.envelope import error_result
from clinpgx_link.mcp.pagination import CursorCodec
from clinpgx_link.mcp.record_shaping import (
    local_collection_result,
    local_singleton_result,
    snapshot_id,
)
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
SearchEntityArg = Annotated[
    SearchEntity,
    Field(description="Entity family to search in the selected source.", examples=["gene"]),
]
DetailEntityArg = Annotated[
    DetailEntity,
    Field(description="Entity family owning the requested identifier.", examples=["gene"]),
]
ResultTypeArg = Annotated[
    ResultType,
    Field(
        description="Connected-object report, API pair, or declared local join result type.",
        examples=["relationship"],
    ),
]
RecordIdArg = Annotated[
    str,
    Field(
        min_length=1,
        max_length=512,
        description="Exact ClinPGx identifier or returned local record_id.",
        examples=["PA124"],
    ),
]
QueryArg = Annotated[
    str | None,
    Field(
        max_length=512,
        description="Literal AND-token local text query; unavailable for API searches.",
        examples=["CYP2C19 clopidogrel"],
    ),
]
FilterArg = Annotated[
    dict[str, str] | None,
    Field(
        description="ANDed canonical exact filters: id, name, gene, chemical, variant, source, annotation_id.",
        examples=[{"gene": "CYP2C19", "chemical": "clopidogrel"}],
    ),
]
LimitArg = Annotated[
    int, Field(ge=1, le=100, description="Maximum rows returned on this page.", examples=[20])
]
OffsetArg = Annotated[
    int, Field(ge=0, description="Zero-based offset; cannot accompany cursor.", examples=[0])
]
CursorArg = Annotated[
    str | None,
    Field(
        max_length=2048,
        description="Opaque continuation returned for the same selectors and source identity.",
        examples=["authenticated-continuation"],
    ),
]
PointerArg = Annotated[
    str,
    Field(
        max_length=4096,
        description="RFC 6901 pointer in decoded API data or the normalized local record.",
        examples=["/symbol"],
    ),
]
ModeArg = Annotated[
    ResponseMode,
    Field(
        description="Response detail preference; does not change source identity.",
        examples=["compact"],
    ),
]
ViewArg = Annotated[
    View,
    Field(description="ClinPGx upstream projection for live API reads.", examples=["base"]),
]
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


def _select(response: SourceResponse, pointer: str) -> SourceResponse:
    selected = SourceResponse(
        select_value(response.value, pointer), response.source, dict(response.details)
    )
    selected.details["source_pointer"] = source_pointer(
        selected.details.get("source_pointer"), pointer
    )
    return selected


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
        entity_type: SearchEntityArg,
        query: QueryArg = None,
        filters: FilterArg = None,
        source: Annotated[
            Literal["auto", "api", "download"],
            Field(
                description="Source policy: auto routes exact filters to API and broad queries locally.",
                examples=["auto"],
            ),
        ] = "auto",
        view: ViewArg = "base",
        limit: LimitArg = 20,
        offset: OffsetArg = 0,
        cursor: CursorArg = None,
        response_mode: ModeArg = "compact",
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
            local_snapshot = await asyncio.to_thread(snapshot_id, repository)
            if entity_type not in _LOCAL_ENTITIES:
                raise InvalidInputError(
                    "This entity type is not indexed in the local snapshot.", field="entity_type"
                )
            if cursor is not None:
                position = cursors.decode(cursor, selectors)
                if position.identity != local_snapshot:
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
                expected_snapshot=local_snapshot,
            )
            return await asyncio.to_thread(
                local_collection_result,
                response,
                snapshot=local_snapshot,
                repository=repository,
                store=store,
                selectors=selectors,
                cursors=cursors,
                offset=offset,
                began=began,
            )
        except ClinPGxError as exc:
            return error_result(
                exc, content_ref=response.details.get("content_ref") if response else None
            )
        except Exception:
            return error_result(ClinPGxError("Entity search failed."))

    @server.tool(annotations=_ANNOTATIONS, tags={"entity", "record"}, output_schema=None)
    async def get_record(
        entity_type: DetailEntityArg,
        record_id: RecordIdArg,
        source: Annotated[
            Literal["api", "website", "download"],
            Field(
                description="Exact source to query; this tool never silently switches.",
                examples=["api"],
            ),
        ] = "api",
        view: ViewArg = "max",
        pointer: PointerArg = "",
        response_mode: ModeArg = "compact",
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
            local_snapshot = await asyncio.to_thread(snapshot_id, repository)
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
                expected_snapshot=local_snapshot,
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
                expected_snapshot=local_snapshot,
            )
            return await asyncio.to_thread(
                local_singleton_result,
                response,
                snapshot=local_snapshot,
                store=store,
                pointer=pointer,
                response_mode=response_mode,
                began=began,
            )
        except ClinPGxError as exc:
            return error_result(
                exc, content_ref=response.details.get("content_ref") if response else None
            )
        except Exception:
            return error_result(ClinPGxError("Entity retrieval failed."))

    @server.tool(annotations=_ANNOTATIONS, tags={"entity", "relationship"}, output_schema=None)
    async def get_related_records(
        record_id: RecordIdArg,
        result_type: ResultTypeArg,
        other_id: Annotated[
            str | None,
            Field(
                max_length=512,
                description="Second exact ClinPGx ID; when supplied, selects live API pair mode.",
                examples=["PA449053"],
            ),
        ] = None,
        entity_type: Annotated[
            ObjectType,
            Field(description="Object class owning record_id.", examples=["Gene"]),
        ] = "Gene",
        other_type: Annotated[
            ObjectType,
            Field(
                description=(
                    "Target connected-object family without other_id; otherwise pair object class."
                ),
                examples=["Chemical"],
            ),
        ] = "Chemical",
        source: Annotated[
            Literal["api", "download"],
            Field(
                description="Exact live connected/pair or installed join source.", examples=["api"]
            ),
        ] = "api",
        view: ViewArg = "base",
        limit: LimitArg = 20,
        offset: OffsetArg = 0,
        cursor: CursorArg = None,
        response_mode: ModeArg = "compact",
    ) -> ToolResult:
        """Read live connected objects or pairs, or loss-preserving installed joins."""
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
                if other_id is None:
                    if result_type == "relationship":
                        operation = "GET /report/connectedObjects/{id}/{type}"
                        path_parameters = {"id": record_id, "type": other_type}
                        query_parameters = None
                    elif result_type in _API_RESULT:
                        raise InvalidInputError(
                            "The documented API pair route requires other_id.", field="other_id"
                        )
                    else:
                        raise InvalidInputError(
                            "This result type has no documented API report route.",
                            field="result_type",
                        )
                else:
                    api_type = _API_RESULT.get(result_type)
                    if api_type is None:
                        raise InvalidInputError(
                            "This result type has no documented API pair route.",
                            field="result_type",
                        )
                    operation = "GET /report/pair/{firstObjId}/{secondObjId}/{resultType}"
                    path_parameters = {
                        "firstObjId": record_id,
                        "secondObjId": other_id,
                        "resultType": api_type,
                    }
                    query_parameters = {"view": view}
                state_ref = None
                if cursor is not None:
                    response, offset, state_ref = await asyncio.to_thread(
                        presenter.resume, cursor, selectors, offset=offset
                    )
                else:
                    if api is None:
                        raise UpstreamUnavailableError("API service is not configured.")
                    response = await api.call(
                        operation,
                        path_parameters=path_parameters,
                        query_parameters=query_parameters,
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
            local_snapshot = await asyncio.to_thread(snapshot_id, repository)
            if cursor is not None:
                position = cursors.decode(cursor, selectors)
                if position.identity != local_snapshot:
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
                expected_snapshot=local_snapshot,
            )
            return await asyncio.to_thread(
                local_collection_result,
                response,
                snapshot=local_snapshot,
                repository=repository,
                store=store,
                selectors=selectors,
                cursors=cursors,
                offset=offset,
                began=began,
            )
        except ClinPGxError as exc:
            return error_result(
                exc, content_ref=response.details.get("content_ref") if response else None
            )
        except Exception:
            return error_result(ClinPGxError("Related-record retrieval failed."))


__all__ = ["register_record_tools"]
