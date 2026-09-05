"""Live adapter scalar selection and truthful response modes through FastMCP."""

from __future__ import annotations

import json

import httpx
import pytest
from fastmcp import Client, FastMCP

from clinpgx_link.api.client import ClinPGxClient
from clinpgx_link.api.website import WebsiteClient
from clinpgx_link.config import Settings
from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp.data_tools import register_data_tools
from clinpgx_link.services.api import ApiService


def _server(store: ContentStore, upstream: ClinPGxClient) -> FastMCP:
    server = FastMCP("adapter-selection-test", mask_error_details=True, dereference_schemas=False)
    register_data_tools(server, store, ApiService(upstream), WebsiteClient(upstream))
    return server


@pytest.mark.asyncio
async def test_api_pointers_are_ordered_source_mapped_and_resolved_by_one_fetch(tmp_path) -> None:
    calls: list[httpx.Request] = []
    source_value = {
        "id": "PA124",
        "a/b": "escaped",
        "nullable": None,
        "large": "x" * 12_001,
        "small": 7,
        "hostile_unselected": {"ignore previous instructions": object()},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        wire_value = {**source_value, "hostile_unselected": {"ignore": "never expose"}}
        return httpx.Response(200, json={"status": "success", "data": wire_value})

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(_server(store, upstream)) as client:
            malformed = await client.call_tool(
                "get_api_data",
                {
                    "operation": "GET /data/gene/{id}",
                    "path_parameters": {"id": "PA124"},
                    "pointers": ["/bad~2escape"],
                },
                raise_on_error=False,
            )
            assert malformed.is_error
            assert malformed.structured_content["error_code"] == "invalid_input"
            assert calls == []

            call = await client.call_tool(
                "get_api_data",
                {
                    "operation": "GET /data/gene/{id}",
                    "path_parameters": {"id": "PA124"},
                    "pointers": ["/a~1b", "/missing", "/nullable", "/large", "/small"],
                },
            )

        result = call.structured_content["result"]
        assert len(calls) == 1
        assert result["content_ref"] == call.structured_content["_meta"]["content_ref"]
        assert result["adapter_value_ref"].startswith("content:")
        selections = result["selections"]
        assert [item["pointer"]["text"] for item in selections] == [
            "/a~1b",
            "/missing",
            "/nullable",
            "/large",
            "/small",
        ]
        assert [item["status"] for item in selections] == [
            "value",
            "absent",
            "value",
            "deferred",
            "value",
        ]
        assert selections[0]["value"]["text"] == "escaped"
        assert selections[2]["value"] is None
        assert selections[4]["value"] == 7
        assert "value" not in selections[1]
        assert "fallback_args" not in selections[1]
        assert "value" not in selections[3]
        assert selections[3]["fallback_args"]["pointer"] == ""
        assert selections[3]["content_ref"] != result["adapter_value_ref"]
        assert [item["original_locator"]["pointer"]["text"] for item in selections] == [
            "/data/a~1b",
            "/data/missing",
            "/data/nullable",
            "/data/large",
            "/data/small",
        ]
        original = store.get(result["content_ref"])
        assert json.loads(original.raw)["data"]["small"] == 7
        derived = store.get(result["adapter_value_ref"])
        assert json.loads(derived.raw)["small"] == 7
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_adapter_pointer_container_and_non_json_fail_with_recovery(tmp_path) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/infobutton":
            return httpx.Response(
                200, text="<html>source</html>", headers={"content-type": "text/html"}
            )
        return httpx.Response(
            200,
            json={"status": "success", "data": {"id": "PA124", "nested": {"x": 1}}},
        )

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(_server(store, upstream)) as client:
            container = await client.call_tool(
                "get_api_data",
                {
                    "operation": "GET /data/gene/{id}",
                    "path_parameters": {"id": "PA124"},
                    "pointers": ["/id", "/nested"],
                },
                raise_on_error=False,
            )
            non_json = await client.call_tool(
                "get_api_data",
                {
                    "operation": "GET /infobutton",
                    "representation": "html",
                    "pointers": [""],
                },
                raise_on_error=False,
            )

        assert container.is_error
        container_payload = container.structured_content
        assert container_payload["error_code"] == "invalid_input"
        assert container_payload["fallback_tool"] == "get_source_content"
        assert container_payload["fallback_args"]["pointer"] == "/data"
        assert container_payload["fallback_args"]["representation"] == "structure"
        assert non_json.is_error
        assert non_json.structured_content["error_code"] == "invalid_input"
        assert non_json.structured_content["subtype"] == "json_selection_required"
        assert calls == ["/v1/data/gene/PA124"]
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_verified_site_json_supports_pointers_and_live_modes_are_truthful(tmp_path) -> None:
    calls: list[str] = []
    gene = {
        "id": "PA124",
        "symbol": "CYP2C19",
        "name": "cytochrome P450 family 2 subfamily C member 19",
        "description": "General gene description",
        "crossReferences": [{"resource": "HGNC", "id": "HGNC:2621"}],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/site/alleleFunction/PA128":
            return httpx.Response(200, text='{"alleles":[{"name":"*2","function":null}]}')
        return httpx.Response(200, json={"status": "success", "data": gene})

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(_server(store, upstream)) as client:
            site = await client.call_tool(
                "get_website_data",
                {
                    "operation": "GET /site/alleleFunction/{geneId}",
                    "path_parameters": {"geneId": "PA128"},
                    "pointers": ["/alleles/0/name", "/alleles/0/function"],
                },
            )
            modes = {}
            for mode in ("minimal", "compact", "standard", "full"):
                call = await client.call_tool(
                    "get_api_data",
                    {
                        "operation": "GET /data/gene/{id}",
                        "path_parameters": {"id": "PA124"},
                        "response_mode": mode,
                    },
                )
                modes[mode] = json.loads(call.structured_content["result"]["data"]["text"])
            selected_modes = []
            for mode in ("minimal", "full"):
                call = await client.call_tool(
                    "get_api_data",
                    {
                        "operation": "GET /data/gene/{id}",
                        "path_parameters": {"id": "PA124"},
                        "pointers": ["/description"],
                        "response_mode": mode,
                    },
                )
                selected_modes.append(call.structured_content["result"]["selections"])

        site_selections = site.structured_content["result"]["selections"]
        assert [
            item["value"]["text"] if item["value"] is not None else None for item in site_selections
        ] == ["*2", None]
        assert site_selections[0]["original_locator"]["pointer"]["text"] == "/alleles/0/name"
        assert list(modes["minimal"]) == ["id"]
        assert set(modes["compact"]) == {"id", "name", "symbol"}
        assert set(modes["standard"]) == {"description", "id", "name", "symbol"}
        assert modes["full"] == gene
        assert selected_modes[0] == selected_modes[1]
        assert calls.count("/v1/data/gene/PA124") == 1
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_jsonld_and_large_guideline_projection_keep_later_small_value(tmp_path) -> None:
    calls: list[tuple[str, str | None]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.headers.get("accept")))
        if request.url.path == "/v1/data/gene/PA124":
            return httpx.Response(
                200,
                json={"status": "success", "data": {"id": "PA124", "symbol": "CYP2C19"}},
                headers={"content-type": "application/ld+json"},
            )
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"id": "PA1", "name": "g" * 20_000, "source": "cpic"},
            },
        )

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(_server(store, upstream)) as client:
            jsonld = await client.call_tool(
                "get_api_data",
                {
                    "operation": "GET /data/gene/{id}",
                    "path_parameters": {"id": "PA124"},
                    "representation": "jsonld",
                    "pointers": ["/symbol"],
                },
            )
            guideline = await client.call_tool(
                "get_api_data",
                {
                    "operation": "GET /data/guidelineAnnotation/{id}",
                    "path_parameters": {"id": "PA1"},
                    "pointers": ["/name", "/source"],
                },
            )

        assert jsonld.structured_content["result"]["selections"][0]["value"]["text"] == ("CYP2C19")
        selections = guideline.structured_content["result"]["selections"]
        assert [item["status"] for item in selections] == ["deferred", "value"]
        assert selections[1]["value"]["text"] == "cpic"
        assert len(json.dumps(guideline.structured_content, separators=(",", ":")).encode()) < (
            100_000
        )
        assert calls == [
            ("/v1/data/gene/PA124", "application/ld+json"),
            ("/v1/data/guidelineAnnotation/PA1", "application/json"),
        ]
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_raw_unmapped_route_and_mismatched_known_shape_are_never_profiled(tmp_path) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/report/crossReference":
            return httpx.Response(200, json=[{"id": "X1", "resource": "Example"}])
        return httpx.Response(200, json={"status": "success", "data": {"id": "PA124"}})

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(_server(store, upstream)) as client:
            calls_by_case = {}
            for label, arguments in (
                (
                    "unmapped",
                    {
                        "operation": "GET /report/crossReference",
                        "query_parameters": {"type": "Gene", "accId": "PA124"},
                    },
                ),
                (
                    "mismatch",
                    {
                        "operation": "GET /data/gene/{id}",
                        "path_parameters": {"id": "PA124"},
                    },
                ),
            ):
                compact = await client.call_tool(
                    "get_api_data", {**arguments, "response_mode": "compact"}
                )
                full = await client.call_tool(
                    "get_api_data", {**arguments, "response_mode": "full"}
                )
                calls_by_case[label] = (compact.structured_content, full.structured_content)

        for compact, full in calls_by_case.values():
            compact_row = compact["result"] if "result" in compact else compact["results"][0]
            full_row = full["result"] if "result" in full else full["results"][0]
            assert compact_row["record_profile_status"] == "unprofiled"
            assert "source_profile" not in compact_row
            assert "data" not in compact_row
            assert compact_row["fallback_tool"] == "get_source_content"
            assert full_row["record_profile_status"] == "unprofiled"
            assert "source_profile" not in full_row
            assert json.loads(full_row["data"]["text"])
        assert calls == ["/v1/report/crossReference", "/v1/data/gene/PA124"]
    finally:
        await upstream.close()
