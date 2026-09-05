"""Real source adapters through FastMCP, with only external HTTP replaced."""

import base64
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
            assert first.structured_content["results"][0]["source_pointer"]["text"] == "/data/0"
            assert second.structured_content["results"][0]["source_pointer"]["text"] == "/data/1"
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
            assert call.structured_content["results"][0]["source_pointer"]["text"] == "/alleles/0"
            reference = call.structured_content["results"][0]["content_ref"]
            content = await client.call_tool(
                "get_source_content",
                {"content_ref": reference, "pointer": "/alleles/0/name", "representation": "text"},
            )
            assert content.structured_content["result"]["text"]["text"] == "*2"
            original = await client.call_tool(
                "get_source_content", {"content_ref": reference, "representation": "base64"}
            )
            assert base64.b64decode(original.structured_content["result"]["base64"]) == (
                b'{"alleles":[{"name":"*2","function":"Normal function"}]}'
            )
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


@pytest.mark.asyncio
async def test_deferred_nested_row_recovery_selects_exact_original_record(tmp_path):
    def handle(request):
        assert request.url.path == "/v1/data/gene/PA124"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "a/b": [{"id": "PA1", "evidence": "x" * 148743}],
                },
            },
        )

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(
            create_mcp(content_store=store, api_service=ApiService(upstream))
        ) as client:
            call = await client.call_tool(
                "get_api_data",
                {
                    "operation": "GET /data/gene/{id}",
                    "path_parameters": {"id": "PA124"},
                    "pointer": "/a~1b",
                },
            )
            row = call.structured_content["results"][0]
            assert row["deferred_content"] is True
            assert row["fallback_args"]["pointer"] == "/data/a~1b/0"
            recovered = await client.call_tool(row["fallback_tool"], row["fallback_args"])
            descriptors = recovered.structured_content["result"]["items"]
            evidence = next(item for item in descriptors if item["key"]["text"] == "evidence")
            text = await client.call_tool(
                "get_source_content",
                {
                    "content_ref": row["content_ref"],
                    "pointer": evidence["pointer"]["text"],
                    "representation": "text",
                    "length": 8192,
                },
            )
            assert text.structured_content["result"]["text"]["text"] == "x" * 8192
            assert text.structured_content["result"]["total"] == 148743
    finally:
        await upstream.close()
