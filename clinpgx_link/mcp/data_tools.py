"""Validated API/site tools share source presentation and immutable continuations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from pydantic import Field

from clinpgx_link.api.website import WebsiteClient
from clinpgx_link.content.reader import select_value
from clinpgx_link.content.store import ContentStore
from clinpgx_link.exceptions import ClinPGxError, InvalidInputError, UpstreamUnavailableError
from clinpgx_link.mcp.adapter_selection import adapter_profile, render_adapter_selections
from clinpgx_link.mcp.envelope import error_result, success_result
from clinpgx_link.mcp.selection import finite_json_bytes, validate_pointers
from clinpgx_link.mcp.shaping import SourcePresenter, source_pointer
from clinpgx_link.models import SourceResponse
from clinpgx_link.services.api import ApiService

Mode = Literal["minimal", "compact", "standard", "full"]
_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def register_data_tools(
    server: FastMCP, store: ContentStore, api: ApiService | None, website: WebsiteClient | None
) -> None:
    presenter = SourcePresenter(store)

    async def run(
        fetch: Callable[[], Awaitable[SourceResponse]],
        selectors: dict[str, Any],
        limit: int,
        offset: int,
        cursor: str | None,
        pointers: list[str] | None,
        response_mode: Mode,
        profile: str | None,
    ) -> ToolResult:
        response = None
        try:
            selected_pointers = validate_pointers(
                pointers,
                pointer=str(selectors["pointer"]),
                cursor=cursor,
                offset=offset,
            )
            state_ref = None
            if cursor:
                response, offset, state_ref = await asyncio.to_thread(
                    presenter.resume, cursor, selectors, offset=offset
                )
            else:
                response = await fetch()
                if selected_pointers is not None:
                    selected = render_adapter_selections(response, selected_pointers, store)
                    if len(finite_json_bytes(selected)) > 70_000:
                        from clinpgx_link.exceptions import ResponseTooLargeError

                        raise ResponseTooLargeError(
                            "The selected source row exceeds its bounded descriptor size."
                        )
                    return success_result(
                        selected,
                        source=response.source,
                        content_ref=response.details["content_ref"],
                    )
                response = SourceResponse(
                    select_value(response.value, selectors["pointer"]),
                    response.source,
                    dict(response.details),
                )
                response.details["source_pointer"] = source_pointer(
                    response.details.get("source_pointer"), selectors["pointer"]
                )
            return await asyncio.to_thread(
                presenter.present,
                response,
                selectors=selectors,
                limit=limit,
                offset=offset,
                state_ref=state_ref,
                response_mode=response_mode,
                profile=None if selectors["pointer"] else profile,
            )
        except ClinPGxError as exc:
            recovery_pointer = None
            if response is not None and exc.subtype == "scalar_selection_required":
                base = response.details.get("source_pointer")
                recovery_pointer = base if isinstance(base, str) else None
            return error_result(
                exc,
                content_ref=response.details.get("content_ref") if response else None,
                recovery_pointer=recovery_pointer,
            )
        except Exception:
            return error_result(ClinPGxError("Source retrieval failed."))

    @server.tool(annotations=_ANNOTATIONS, tags={"source"}, output_schema=None)
    async def get_api_data(
        operation: Annotated[
            str,
            Field(
                description="Exact API operation from get_api_schema.",
                examples=["GET /report/stats", "GET /data/gene/{id}"],
            ),
        ],
        path_parameters: Annotated[
            dict[str, Any] | None,
            Field(description="Declared path fields only.", examples=[{"id": "PA124"}]),
        ] = None,
        query_parameters: Annotated[
            dict[str, Any] | None, Field(description="Declared query fields only.")
        ] = None,
        form_parameters: Annotated[
            dict[str, Any] | None, Field(description="Verified Infobutton form only.")
        ] = None,
        representation: Annotated[
            Literal["json", "jsonld", "html", "text"],
            Field(description="Verified source representation."),
        ] = "json",
        pointer: Annotated[
            str, Field(max_length=4096, description="RFC 6901 pointer in decoded source data.")
        ] = "",
        pointers: Annotated[
            list[str] | None,
            Field(max_length=12, description="Ordered scalar RFC 6901 pointers for JSON data."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum returned rows.")] = 20,
        offset: Annotated[
            int, Field(ge=0, description="Offset in this retained response, not upstream corpus.")
        ] = 0,
        cursor: Annotated[
            str | None, Field(max_length=2048, description="Immutable previous-page cursor.")
        ] = None,
        response_mode: Annotated[
            Mode, Field(description="Detail mode; source content stays reachable.")
        ] = "compact",
    ) -> ToolResult:
        """Read one allowlisted API operation; paginate its response without refetching."""

        async def fetch() -> SourceResponse:
            if api is None:
                raise UpstreamUnavailableError("API service is not configured.")
            return await api.call(
                operation,
                path_parameters=path_parameters,
                query_parameters=query_parameters,
                form_parameters=form_parameters,
                representation=representation,
            )

        selectors = {
            "tool": "get_api_data",
            "operation": operation,
            "path_parameters": path_parameters or {},
            "query_parameters": query_parameters or {},
            "form_parameters": form_parameters or {},
            "representation": representation,
            "pointer": pointer,
        }
        if pointers is not None and representation in {"html", "text"}:
            try:
                validate_pointers(pointers, pointer=pointer, cursor=cursor, offset=offset)
            except ClinPGxError as exc:
                return error_result(exc)
            else:
                return error_result(
                    InvalidInputError(
                        "Scalar selection requires a verified JSON representation.",
                        field="pointers",
                        subtype="json_selection_required",
                    )
                )
        return await run(
            fetch,
            selectors,
            limit,
            offset,
            cursor,
            pointers,
            response_mode,
            adapter_profile(operation),
        )

    @server.tool(annotations=_ANNOTATIONS, tags={"source"}, output_schema=None)
    async def get_website_data(
        operation: Annotated[
            str,
            Field(
                description="Website or CPIC operation from get_api_schema.",
                examples=["GET /site/pathwayCategories", "GET /site/gene/{id}"],
            ),
        ],
        path_parameters: Annotated[
            dict[str, Any] | None,
            Field(description="Declared path fields only.", examples=[{"id": "PA124"}]),
        ] = None,
        query_parameters: Annotated[
            dict[str, Any] | None, Field(description="Declared query fields only.")
        ] = None,
        pointer: Annotated[
            str, Field(max_length=4096, description="RFC 6901 pointer in decoded source data.")
        ] = "",
        pointers: Annotated[
            list[str] | None,
            Field(max_length=12, description="Ordered scalar RFC 6901 pointers for JSON data."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum returned rows.")] = 20,
        offset: Annotated[
            int, Field(ge=0, description="Offset in this retained response, not upstream corpus.")
        ] = 0,
        cursor: Annotated[
            str | None, Field(max_length=2048, description="Immutable previous-page cursor.")
        ] = None,
        response_mode: Annotated[
            Mode, Field(description="Detail mode; source content stays reachable.")
        ] = "compact",
    ) -> ToolResult:
        """Read verified site and linked CPIC reference routes with distinct provenance."""

        async def fetch() -> SourceResponse:
            if website is None:
                raise UpstreamUnavailableError("Website service is not configured.")
            return await website.call(operation, path_parameters, query_parameters)

        selectors = {
            "tool": "get_website_data",
            "operation": operation,
            "path_parameters": path_parameters or {},
            "query_parameters": query_parameters or {},
            "pointer": pointer,
        }
        return await run(
            fetch,
            selectors,
            limit,
            offset,
            cursor,
            pointers,
            response_mode,
            adapter_profile(operation),
        )
