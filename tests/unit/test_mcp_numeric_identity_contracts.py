"""Actionable numeric identity contracts at the MCP boundary."""

from __future__ import annotations

import json

import httpx
import pytest
from fastmcp import Client

from clinpgx_link.api.client import ClinPGxClient
from clinpgx_link.config import Settings
from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp.facade import create_mcp
from clinpgx_link.services.api import ApiService

OBSERVED_LITERATURE_BODY_SHA256 = "97abd1e42c9fa645890cb5e945b95f16a81b3af1966160399ec5e1bcb5395621"


def _api(tmp_path, handler):
    store = ContentStore(tmp_path / "content.sqlite")
    client = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        store,
    )
    return store, client, ApiService(client)


@pytest.mark.asyncio
async def test_registry_valid_32_digit_numeric_detail_id_reaches_upstream(tmp_path):
    record_id = "9" * 32
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404)

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            result = await client.call_tool(
                "get_record",
                {"entity_type": "literature", "record_id": record_id, "source": "api"},
                raise_on_error=False,
            )
        assert result.structured_content["error_code"] == "not_found"
        assert [request.url.path for request in calls] == [f"/v1/data/literature/{record_id}"]
    finally:
        await upstream.close()


def test_numeric_capabilities_take_discovery_filters_from_executable_contract() -> None:
    from clinpgx_link.identity_contracts import detail_identifier_capabilities

    requested: list[str] = []

    def executable_filters(entity_type: str) -> list[str]:
        requested.append(entity_type)
        return [f"derived-{entity_type}"]

    capabilities = detail_identifier_capabilities(executable_filters)

    assert requested == ["literature", "summary_annotation", "variant_annotation"]
    assert capabilities["literature"]["search_filters"] == ["derived-literature"]


