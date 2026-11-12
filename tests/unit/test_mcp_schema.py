"""The public schema tool exposes the actual registries without network access."""

import base64
import json

import pytest
from fastmcp import Client

from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp.facade import create_mcp


@pytest.fixture
def server(tmp_path):
    store = ContentStore(tmp_path / "content.sqlite")
    yield create_mcp(content_store=store)
    store.close()


@pytest.mark.asyncio
async def test_all_registry_operations_are_discoverable_once_over_pages(server):
    async with Client(server) as client:
        ids = []
        cursor = None
        while True:
            args = {"limit": 17}
            if cursor:
                args["cursor"] = cursor
            call = await client.call_tool("get_api_schema", args)
            envelope = call.structured_content
            assert json.loads(call.content[0].text) == envelope
            ids.extend(row["operation"] for row in envelope["results"])
            page = envelope["_meta"]["pagination"]
            assert page["total"] == 110
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert len(ids) == len(set(ids)) == 110
        assert "GET /data/gene/{id}" in ids
        assert "GET /site/gene/{id}" in ids
        assert "CPIC GET /allele_definition" in ids


@pytest.mark.asyncio
async def test_schema_detail_is_fenced_and_original_retained(server):
    async with Client(server) as client:
        call = await client.call_tool(
            "get_api_schema", {"operation": "GET /data/gene/{id}", "namespace": "api"}
        )
        result = call.structured_content["result"]
        schema = json.loads(result["schema"]["text"])
        assert schema["path"] == "/data/gene/{id}"
        assert schema["representations"] == ["json", "jsonld"]
        chunks = []
        start = 0
        while True:
            content = await client.call_tool(
                "get_source_content",
                {"content_ref": result["content_ref"], "representation": "base64", "start": start},
            )
            part = content.structured_content["result"]
            chunks.append(base64.b64decode(part["base64"]))
            if not part["has_more"]:
                break
            start = part["next_start"]
        assert json.loads(b"".join(chunks)) == schema


@pytest.mark.asyncio
async def test_schema_cursor_rejects_changed_namespace_and_unknown_operation(server):
    async with Client(server) as client:
        first = await client.call_tool("get_api_schema", {"limit": 1})
        cursor = first.structured_content["_meta"]["pagination"]["next_cursor"]
        for args in (
            {"namespace": "website", "cursor": cursor},
            {"operation": "GET /evil/SECRET"},
            {"operation": "GET /site/gene/{id}", "namespace": "api"},
        ):
            call = await client.call_tool("get_api_schema", args, raise_on_error=False)
            assert call.is_error
            assert call.structured_content["error_code"] == "invalid_input"
            assert "SECRET" not in call.content[0].text


@pytest.mark.asyncio
async def test_known_broken_vip_route_exposes_working_fallback_contract(server):
    async with Client(server) as client:
        call = await client.call_tool("get_api_schema", {"operation": "GET /data/vip/{id}"})
        result = call.structured_content["result"]
        assert result["callable"] is False
        assert result["availability"] == "documented_broken"
        assert result["fallback_operation"] == "GET /site/vip/{id}"


@pytest.mark.asyncio
async def test_every_null_default_is_accepted_by_its_advertised_schema(server):
    """Contract-Truth: proxies fill advertised defaults, so `default: null` must validate null."""
    from jsonschema import Draft202012Validator

    async with Client(server) as client:
        tools = await client.list_tools()
    checked, broken = 0, []
    for tool in tools:
        schema = tool.input_schema
        for name, prop in schema.get("properties", {}).items():
            if "default" in prop and prop["default"] is None:
                checked += 1
                sub = {"$defs": schema.get("$defs", {}), **prop}
                if not Draft202012Validator(sub).is_valid(None):
                    broken.append(f"{tool.name}.{name}")
    assert checked, "no null defaults found: the guard is walking nothing"
    assert broken == []
