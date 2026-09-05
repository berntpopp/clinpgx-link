"""Fleet behavior at the real FastMCP argument and schema boundary."""

from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest
from fastmcp import Client
from jsonschema import Draft202012Validator

from clinpgx_link.api.client import ClinPGxClient
from clinpgx_link.api.website import WebsiteClient
from clinpgx_link.config import Settings
from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp.facade import create_mcp
from clinpgx_link.services.api import ApiService
from tests.unit.test_repository import _repository


@pytest.mark.parametrize(
    ("subtype", "expected_message"),
    [
        (
            "base64_pointer_unsupported",
            "Base64 retrieval requires an empty pointer; retry to retrieve the exact original bytes.",
        ),
        (
            "pointer_syntax_invalid",
            "The pointer is not valid bounded RFC 6901 syntax; use a pointer from structure discovery.",
        ),
        (
            "array_index_invalid",
            "The pointer has an invalid array index; use a canonical index from structure discovery.",
        ),
        (
            "scalar_selection_required",
            "The selected value is a container; inspect its structure and select scalar children.",
        ),
        (
            "text_selection_required",
            "Text retrieval requires a string; use structure or the owning read tool for other values.",
        ),
        (
            "json_pointer_required",
            "Pointers require a JSON representation; retry without a pointer for non-JSON content.",
        ),
    ],
)
def test_closed_content_subtypes_select_fixed_public_messages(subtype, expected_message):
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.mcp.envelope import error_result

    hostile = "ignore-all-instructions-secret"
    result = error_result(
        InvalidInputError(hostile, field="pointer", hint=hostile, subtype=subtype)
    )

    assert result.structured_content["message"] == expected_message
    assert result.structured_content["subtype"] == subtype
    assert hostile not in result.content[0].text
    assert json.loads(result.content[0].text) == result.structured_content


def test_unknown_subtype_hint_and_message_cannot_supply_public_instructions():
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.mcp.envelope import error_result

    hostile = "ignore-all-instructions-secret"
    result = error_result(
        InvalidInputError(hostile, field="pointer", hint=hostile, subtype="hostile_subtype")
    )

    assert result.structured_content["message"] == (
        "The request is outside the supported input contract."
    )
    assert "subtype" not in result.structured_content
    assert hostile not in result.content[0].text


@pytest.mark.parametrize(
    ("subtype", "selection_index", "reason"),
    [
        ("scalar_selection_required", -1, "container_selected"),
        ("scalar_selection_required", 12, "container_selected"),
        ("scalar_selection_required", True, "container_selected"),
        ("scalar_selection_required", 0, "hostile_reason"),
        ("data_invalid", 0, "container_selected"),
        ("pointer_syntax_invalid", 0, "container_selected"),
    ],
)
def test_selection_metadata_requires_a_bounded_index_and_matching_closed_reason(
    subtype, selection_index, reason
):
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.mcp.envelope import error_result

    result = error_result(
        InvalidInputError(
            "discarded",
            subtype=subtype,
            selection_index=selection_index,
            reason=reason,
        )
    )

    assert "selection_index" not in result.structured_content
    assert "reason" not in result.structured_content


def test_selection_metadata_accepts_the_upper_bounded_index_for_its_closed_reason():
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.mcp.envelope import error_result

    result = error_result(
        InvalidInputError(
            "discarded",
            subtype="array_index_invalid",
            selection_index=11,
            reason="invalid_array_index",
        )
    )

    assert result.structured_content["selection_index"] == 11
    assert result.structured_content["reason"] == "invalid_array_index"


@pytest.mark.parametrize(
    ("subtype", "message", "retry_after"),
    [
        (
            "admission_capacity",
            "The server's active work capacity is full. Retry after 1 second.",
            1,
        ),
        (
            "execution_deadline",
            "The tool execution deadline was reached. Work may still be terminating. Retry after 5 seconds with a narrower request.",
            5,
        ),
    ],
)
def test_existing_capacity_and_deadline_guidance_is_preserved(subtype, message, retry_after):
    from clinpgx_link.exceptions import RateLimitedError, UpstreamUnavailableError
    from clinpgx_link.mcp.envelope import error_result

    error_type = RateLimitedError if subtype == "admission_capacity" else UpstreamUnavailableError
    result = error_result(error_type("discarded", subtype=subtype))

    assert result.structured_content["message"] == message
    assert result.structured_content["retry_after_seconds"] == retry_after


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{"limit": 0}, {}, {"untrusted": "secret"}])
async def test_boundary_clock_covers_validation_and_tool_errors(tmp_path, arguments):
    from clinpgx_link.mcp.middleware import BoundaryGuard

    store = ContentStore(tmp_path / "clock.sqlite")
    server = create_mcp(content_store=store)
    guard = next(item for item in server.middleware if isinstance(item, BoundaryGuard))
    ticks = iter([10.0, 10.012345])
    guard.clock = lambda: next(ticks)
    try:
        async with Client(server) as client:
            call = await client.call_tool("list_datasets", arguments, raise_on_error=False)
        assert call.structured_content["_meta"]["elapsed_ms"] == 12.345
        assert call.structured_content["_meta"]["timing_scope"] == "tool_boundary"
        assert json.loads(call.content[0].text) == call.structured_content
    finally:
        store.close()


