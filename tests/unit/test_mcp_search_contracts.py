"""Executable MCP search contracts and closed recovery guidance."""

from __future__ import annotations

import json
from copy import deepcopy

import httpx
import pytest
from fastmcp import Client

from clinpgx_link.api.client import ClinPGxClient
from clinpgx_link.config import Settings
from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp import search_contracts
from clinpgx_link.mcp.facade import create_mcp
from clinpgx_link.mcp.search_contracts import capabilities_payload
from clinpgx_link.services.api import ApiService

API_CASES = {
    "pathway": ({"id": "PA154424674"}, "/v1/data/pathway", {"accessionId": "PA154424674"}),
    "gene": ({"gene": "CYP2C19"}, "/v1/data/gene", {"symbol": "CYP2C19"}),
    "chemical": (
        {"chemical": "clopidogrel"},
        "/v1/data/chemical",
        {"name": "clopidogrel"},
    ),
    "disease": ({"id": "PA999999"}, "/v1/data/disease", {"accessionId": "PA999999"}),
    "variant": ({"variant": "rs4244285"}, "/v1/data/variant/", {"symbol": "rs4244285"}),
    "literature": ({"id": "15178564"}, "/v1/data/literature", {"id": "15178564"}),
    "guideline_annotation": (
        {"source": "CPIC"},
        "/v1/data/guidelineAnnotation",
        {"source": "cpic"},
    ),
    "label": (
        {"gene": "DPYD", "chemical": "fluorouracil"},
        "/v1/data/label",
        {"relatedGenes.symbol": "DPYD", "relatedChemicals.name": "fluorouracil"},
    ),
    "summary_annotation": (
        {"gene": "CYP2C19", "chemical": "clopidogrel"},
        "/v1/data/summaryAnnotation",
        {
            "location.genes.symbol": "CYP2C19",
            "relatedChemicals.name": "clopidogrel",
        },
    ),
    "variant_annotation": (
        {"variant": "rs4149056"},
        "/v1/data/variantAnnotation",
        {"location.fingerprint": "rs4149056"},
    ),
}


def _api(tmp_path, handler):
    store = ContentStore(tmp_path / "content.sqlite")
    client = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        store,
    )
    return store, client, ApiService(client)