@pytest.mark.asyncio
async def test_variant_annotation_search_exposes_validated_numeric_detail_continuation(tmp_path):
    calls: list[httpx.Request] = []
    row = {
        "id": 1454052260,
        "accessionId": "PA166399461",
        "objCls": "VariantDrugAnnotation",
        "sentence": "Source-authored annotation.",
        "literature": {
            "id": 15178564,
            "resourceId": "40297930",
            "title": "Source title",
            "type": "article",
        },
    }

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/data/variantAnnotation":
            return httpx.Response(200, json={"status": "success", "data": [row]})
        assert request.url.path == "/v1/data/variantAnnotation/1454052260"
        return httpx.Response(200, json={"status": "success", "data": row})

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            found = await client.call_tool(
                "search_records",
                {
                    "entity_type": "variant_annotation",
                    "filters": {"variant": "rs4149056"},
                    "source": "api",
                    "view": "max",
                    "response_mode": "minimal",
                },
            )
            result = found.structured_content["results"][0]
            assert result["source_profile"] == "variant_annotation"
            assert result["id"] == 1454052260
            assert json.loads(result["data"]["text"]) == {"id": 1454052260}
            assert result["detail_identifier"] == {
                "source_field": "id",
                "role": "internal_numeric_detail_id",
                "value": 1454052260,
            }
            command = result["next_commands"][0]
            assert command == {
                "tool": "get_record",
                "arguments": {
                    "entity_type": "variant_annotation",
                    "record_id": "1454052260",
                    "source": "api",
                    "view": "max",
                },
            }
            detail = await client.call_tool(command["tool"], command["arguments"])
            assert detail.structured_content["result"]["id"] == 1454052260
            assert [request.url.path for request in calls] == [
                "/v1/data/variantAnnotation",
                "/v1/data/variantAnnotation/1454052260",
            ]
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_numeric_profiles_validate_shape_and_keep_optional_accession_optional(tmp_path):
    rows = [
        {"id": 1454052260, "objCls": "VariantDrugAnnotation", "sentence": "Observed"},
        {"id": True, "accessionId": "PA1"},
        {"id": 1.5, "accessionId": "PA1"},
        {"id": -1, "accessionId": "PA1"},
        {"id": 2147483648, "accessionId": "PA1"},
        {"id": 7, "accessionId": 42},
    ]

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": rows})

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            compact = await client.call_tool(
                "search_records",
                {
                    "entity_type": "variant_annotation",
                    "filters": {"variant": "rs4149056"},
                    "source": "api",
                    "response_mode": "compact",
                },
            )
            full = await client.call_tool(
                "search_records",
                {
                    "entity_type": "variant_annotation",
                    "filters": {"variant": "rs4149056"},
                    "source": "api",
                    "response_mode": "full",
                },
            )

        active = compact.structured_content["results"][0]
        assert active["source_profile"] == "variant_annotation"
        assert json.loads(active["data"]["text"])["id"] == 1454052260
        assert "accessionId" not in json.loads(active["data"]["text"])
        for compact_row, full_row, original in zip(
            compact.structured_content["results"][1:],
            full.structured_content["results"][1:],
            rows[1:],
            strict=True,
        ):
            assert compact_row["record_profile_status"] == "unprofiled"
            assert "data" not in compact_row
            assert "detail_identifier" not in compact_row
            assert json.loads(full_row["data"]["text"]) == original
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_literature_profile_and_capabilities_distinguish_internal_id_from_pubmed(tmp_path):
    row = {
        "id": 15178564,
        "resourceId": "40297930",
        "title": "Source title",
        "type": "article",
    }

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": [row]})

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            found = await client.call_tool(
                "search_records",
                {
                    "entity_type": "literature",
                    "filters": {"resource_id": "40297930"},
                    "source": "api",
                },
            )
            capabilities = await client.call_tool("get_server_capabilities", {})

        data = json.loads(found.structured_content["results"][0]["data"]["text"])
        assert data == row
        contracts = capabilities.structured_content["result"]["detail_identifier_contracts"]
        literature = contracts["literature"]
        assert literature["source_field"] == "id"
        assert literature["role"] == "internal_numeric_detail_id"
        assert literature["argument_encoding"] == "decimal_string"
        assert literature["external_cross_references"] == [
            {
                "source_field": "resourceId",
                "role": "external_cross_reference",
                "detail_identifier": False,
            },
            {
                "source_field": "crossReferences[].resourceId",
                "role": "external_cross_reference",
                "detail_identifier": False,
            },
        ]
        assert literature["external_reference_note"] == (
            "PubMed resourceId is not the internal ClinPGx literature id."
        )
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_observed_literature_cross_reference_profile_is_active_and_strict(tmp_path):
    """Reduced shape from the retained body identified by OBSERVED_LITERATURE_BODY_SHA256."""
    assert len(OBSERVED_LITERATURE_BODY_SHA256) == 64
    valid = {
        "id": 15178564,
        "title": "Reduced source title",
        "type": "article",
        "crossReferences": [
            {
                "id": 123,
                "resource": "PubMed",
                "resourceId": "40297930",
                "_url": "https://pubmed.ncbi.nlm.nih.gov/40297930/",
            }
        ],
    }
    malformed = {
        **valid,
        "crossReferences": [{"id": True, "resource": "PubMed", "resourceId": 40297930}],
    }

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": [valid, malformed]})

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            result = await client.call_tool(
                "search_records",
                {"entity_type": "literature", "filters": {"id": "15178564"}},
            )
        active, drifted = result.structured_content["results"]
        assert active["source_profile"] == "literature"
        assert json.loads(active["data"]["text"])["crossReferences"] == valid["crossReferences"]
        assert drifted["record_profile_status"] == "unprofiled"
        assert "data" not in drifted
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_summary_annotation_profile_is_active_and_invalid_id_falls_back(tmp_path):
    valid = {
        "id": 1448100508,
        "levelOfEvidence": {"term": "1A"},
        "relatedChemicals": [{"id": "PA449053", "name": "clopidogrel"}],
    }
    invalid = {**valid, "id": 1.5}

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": [valid, invalid]})

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            result = await client.call_tool(
                "search_records",
                {
                    "entity_type": "summary_annotation",
                    "filters": {"id": "1448100508"},
                    "source": "api",
                },
            )
        active, drifted = result.structured_content["results"]
        assert active["source_profile"] == "summary_annotation"
        assert active["next_commands"][0]["arguments"]["record_id"] == "1448100508"
        assert json.loads(active["data"]["text"]) == valid
        assert drifted["record_profile_status"] == "unprofiled"
        assert "next_commands" not in drifted
    finally:
        await upstream.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_type", ["literature", "summary_annotation", "variant_annotation"])
async def test_numeric_detail_route_rejects_accession_with_closed_nonreflecting_guidance(
    tmp_path, entity_type
):
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            rejected = await client.call_tool(
                "get_record",
                {"entity_type": entity_type, "record_id": "PA166399461", "source": "api"},
                raise_on_error=False,
            )
        payload = rejected.structured_content
        assert payload["error_code"] == "invalid_input"
        assert payload["subtype"] == "numeric_detail_id_required"
        assert payload["recovery_action"] == "discover_numeric_detail_id"
        assert payload["recovery"]["valid_choices"]["filters"]
        assert payload["fallback_tool"] == "get_server_capabilities"
        assert "PA166399461" not in json.dumps(payload)
        assert calls == []
    finally:
        await upstream.close()
