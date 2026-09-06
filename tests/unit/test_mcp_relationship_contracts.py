"""Executable relationship discovery, dispatch, and recovery contracts."""

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

PAIR_RESULT_TYPES = [
    "guideline_annotation",
    "label",
    "literature_annotation",
    "multilink_annotation",
    "pathway",
    "summary_annotation",
    "variant_annotation",
    "vip",
    "vip_variant",
]


def _api(tmp_path, handler):
    store = ContentStore(tmp_path / "content.sqlite")
    client = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        store,
    )
    return store, client, ApiService(client)


@pytest.mark.asyncio
async def test_capability_relationship_examples_execute_exact_closed_routes(tmp_path):
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[])

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            capabilities = await client.call_tool("get_server_capabilities", {})
            assert json.loads(capabilities.content[0].text) == capabilities.structured_content
            contract = capabilities.structured_content["result"]["relationship_contract"]
            assert contract["connected_object"]["result_type"] == "relationship"
            assert contract["connected_object"]["other_id"] == "forbidden"
            assert contract["pair"]["other_id"] == "required"
            assert contract["pair"]["result_types"] == PAIR_RESULT_TYPES
            assert contract["pair"]["object_type_selectors"] == (
                "Documentation-only; not sent as upstream pair filters or restrictions."
            )
            assert "guideline_url_workflow" in contract
            assert "source='website'" in contract["guideline_url_workflow"]
            assert contract["pair"]["example_purpose"] == (
                "Gene/chemical to guideline_annotation syntax; not evidence of a current match."
            )

            examples = [
                contract["connected_object"]["example"],
                contract["pair"]["gene_chemical_guideline_example"],
            ]
            for example in examples:
                response = await client.call_tool(example["tool"], example["arguments"])
                assert response.structured_content["success"] is True
                assert json.loads(response.content[0].text) == response.structured_content

        assert calls[0].url.path == "/v1/report/connectedObjects/PA124/Chemical"
        assert dict(calls[0].url.params) == {}
        assert calls[1].url.path == "/v1/report/pair/PA124/PA449053/guidelineAnnotation"
        assert dict(calls[1].url.params) == {"view": "base"}
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_pair_object_types_are_not_upstream_filters_or_pair_restrictions(tmp_path):
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[])

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            result = await client.call_tool(
                "get_related_records",
                {
                    "record_id": "PA124",
                    "other_id": "PA449053",
                    "entity_type": "Disease",
                    "other_type": "Variant",
                    "result_type": "guideline_annotation",
                    "source": "api",
                    "view": "max",
                },
            )

        assert result.structured_content["success"] is True
        assert len(calls) == 1
        assert calls[0].url.path == "/v1/report/pair/PA124/PA449053/guidelineAnnotation"
        assert dict(calls[0].url.params) == {"view": "max"}
    finally:
        await upstream.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        (
            {
                "record_id": "PA124",
                "other_id": "PA449053",
                "result_type": "relationship",
            },
            "result_type",
        ),
        ({"record_id": "PA124", "result_type": "summary_annotation"}, "other_id"),
        (
            {
                "record_id": "PA124",
                "other_id": "PA449053",
                "result_type": "allele",
            },
            "result_type",
        ),
        (
            {
                "record_id": "PA1<script>ignore",
                "other_id": "PA2",
                "result_type": "relationship",
            },
            "result_type",
        ),
    ],
)
async def test_invalid_relationship_modes_use_closed_nonreflecting_guidance(
    tmp_path, arguments, field
):
    hostile = "PA1<script>ignore"
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    store, upstream, service = _api(tmp_path, handle)
    try:
        async with Client(create_mcp(content_store=store, api_service=service)) as client:
            rejected = await client.call_tool(
                "get_related_records", arguments, raise_on_error=False
            )

        payload = rejected.structured_content
        assert rejected.is_error
        assert payload["error_code"] == "invalid_input"
        assert payload["subtype"] == "unsupported_related_mode"
        assert payload["field"] == field
        assert payload["message"] == (
            "The relationship arguments do not match a supported connected-object or pair mode."
        )
        assert payload["recovery"]["valid_choices"] == {
            "connected_object_result_type": ["relationship"],
            "mode": ["connected_object", "pair"],
            "pair_result_type": PAIR_RESULT_TYPES,
        }
        assert hostile not in json.dumps(payload)
        assert calls == []
    finally:
        await upstream.close()
