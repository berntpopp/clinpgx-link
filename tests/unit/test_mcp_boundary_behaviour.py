"""Fleet behavior at the real FastMCP argument and schema boundary."""

from __future__ import annotations

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
            schema = tools[tool_name].inputSchema
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