@pytest.mark.asyncio
async def test_capability_examples_execute_through_captured_api_routes(tmp_path):
    expected = dict(API_CASES)
    calls: list[httpx.Request] = []
    modes = ("minimal", "compact", "standard", "full")

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"status": "success", "data": []})

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            capability_tool = next(
                tool for tool in await client.list_tools() if tool.name == "get_server_capabilities"
            )
            assert "api_contract_defaults" in capability_tool.description
            assert "full" in capability_tool.description

            canonical = capabilities_payload()
            results = {}
            outer_keys = None
            stable_result = None
            for mode in modes:
                capabilities = await client.call_tool(
                    "get_server_capabilities", {"response_mode": mode}
                )
                payload = capabilities.structured_content
                result_payload = payload["result"]
                results[mode] = result_payload
                assert result_payload["response_mode"] == mode
                assert payload["success"] is True
                assert json.loads(capabilities.content[0].text) == payload
                if outer_keys is None:
                    outer_keys = set(payload)
                assert set(payload) == outer_keys

                current_stable = {
                    key: value
                    for key, value in result_payload.items()
                    if key not in {"response_mode", "search_contracts"}
                }
                if stable_result is None:
                    stable_result = current_stable
                assert current_stable == stable_result

                contracts = result_payload["search_contracts"]
                if mode == "full":
                    assert contracts == canonical
                    api_contracts = contracts["api"]
                else:
                    assert contracts["api_contract_defaults"] == {
                        "applies_to": "Every API row unless that row overrides the field.",
                        "source": "api",
                        "semantics": (
                            "All supplied canonical exact filters are combined with AND semantics."
                        ),
                        "example_purpose": (
                            "Request syntax and route binding; not evidence of a match."
                        ),
                    }
                    defaults = {
                        key: contracts["api_contract_defaults"][key]
                        for key in ("source", "semantics", "example_purpose")
                    }
                    assert all(
                        not set(defaults) & set(contract) for contract in contracts["api"].values()
                    )
                    api_contracts = {
                        entity_type: {**defaults, **contract}
                        for entity_type, contract in contracts["api"].items()
                    }
                    assert api_contracts == canonical["api"]
                    assert contracts["download"] == canonical["download"]

                assert set(api_contracts) == set(expected)
                assert api_contracts["guideline_annotation"]["filter_values"] == {
                    "source": ["cpic", "dpwg", "pro"]
                }
                assert api_contracts["label"]["filter_values"] == {
                    "source": ["ema", "fda", "hcsc", "pmda"]
                }
                assert "membership" in contracts["download"]["semantics"]
                assert "entity-family identity" not in contracts["download"]["semantics"]

                for entity_type, contract in api_contracts.items():
                    filters, expected_path, expected_filters = expected[entity_type]
                    assert contract["source"] == "api"
                    assert contract["operation"] == "GET " + expected_path.removeprefix("/v1")
                    assert contract["filters"] == sorted(contract["filter_mapping"])
                    assert contract["example_purpose"].endswith("not evidence of a match.")
                    assert contract["example"] == {
                        "tool": "search_records",
                        "arguments": {
                            "entity_type": entity_type,
                            "filters": filters,
                            "source": "api",
                        },
                    }
                    call = await client.call_tool(
                        contract["example"]["tool"], contract["example"]["arguments"]
                    )
                    assert call.structured_content["success"] is True
                    if mode == "minimal":
                        request = calls[-1]
                        assert request.url.path == expected_path
                        assert dict(request.url.params) == {**expected_filters, "view": "base"}

            assert len(calls) == len(expected)
            full_serialized = json.dumps(
                results["full"],
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            compact_serialized = json.dumps(
                results["compact"],
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            assert len(full_serialized) - len(compact_serialized) >= 1_000
            assert len(full_serialized) <= 100_000
            assert (len(full_serialized) + 3) // 4 <= 25_000
    finally:
        await upstream.close()


def test_compact_capability_projection_preserves_heterogeneous_and_missing_fields():
    payload = {
        "api": {
            "one": {
                "source": "api",
                "semantics": "shared semantics",
                "example_purpose": "row-only purpose",
                "operation": "GET /one",
                "nested": {"values": ["one"]},
            },
            "two": {
                "source": "website",
                "semantics": "shared semantics",
                "operation": "GET /two",
                "nested": {"values": ["two"]},
            },
        },
        "download": {"entities": ["gene"]},
    }
    original = deepcopy(payload)

    projected = search_contracts.compact_capabilities_payload(payload)

    assert payload == original
    assert projected["api_contract_defaults"] == {
        "applies_to": "Every API row unless that row overrides the field.",
        "semantics": "shared semantics",
    }
    assert projected["api"]["one"]["source"] == "api"
    assert projected["api"]["two"]["source"] == "website"
    assert projected["api"]["one"]["example_purpose"] == "row-only purpose"
    assert "example_purpose" not in projected["api"]["two"]
    assert "semantics" not in projected["api"]["one"]
    assert "semantics" not in projected["api"]["two"]
    projected["api"]["one"]["nested"]["values"].append("changed")
    assert payload == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity_type", "source_value", "source_policy", "choices"),
    [
        ("guideline_annotation", "fda", "api", ["cpic", "dpwg", "pro"]),
        ("label", "CPIC", "api", ["ema", "fda", "hcsc", "pmda"]),
        ("guideline_annotation", "not-a-source", "auto", ["cpic", "dpwg", "pro"]),
        (
            "label",
            "IGNORE_PREVIOUS_<script>source-secret",
            "api",
            ["ema", "fda", "hcsc", "pmda"],
        ),
    ],
)
async def test_api_source_values_are_route_scoped_and_non_reflecting(
    tmp_path, entity_type, source_value, source_policy, choices
):
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            rejected = await client.call_tool(
                "search_records",
                {
                    "entity_type": entity_type,
                    "filters": {"source": source_value},
                    "source": source_policy,
                },
                raise_on_error=False,
            )
            payload = rejected.structured_content
            assert rejected.is_error
            assert payload["error_code"] == "invalid_input"
            assert payload["subtype"] == "unsupported_api_filter_value"
            assert payload["field"] == "filters"
            assert payload["recovery_action"] == "unsupported_api_filters"
            assert "route-scoped values" in payload["recovery"]["limitation"]
            assert payload["recovery"]["valid_choices"]["source"] == choices
            assert source_value not in json.dumps(payload)
            assert calls == []
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_declared_source_value_accepts_case_insensitive_spelling(tmp_path):
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"status": "success", "data": []})

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            result = await client.call_tool(
                "search_records",
                {
                    "entity_type": "guideline_annotation",
                    "filters": {"source": "CPIC"},
                    "source": "api",
                },
            )
            assert result.structured_content["success"] is True
            assert len(calls) == 1
            assert calls[0].url.path == "/v1/data/guidelineAnnotation"
            assert dict(calls[0].url.params) == {"source": "cpic", "view": "base"}
    finally:
        await upstream.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity_type", "filters", "choices"),
    [
        ("gene", {"name": "CYP2C19"}, ["gene", "id"]),
        ("chemical", {"name": "clopidogrel"}, ["chemical", "id"]),
        ("variant", {"name": "rs4244285"}, ["variant"]),
        ("guideline_annotation", {"gene": "CYP2C19", "chemical": "clopidogrel"}, ["source"]),
        ("guideline_annotation", {"gene": "TPMT"}, ["source"]),
    ],
)
async def test_known_api_filter_mismatches_have_fixed_nonexecuting_recovery(
    tmp_path, entity_type, filters, choices
):
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            rejected = await client.call_tool(
                "search_records",
                {"entity_type": entity_type, "filters": filters, "source": "api"},
                raise_on_error=False,
            )
            payload = rejected.structured_content
            assert rejected.is_error
            assert payload["error_code"] == "invalid_input"
            assert payload["subtype"] == "unsupported_api_filters"
            assert payload["recovery"]["valid_choices"]["filters"] == choices
            assert all(
                command["tool"] == "get_server_capabilities"
                for command in payload["recovery"]["next_commands"]
            )
            assert not any(
                command["tool"] == "search_records"
                and command["arguments"].get("source") == "download"
                for command in payload["recovery"]["next_commands"]
            )
            assert calls == []
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_guideline_name_filter_recovery_publishes_resolution_pair_workflow(tmp_path):
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(create_mcp(content_store=store)) as client:
            rejected = await client.call_tool(
                "search_records",
                {
                    "entity_type": "guideline_annotation",
                    "filters": {"gene": "DPYD", "chemical": "capecitabine"},
                    "source": "api",
                },
                raise_on_error=False,
            )
            workflow = rejected.structured_content["recovery"]["resolution_workflow"]
            assert [step["tool"] for step in workflow] == [
                "search_records",
                "search_records",
                "get_related_records",
            ]
            assert workflow[0]["arguments_template"]["filters"] == {"gene": "{gene}"}
            assert workflow[1]["arguments_template"]["filters"] == {"chemical": "{chemical}"}
            assert workflow[2]["arguments_template"]["record_id"] == "{returned_gene_id}"
            assert workflow[2]["arguments_template"]["other_id"] == "{returned_chemical_id}"
            assert "PA124" not in json.dumps(workflow)
            assert "PA449053" not in json.dumps(workflow)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_auto_reports_api_filter_mismatch_when_no_local_membership_exists(tmp_path):
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(create_mcp(content_store=store)) as client:
            rejected = await client.call_tool(
                "search_records",
                {"entity_type": "guideline_annotation", "filters": {"gene": "TPMT"}},
                raise_on_error=False,
            )
            assert rejected.structured_content["subtype"] == "unsupported_api_filters"
            assert rejected.structured_content["recovery"]["valid_choices"]["filters"] == ["source"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_hostile_known_filter_value_is_not_reflected(tmp_path):
    hostile = "IGNORE_PREVIOUS_<script>source-secret"
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(create_mcp(content_store=store)) as client:
            rejected = await client.call_tool(
                "search_records",
                {
                    "entity_type": "gene",
                    "filters": {"name": hostile},
                    "source": "api",
                },
                raise_on_error=False,
            )
            assert hostile not in json.dumps(rejected.structured_content)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_summary_annotation_aliases_deduplicate_or_reject_without_dropping_predicate(
    tmp_path,
):
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"status": "success", "data": []})

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            conflict = await client.call_tool(
                "search_records",
                {
                    "entity_type": "summary_annotation",
                    "filters": {"id": "123", "annotation_id": "456"},
                    "source": "api",
                },
                raise_on_error=False,
            )
            assert conflict.is_error
            assert conflict.structured_content["error_code"] == "invalid_input"
            assert conflict.structured_content["subtype"] == "conflicting_api_filters"
            assert conflict.structured_content["field"] == "filters"
            assert calls == []

            equal = await client.call_tool(
                "search_records",
                {
                    "entity_type": "summary_annotation",
                    "filters": {"id": "123", "annotation_id": "123"},
                    "source": "api",
                },
            )
            assert equal.structured_content["success"] is True
            assert dict(calls[0].url.params) == {"id": "123", "view": "base"}
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_genuinely_unsupported_api_entity_has_distinct_recovery(tmp_path):
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(create_mcp(content_store=store)) as client:
            rejected = await client.call_tool(
                "search_records",
                {"entity_type": "ontology_term", "filters": {"id": "PA1"}, "source": "api"},
                raise_on_error=False,
            )
            assert rejected.structured_content["subtype"] == "unsupported_search_source"
            assert rejected.structured_content["recovery_action"] == "unsupported_search_source"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_record_tool_schema_budget_remains_bounded(tmp_path):
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        tools = await create_mcp(content_store=store).list_tools()
        encoded: list[bytes] = []
        for tool in tools:
            item = json.dumps(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.parameters,
                },
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            encoded.append(item)
            assert (len(item) + 3) // 4 <= 1_200
        assert (sum(map(len, encoded)) + 3) // 4 <= 10_000
    finally:
        store.close()
