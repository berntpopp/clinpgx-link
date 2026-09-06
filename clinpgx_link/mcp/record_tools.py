"""Entity-oriented tools over exact live operations and immutable local rows."""

from __future__ import annotations

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
from clinpgx_link.identity_contracts import (
    numeric_detail_argument_is_valid,
    numeric_identity_contract,
)
from clinpgx_link.mcp import recovery as recovery_help
from clinpgx_link.mcp.adapter_selection import adapter_profile, adapter_selection_result
from clinpgx_link.mcp.admission import run_sync
from clinpgx_link.mcp.envelope import error_result
from clinpgx_link.mcp.pagination import CursorCodec
from clinpgx_link.mcp.record_shaping import (
    local_collection_result,
    local_singleton_result,
    snapshot_id,
)
from clinpgx_link.mcp.record_types import (
    DetailEntity,
    ObjectType,
    ResponseMode,
    ResultType,
    SearchEntity,
    View,
)
from clinpgx_link.mcp.relationship_contracts import (
    guideline_website_recommendation,
    resolve_api_relationship_route,
)
from clinpgx_link.mcp.search_contracts import FILTER_DESCRIPTION, resolve_search_route
from clinpgx_link.mcp.selection import validate_pointers
from clinpgx_link.mcp.shaping import SourcePresenter, source_pointer
from clinpgx_link.models import SourceResponse
from clinpgx_link.services.api import ApiService

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
        description=(
            "Use relationship without other_id for API connected-object mode; API pair mode "
            "requires other_id and a mapped result type published by capabilities."
        ),
        examples=["relationship"],
    ),
]
RecordIdArg = Annotated[
    str,
    Field(
        min_length=1,
        max_length=512,
        description=(
            "Exact ClinPGx identifier or returned local record_id; numeric-detail families "
            "reuse the decimal id returned by search_records."
        ),
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
PointersArg = Annotated[
    list[str] | None,
    Field(
        max_length=12,
        description="Ordered scalar RFC 6901 pointers; mutually exclusive with pointer.",
        examples=[["/id", "/name"]],
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
    base = response.details.get("source_pointer")
    target = (
        pointer.removeprefix(base)
        if isinstance(base, str) and base and pointer.startswith(base + "/")
        else ("" if isinstance(base, str) and base and pointer == base else pointer)
    )
    selected = SourceResponse(
        select_value(response.value, target), response.source, dict(response.details)
    )
    selected.details["source_pointer"] = source_pointer(
        selected.details.get("source_pointer"), target
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
                    response, offset, state_ref = await run_sync(
                        presenter.resume, cursor, selectors, offset=offset
                    )
                else:
                    if api is None:
                        raise UpstreamUnavailableError("API service is not configured.")
                    response = await api.search(entity_type, translated, view)
                assert response is not None
                return await run_sync(
                    presenter.present,
                    response,
                    selectors=selectors,
                    limit=limit,
                    offset=offset,
                    state_ref=state_ref,
                    response_mode=response_mode,
                    profile=adapter_profile("", family=entity_type),
                )
            local_snapshot = await run_sync(snapshot_id, repository)
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
            response = await run_sync(
                repository.search_entities,
                entity_type,
                query=query,
                filters=selected_filters,
                limit=limit,
                offset=offset,
                expected_snapshot=local_snapshot,
            )
            return await run_sync(
                local_collection_result,
                response,
                snapshot=local_snapshot,
                repository=repository,
                store=store,
                selectors=selectors,
                cursors=cursors,
                offset=offset,
                began=began,
                response_mode=response_mode,
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
        pointers: PointersArg = None,
        response_mode: ModeArg = "compact",
    ) -> ToolResult:
        """Get one exact entity without changing source; use source='website' for guideline URLs."""
        began = time.monotonic()
        response: SourceResponse | None = None
        recovery: recovery_help.RecoveryPlan | None = None
        try:
            selected_pointers = validate_pointers(pointers, pointer=pointer)
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
                if numeric_identity_contract(entity_type) is not None and not (
                    numeric_detail_argument_is_valid(entity_type, record_id)
                ):
                    recovery = recovery_help.numeric_detail_plan(entity_type, source, view)
                    raise InvalidInputError(
                        "Numeric detail identifier required.",
                        field="record_id",
                        subtype="numeric_detail_id_required",
                    )
                if api is None:
                    raise UpstreamUnavailableError("API service is not configured.")
                if not recovery_help.detail_source_supported(entity_type, source):
                    recovery = recovery_help.unsupported_detail_plan(
                        entity_type, record_id, source, view
                    )
                response = await api.get(entity_type, record_id, view)
                next_cmds = guideline_website_recommendation(entity_type, record_id, source)
                if selected_pointers is not None:
                    return await run_sync(
                        adapter_selection_result,
                        response,
                        selected_pointers,
                        store,
                        next_commands=next_cmds,
                    )
                return await run_sync(
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
                    response_mode=response_mode,
                    profile=None if pointer else adapter_profile("", family=entity_type),
                    next_commands=next_cmds,
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
                if selected_pointers is not None:
                    return await run_sync(
                        adapter_selection_result, response, selected_pointers, store
                    )
                return await run_sync(
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
                    response_mode=response_mode,
                    profile=None if pointer else adapter_profile("", family=entity_type),
                )
            local_snapshot = await run_sync(snapshot_id, repository)
            if entity_type not in recovery_help.LOCAL_ENTITIES:
                recovery = recovery_help.unsupported_detail_plan(
                    entity_type, record_id, source, view
                )
                raise InvalidInputError(
                    "This entity type is not indexed in the local snapshot.", field="entity_type"
                )
            assert repository is not None
            matches = await run_sync(
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
            response = await run_sync(
                repository.get_record,
                matches.value[0]["record_id"],
                expected_snapshot=local_snapshot,
            )
            return await run_sync(
                local_singleton_result,
                response,
                snapshot=local_snapshot,
                store=store,
                pointer=pointer,
                response_mode=response_mode,
                began=began,
                repository=repository,
                pointers=selected_pointers,
            )
        except ClinPGxError as exc:
            if isinstance(exc, NotFoundError):
                recovery = recovery_help.not_found_plan(entity_type, record_id, source, view)
            recovery_pointer = getattr(exc, "recovery_pointer", None)
            content_ref = getattr(exc, "content_ref", None)
            if response is not None and exc.subtype == "scalar_selection_required":
                base = response.details.get("source_pointer")
                if recovery_pointer is None:
                    recovery_pointer = base if isinstance(base, str) else None
            return error_result(
                exc,
                content_ref=(
                    content_ref or (response.details.get("content_ref") if response else None)
                ),
                recovery=recovery,
                recovery_pointer=recovery_pointer,
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
            Field(
                description=(
                    "Object class owning record_id; documentation-only and cursor-bound, not "
                    "sent upstream as a pair filter."
                ),
                examples=["Gene"],
            ),
        ] = "Gene",
        other_type: Annotated[
            ObjectType,
            Field(
                description=(
                    "Target connected-object type sent upstream without other_id; documentation-only "
                    "and cursor-bound in pair mode, not an upstream pair filter."
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
        """Use known IDs for live reports or joins. Guideline URLs live under source='website'."""
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
                try:
                    route = resolve_api_relationship_route(
                        record_id, result_type, other_id, other_type, view
                    )
                except InvalidInputError:
                    recovery = recovery_help.related_mode_plan(
                        record_id, result_type, other_id, other_type, source, view
                    )
                    raise
                state_ref = None
                if cursor is not None:
                    response, offset, state_ref = await run_sync(
                        presenter.resume, cursor, selectors, offset=offset
                    )
                else:
                    if api is None:
                        raise UpstreamUnavailableError("API service is not configured.")
                    response = await api.call(
                        route.operation,
                        path_parameters=route.path_parameters,
                        query_parameters=route.query_parameters,
                    )
                assert response is not None
                return await run_sync(
                    presenter.present,
                    response,
                    selectors=selectors,
                    limit=limit,
                    offset=offset,
                    state_ref=state_ref,
                    response_mode=response_mode,
                    profile=(
                        "connected_object"
                        if route.mode == "connected_object"
                        else adapter_profile("", family=result_type)
                    ),
                )
            local_snapshot = await run_sync(snapshot_id, repository)
            if cursor is not None:
                position = cursors.decode(cursor, selectors)
                if position.identity != local_snapshot:
                    raise UpstreamUnavailableError(
                        "The cursor belongs to another snapshot.", subtype="snapshot_mismatch"
                    )
                offset = position.offset
            assert repository is not None
            response = await run_sync(
                repository.related,
                record_id,
                result_type=result_type,
                other_id=other_id,
                limit=limit,
                offset=offset,
                expected_snapshot=local_snapshot,
            )
            return await run_sync(
                local_collection_result,
                response,
                snapshot=local_snapshot,
                repository=repository,
                store=store,
                selectors=selectors,
                cursors=cursors,
                offset=offset,
                began=began,
                response_mode=response_mode,
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