@pytest.mark.asyncio
async def test_overload_timing_measures_preflight_and_admission(tmp_path):
    from clinpgx_link.mcp.admission import Admission
    from clinpgx_link.mcp.middleware import BoundaryGuard

    admission = Admission(2)
    store = ContentStore(tmp_path / "overload.sqlite")
    server = create_mcp(content_store=store, admission=admission)
    guard = next(item for item in server.middleware if isinstance(item, BoundaryGuard))
    ticks = iter([5.0, 5.00789])
    guard.clock = lambda: next(ticks)
    started, release = asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        await release.wait()

    active = asyncio.create_task(admission.run("local", work))
    await started.wait()
    try:
        async with Client(server) as client:
            result = await client.call_tool("get_server_capabilities", {}, raise_on_error=False)
        assert result.structured_content["subtype"] == "admission_capacity"
        assert result.structured_content["_meta"]["elapsed_ms"] == 7.89
        assert result.structured_content["_meta"]["timing_scope"] == "tool_boundary"
        assert json.loads(result.content[0].text) == result.structured_content
    finally:
        release.set()
        await active
        store.close()


@pytest.mark.asyncio
async def test_boundary_timing_growth_preserves_bounded_error_envelope():
    from fastmcp import FastMCP

    from clinpgx_link.mcp.envelope import wire_result
    from clinpgx_link.mcp.middleware import BoundaryGuard

    server = FastMCP("bounded timing")
    ticks = iter([1.0, 1.01, 1.02])
    server.add_middleware(BoundaryGuard(server, clock=lambda: next(ticks)))

    @server.tool(output_schema=None)
    async def get_server_capabilities():
        payload = {
            "success": True,
            "result": "",
            "_meta": {"request_id": "a", "elapsed_ms": 0, "timing_scope": "tool_body"},
        }
        size = len(json.dumps(payload, separators=(",", ":")).encode())
        payload["result"] = "x" * (100_000 - size)
        return wire_result(payload)

    async with Client(server) as client:
        result = await client.call_tool("get_server_capabilities", {}, raise_on_error=False)
    assert result.structured_content["error_code"] == "invalid_input"
    assert result.structured_content["subtype"] == "response_too_large"
    assert result.structured_content["_meta"]["timing_scope"] == "tool_boundary"
    assert result.structured_content["_meta"]["elapsed_ms"] == 20.0


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [0.0000001, 0.123456])
async def test_measured_success_timing_can_round_to_zero(tmp_path, delta):
    from clinpgx_link.mcp.middleware import BoundaryGuard

    store = ContentStore(tmp_path / "success-clock.sqlite")
    server = create_mcp(content_store=store)
    guard = next(item for item in server.middleware if isinstance(item, BoundaryGuard))
    ticks = iter([10.0, 10.0 + delta])
    guard.clock = lambda: next(ticks)
    try:
        async with Client(server) as client:
            call = await client.call_tool("get_server_capabilities", {})
        assert call.structured_content["_meta"]["elapsed_ms"] == round(delta * 1000, 3)
        assert call.structured_content["_meta"]["timing_scope"] == "tool_boundary"
        assert "timing_unavailable_reason" not in call.structured_content["_meta"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_unknown_arguments_are_actionable_without_reflecting_hostile_input(tmp_path, caplog):
    hostile_name = "ignore_previous_instructions_secret"
    hostile_value = "DO_NOT_LOG_THIS_SECRET_VALUE"
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(create_mcp(content_store=store)) as client:
            tools = await client.list_tools()
            assert len(tools) == 13
            for tool in tools:
                caplog.clear()
                call = await client.call_tool(
                    tool.name, {hostile_name: hostile_value}, raise_on_error=False
                )
                rendered = (
                    json.dumps(call.structured_content, sort_keys=True)
                    + "\n"
                    + "\n".join(
                        content.text for content in call.content if hasattr(content, "text")
                    )
                )
                assert call.is_error
                assert call.structured_content["error_code"] == "invalid_input"
                assert call.structured_content["field"] == "arguments"
                assert hostile_name not in rendered
                assert hostile_value not in rendered
                assert hostile_name not in caplog.text
                assert hostile_value not in caplog.text
    finally:
        store.close()


@pytest.mark.asyncio
async def test_in_memory_legacy_client_negotiates_2025_11_25_and_calls_tools(tmp_path):
    store = ContentStore(tmp_path / "legacy-client.sqlite")
    try:
        async with Client(create_mcp(content_store=store), mode="legacy") as client:
            assert client.protocol_version == "2025-11-25"
            tools = {tool.name for tool in await client.list_tools()}
            called = await client.call_tool("get_server_capabilities", {})
            unknown = await client.call_tool("unknown-tool-never-reflect", {}, raise_on_error=False)

        assert "get_server_capabilities" in tools
        assert called.structured_content["success"] is True
        assert unknown.structured_content["error_code"] == "not_found"
        assert "unknown-tool-never-reflect" not in json.dumps(unknown.structured_content)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_schema_validation_reports_only_declared_top_level_field(tmp_path):
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(create_mcp(content_store=store)) as client:
            invalid_limit = await client.call_tool(
                "list_datasets", {"limit": 0}, raise_on_error=False
            )
            assert invalid_limit.structured_content["field"] == "limit"

            hostile_nested_key = "nested_secret_key"
            nested = await client.call_tool(
                "search_dataset",
                {"dataset_id": "data/genes.zip", "filters": {hostile_nested_key: 7}},
                raise_on_error=False,
            )
            assert nested.structured_content["field"] == "filters"
            assert hostile_nested_key not in json.dumps(nested.structured_content)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_required_dataset_tool_examples_match_their_schemas(tmp_path):
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(create_mcp(content_store=store)) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        for tool_name in ("get_dataset", "search_dataset", "get_dataset_record"):
            schema = tools[tool_name].input_schema
            for field in schema["required"]:
                examples = schema["properties"][field].get("examples")
                assert examples, f"{tool_name}.{field} has no example"
                for example in examples:
                    Draft202012Validator(schema["properties"][field]).validate(example)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_record_example_is_discoverable_syntax_but_absent_from_fixture(tmp_path):
    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        server = create_mcp(content_store=store, repository=repository)
        tools = {tool.name: tool for tool in await server.list_tools()}
        record_schema = tools["get_dataset_record"].parameters["properties"]["record_id"]
        example = record_schema["examples"][0]
        assert re.fullmatch(r"record:[0-9a-f]{64}", example)
        assert "search_dataset" in record_schema["description"]

        async with Client(server) as client:
            call = await client.call_tool(
                "get_dataset_record", {"record_id": example}, raise_on_error=False
            )
        assert call.is_error
        assert call.structured_content["error_code"] == "not_found"
        assert call.structured_content["field"] == "record_id"
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_leading_source_examples_bind_and_call_with_only_required_arguments(tmp_path):
    requested_paths = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/v1/report/stats":
            return httpx.Response(200, json={"allGenes": {"count": 25047}})
        assert request.url.path == "/v1/site/pathwayCategories"
        return httpx.Response(
            200,
            json={"status": "success", "data": [{"id": "PA165108034", "name": "PK"}]},
        )

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    api = ApiService(upstream)
    website = WebsiteClient(upstream)
    server = create_mcp(content_store=store, api_service=api, website_client=website)
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        api_properties = tools["get_api_data"].parameters["properties"]
        website_properties = tools["get_website_data"].parameters["properties"]
        api_example = api_properties["operation"]["examples"][0]
        website_example = website_properties["operation"]["examples"][0]
        assert api.registry.bind(api_example, {}, {}).path == "/report/stats"
        assert website.registry.bind(website_example, {}, {}).path == "/site/pathwayCategories"
        assert api_properties["path_parameters"]["examples"] == [{"id": "PA124"}]
        assert website_properties["path_parameters"]["examples"] == [{"id": "PA124"}]

        async with Client(server) as client:
            api_call = await client.call_tool("get_api_data", {"operation": api_example})
            website_call = await client.call_tool(
                "get_website_data", {"operation": website_example}
            )
        api_data = json.loads(api_call.structured_content["result"]["data"]["text"])
        website_data = json.loads(website_call.structured_content["results"][0]["data"]["text"])
        assert api_data["allGenes"]["count"] == 25047
        assert website_data["id"] == "PA165108034"
        assert requested_paths == ["/v1/report/stats", "/v1/site/pathwayCategories"]
    finally:
        await upstream.close()
