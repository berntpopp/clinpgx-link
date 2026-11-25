"""The public schema tool exposes the actual registries without network access."""

import base64
import json
import re

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


# ponytail: copy of genefoundry-router scripts/surface.py lead_states_return (D1, Tool-Schema
# Documentation v1.1); re-vendor byte-identically if this repo ever vendors surface.py.
_LEAD_VERBS = (  # noqa: SIM905 - kept as in router surface.py
    "get fetch retrieve list look lookup search find resolve report read predict annotate "
    "compute calculate count summarize summarise map translate convert describe show check "
    "validate classify score rank query browse discover explain compare diff normalize normalise "
    "expand evaluate assess enumerate identify locate export download match suggest recommend "
    "inspect generate render estimate"
).split()
_VERB_LEAD = re.compile(
    rf"^(?:use this (?:tool )?to )?(?:{'|'.join(_LEAD_VERBS)})(?:s|es|ies)?\b", re.IGNORECASE
)
_RETURNS = re.compile(
    r"(?<!not )(?<!n't )(?<!n\u2019t )(?<!never )\breturn(?:s|ed|ing)?\b(?!\s*[~<\d])|→|->",
    re.IGNORECASE,
)
_FIELD_LIST = re.compile(r"\w+\W+\w+[^:]*:\s+[^.;:]+,")
_SENTENCE_END = re.compile(r"(?<!e\.g\.)(?<!i\.e\.)(?<=[.!?])\s+")


def _lead_states_return(description: str | None) -> bool:
    text = " ".join((description or "").split())
    lead = _SENTENCE_END.split(text, maxsplit=1)[0][:300]
    return bool(_VERB_LEAD.match(lead) or _RETURNS.search(lead) or _FIELD_LIST.search(lead))


@pytest.mark.asyncio
async def test_every_tool_description_leads_with_what_it_returns(server):
    """D1: the first sentence says what the tool returns, before usage advice."""
    async with Client(server) as client:
        tools = await client.list_tools()
    assert tools
    assert [t.name for t in tools if not _lead_states_return(t.description)] == []
    assert not _lead_states_return("Use known IDs for live reports or joins.")


@pytest.mark.asyncio
async def test_every_array_property_carries_an_example(server):
    """S3: without an example a model cannot tell a list from a scalar."""
    async with Client(server) as client:
        tools = await client.list_tools()
    missing = [
        f"{tool.name}.{name}"
        for tool in tools
        for name, prop in tool.inputSchema.get("properties", {}).items()
        if '"type": "array"' in json.dumps(prop) and not prop.get("examples")
    ]
    assert missing == []
