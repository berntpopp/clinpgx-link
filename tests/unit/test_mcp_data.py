"""Real source adapters through FastMCP, with only external HTTP replaced."""

import json

import httpx
import pytest
from fastmcp import Client

from clinpgx_link.api.client import ClinPGxClient
from clinpgx_link.api.website import WebsiteClient
from clinpgx_link.config import Settings
from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp.facade import create_mcp
from clinpgx_link.services.api import ApiService


@pytest.mark.asyncio
async def test_api_data_pages_without_refetch_and_rejects_changed_selector(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.path == "/v1/data/gene"
        assert request.url.params["symbol"] == "CYP2C19"
        return httpx.Response(
            200, json={"status": "success", "data": [{"id": "PA1"}, {"id": "PA2"}]}
        )

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    server = create_mcp(content_store=store, api_service=ApiService(upstream))
    try:
        async with Client(server) as client:
            args = {
                "operation": "GET /data/gene",
                "query_parameters": {"symbol": "CYP2C19"},
                "limit": 1,
            }
            first = await client.call_tool("get_api_data", args)
            cursor = first.structured_content["_meta"]["pagination"]["next_cursor"]
            second = await client.call_tool(
                "get_api_data", {**args, "cursor": cursor, "response_mode": "full"}
            )
            assert second.structured_content["results"][0]["id"] == "PA2"
            assert len(calls) == 1
            bad = await client.call_tool(
                "get_api_data", {**args, "cursor": cursor, "pointer": "/x"}, raise_on_error=False
            )
            assert bad.is_error
            assert len(calls) == 1
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_website_text_plain_json_is_retrievable_through_mcp(tmp_path):
    def handle(request):
        assert request.url.path == "/v1/site/alleleFunction/PA128"
        return httpx.Response(200, text='{"alleles":[{"name":"*2","function":"Normal function"}]}')

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(
            create_mcp(content_store=store, website_client=WebsiteClient(upstream))
        ) as client:
            call = await client.call_tool(
                "get_website_data",
                {
                    "operation": "GET /site/alleleFunction/{geneId}",
                    "path_parameters": {"geneId": "PA128"},
                    "pointer": "/alleles",
                },
            )
            assert json.loads(call.structured_content["results"][0]["data"]["text"])["name"] == "*2"
            assert call.structured_content["_meta"]["data_source"] == "website"
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_unconfigured_data_tool_returns_typed_error(tmp_path):
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(create_mcp(content_store=store)) as client:
            call = await client.call_tool(
                "get_api_data",
                {"operation": "GET /data/gene/{id}", "path_parameters": {"id": "PA124"}},
                raise_on_error=False,
            )
            assert call.is_error
            assert call.structured_content["error_code"] == "upstream_unavailable"
    finally:
        store.close()
