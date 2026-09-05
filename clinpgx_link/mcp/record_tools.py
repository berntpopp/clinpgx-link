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
from clinpgx_link.mcp import recovery as recovery_help
from clinpgx_link.mcp.envelope import error_result
from clinpgx_link.mcp.pagination import CursorCodec
from clinpgx_link.mcp.record_shaping import (
    local_collection_result,
    local_singleton_result,
    snapshot_id,
)
from clinpgx_link.mcp.search_contracts import FILTER_DESCRIPTION, resolve_search_route
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
        description=FILTER_DESCRIPTION,
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
        """Search exact live fields or installed dataset memberships."""
        began = time.monotonic()
        response: SourceResponse | None = None
        recovery: recovery_help.RecoveryPlan | None = None
        try:
            selected_filters = filters or {}
            try:
                recovery_help.validate_filters(selected_filters)
            except InvalidInputError as exc:
                return error_result(
                    exc,
                    recovery=recovery_help.invalid_filters_plan(entity_type, source, view),
                )
            if cursor is not None and offset:
                raise InvalidInputError("Cursor and offset cannot be combined.", field="offset")
            try:
                selected_source, translated = resolve_search_route(
                    entity_type, source, query, selected_filters
                )
            except InvalidInputError as exc:
                recovery = recovery_help.search_contract_plan(
                    entity_type, source, view, exc.subtype
                )
                raise
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
                assert translated is not None
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
            if entity_type not in recovery_help.LOCAL_ENTITIES:
                recovery = recovery_help.unsupported_search_plan(entity_type, source, view)
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
                exc,
                content_ref=response.details.get("content_ref") if response else None,
                recovery=recovery,
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
        recovery: recovery_help.RecoveryPlan | None = None
        try:
            if (
                source == "api"
                and entity_type == "variant"
                and record_id.startswith("rs")
                and recovery_help.safe_identifier(record_id)
            ):
                recovery = recovery_help.variant_symbol_plan(record_id, view)
                raise InvalidInputError(
                    "Variant symbols require collection search.", field="record_id"
                )
            if source == "api":
                if api is None:
                    raise UpstreamUnavailableError("API service is not configured.")
                if not recovery_help.detail_source_supported(entity_type, source):
                    recovery = recovery_help.unsupported_detail_plan(
                        entity_type, record_id, source, view
                    )
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
                operation = recovery_help.WEBSITE_GET.get(entity_type)
                if operation is None:
                    recovery = recovery_help.unsupported_detail_plan(
                        entity_type, record_id, source, view
                    )
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
            if entity_type not in recovery_help.LOCAL_ENTITIES:
                recovery = recovery_help.unsupported_detail_plan(
                    entity_type, record_id, source, view
                )
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
            if isinstance(exc, NotFoundError):
                recovery = recovery_help.not_found_plan(entity_type, record_id, source, view)
            return error_result(
                exc,
                content_ref=response.details.get("content_ref") if response else None,
                recovery=recovery,
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
        view: Annotated[
            View,
            Field(
                description=(
                    "API pair upstream projection; not sent upstream in connected-object mode, "
                    "but remains cursor-bound."
                ),
                examples=["base"],
            ),
        ] = "base",
        limit: LimitArg = 20,
        offset: OffsetArg = 0,
        cursor: CursorArg = None,
        response_mode: ModeArg = "compact",
    ) -> ToolResult:
        """Read live connected objects or pairs, or loss-preserving installed joins."""
        began = time.monotonic()
        response: SourceResponse | None = None
        recovery: recovery_help.RecoveryPlan | None = None
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
                    elif result_type in recovery_help.API_RESULT:
                        recovery = recovery_help.related_mode_plan(
                            record_id, result_type, other_id, other_type, source, view
                        )
                        raise InvalidInputError(
                            "The documented API pair route requires other_id.", field="other_id"
                        )
                    else:
                        recovery = recovery_help.related_mode_plan(
                            record_id, result_type, other_id, other_type, source, view
                        )
                        raise InvalidInputError(
                            "This result type has no documented API report route.",
                            field="result_type",
                        )
                else:
                    api_type = recovery_help.API_RESULT.get(result_type)
                    if api_type is None:
                        recovery = recovery_help.related_mode_plan(
                            record_id, result_type, other_id, other_type, source, view
                        )
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
                exc,
                content_ref=response.details.get("content_ref") if response else None,
                recovery=recovery,
            )
        except Exception:
            return error_result(ClinPGxError("Related-record retrieval failed."))


__all__ = ["register_record_tools"]
