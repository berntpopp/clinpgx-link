"""Public entity-oriented MCP tools over live and immutable sources."""

from __future__ import annotations

import json

import httpx
import pytest
from fastmcp import Client, FastMCP

from clinpgx_link.api.client import ClinPGxClient
from clinpgx_link.api.website import WebsiteClient
from clinpgx_link.config import Settings
from clinpgx_link.content.assets import AssetReference
from clinpgx_link.content.store import ContentStore
from clinpgx_link.models import SourceResponse
from clinpgx_link.services.api import ApiService
from tests.unit.test_repository import (
    GENES_RETRIEVED_AT,
    PATHWAYS_RETRIEVED_AT,
    RELEASE_TAG,
    _archive,
    _mixed_repository,
    _repository,
)


def _server(store, *, repository=None, api=None, website=None):
    from clinpgx_link.mcp.record_tools import register_record_tools

    server = FastMCP("entity-record-test", mask_error_details=True, dereference_schemas=False)
    register_record_tools(server, repository, store, api, website)
    return server


def _data(row, key):
    return row["fields"][key]["text"]


def test_recovery_plan_rejects_nonclosed_kind_and_unsafe_context() -> None:
    from clinpgx_link.mcp.recovery import RecoveryPlan, recovery_payload

    with pytest.raises(ValueError):
        RecoveryPlan("invented_action")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        RecoveryPlan(
            "record_not_found",
            entity_type="gene",
            record_id="PA1<script>",
            source="api",
        )
    with pytest.raises(ValueError):
        RecoveryPlan(
            "record_not_found",
            entity_type="gene",
            record_id="PA" + "1" * 511,
            source="api",
        )
    with pytest.raises(TypeError):
        recovery_payload({"kind": "record_not_found"})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_record_tool_definitions_describe_every_argument_within_budget(tmp_path):
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        tools = {tool.name: tool for tool in await _server(store).list_tools()}
        for name in ("search_records", "get_record", "get_related_records"):
            tool = tools[name]
            properties = tool.parameters["properties"]
            assert all(value.get("description") for value in properties.values())
            assert all(value.get("examples") for value in properties.values())
            assert (
                len(
                    json.dumps(
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": tool.parameters,
                        },
                        separators=(",", ":"),
                    ).encode()
                )
                <= 4800
            )
        assert "membership" in tools["search_records"].description
        related = tools["get_related_records"].parameters["properties"]
        assert related["result_type"]["examples"] == ["relationship"]
        assert "connected-object" in related["other_type"]["description"]
        assert "pair" in related["other_type"]["description"]
        view_description = related["view"]["description"]
        assert "API pair" in view_description
        assert "upstream projection" in view_description
        assert "not sent upstream" in view_description
        assert "cursor-bound" in view_description
    finally:
        store.close()


@pytest.mark.asyncio
async def test_auto_local_multivalue_search_and_snapshot_cursor(tmp_path):
    repository, built = _mixed_repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_server(store, repository=repository)) as client:
            first = await client.call_tool(
                "search_records",
                {
                    "entity_type": "gene",
                    "filters": {"gene": "RGS4", "chemical": "olanzapine"},
                    "limit": 1,
                },
            )
            payload = first.structured_content
            assert payload["_meta"]["data_source"] == "download"
            assert payload["_meta"]["snapshot_id"] == built.snapshot_id
            assert payload["_meta"]["source_url"] == "https://www.clinpgx.org/downloads"
            assert payload["_meta"]["source_scope"] == "snapshot"
            assert payload["_meta"]["retrieved_at"] == PATHWAYS_RETRIEVED_AT
            assert payload["_meta"]["retrieval_time_scope"] == "aggregate_snapshot"
            assert payload["results"][0]["id"] == "655384607"
            assert payload["results"][0]["content_ref"].startswith("asset:")
            chemical = await client.call_tool(
                "search_records",
                {
                    "entity_type": "chemical",
                    "filters": {"gene": "RGS4", "chemical": "olanzapine"},
                },
            )
            assert chemical.structured_content["results"][0]["id"] == "655384607"

            dpwg = await client.call_tool(
                "search_records",
                {
                    "entity_type": "gene",
                    "filters": {"gene": "PA134865839", "source": "DPWG"},
                },
            )
            assert dpwg.structured_content["results"][0]["id"] == "PA166363221"

            broad = await client.call_tool(
                "search_records", {"entity_type": "gene", "query": "CPCJ", "limit": 1}
            )
            broad_payload = broad.structured_content
            broad_row = broad_payload["results"][0]
            assert _data(broad_row, "Symbol") == "CYP2C19"
            assert broad_payload["_meta"]["source_url"] == "https://www.clinpgx.org/downloads"
            assert broad_row["provenance"]["dataset_source_url"].endswith("/data/genes.zip")
            assert broad_row["provenance"]["retrieved_at"] == GENES_RETRIEVED_AT
            assert (
                broad_row["provenance"]["archive_sha256"] != broad_payload["_meta"]["source_sha256"]
            )
            assert broad_row["member"]["provenance"]["retrieved_at"] == GENES_RETRIEVED_AT
            broad_asset = AssetReference.decode(broad_row["content_ref"])
            assert broad_asset.dataset_id == "data/genes.zip"
            assert broad_asset.member == "genes.tsv"

            wrong_source = await client.call_tool(
                "search_records",
                {"entity_type": "gene", "query": "CPCJ", "source": "api"},
                raise_on_error=False,
            )
            recovery = wrong_source.structured_content
            assert recovery["recovery"]["valid_choices"]["source"] == ["api", "download"]
            assert recovery["fallback_tool"] == "get_server_capabilities"
            assert recovery["fallback_args"] == {}
            assert recovery["recovery"]["next_commands"] == [
                {"tool": "get_server_capabilities", "arguments": {}}
            ]

            all_genes = await client.call_tool(
                "search_records", {"entity_type": "gene", "source": "download", "limit": 1}
            )
            cursor = all_genes.structured_content["_meta"]["pagination"]["next_cursor"]
            repository._snapshot_id = "sha256:" + "f" * 64
            stale = await client.call_tool(
                "search_records",
                {"entity_type": "gene", "source": "download", "limit": 1, "cursor": cursor},
                raise_on_error=False,
            )
            assert stale.is_error
            assert stale.structured_content["subtype"] == "snapshot_mismatch"
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_local_discovery_does_not_enum_restrict_swissmedic_source(tmp_path):
    from clinpgx_link.data.catalog import SourceInput
    from clinpgx_link.data.repository import DatasetRepository
    from clinpgx_link.ingest.builder import build_snapshot

    archive = tmp_path / "drugLabels.zip"
    _archive(
        archive,
        {
            "drugLabels.tsv": (
                b"PharmGKB ID\tName\tSource\tChemicals\tGenes\tVariants/Haplotypes\n"
                b"PA-SWISS\tSwiss label\tSwissmedic\tclopidogrel\tCYP2C19\t*2\n"
            )
        },
    )
    source = SourceInput.from_path(
        dataset_id="data/drugLabels.zip",
        path=archive,
        source_url="https://api.clinpgx.org/v1/download/file/data/drugLabels.zip",
        retrieved_at="2026-09-05T08:00:00Z",
        published_at="2026-09-05T00:00:00Z",
        media_type="application/zip",
        license_id="operator-local-only",
        tier="approved_registry",
    )
    built = build_snapshot([source], tmp_path / "candidate", RELEASE_TAG)
    repository = DatasetRepository(built.database)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_server(store, repository=repository)) as client:
            result = await client.call_tool(
                "search_records",
                {
                    "entity_type": "chemical",
                    "filters": {"chemical": "clopidogrel", "source": "Swissmedic"},
                },
            )
            assert result.structured_content["results"][0]["id"] == "PA-SWISS"
            assert result.structured_content["_meta"]["data_source"] == "download"
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_exact_auto_and_explicit_api_use_documented_parameters(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.path == "/v1/data/gene"
        assert dict(request.url.params) == {"symbol": "CYP2C19", "view": "base"}
        return httpx.Response(
            200, json={"status": "success", "data": [{"id": "PA124"}, {"id": "PA125"}]}
        )

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(_server(store, api=ApiService(upstream))) as client:
            first = await client.call_tool(
                "search_records",
                {"entity_type": "gene", "filters": {"gene": "CYP2C19"}, "limit": 1},
            )
            cursor = first.structured_content["_meta"]["pagination"]["next_cursor"]
            second = await client.call_tool(
                "search_records",
                {
                    "entity_type": "gene",
                    "filters": {"gene": "CYP2C19"},
                    "limit": 1,
                    "cursor": cursor,
                    "response_mode": "full",
                },
            )
            assert second.structured_content["results"][0]["id"] == "PA125"
            assert len(calls) == 1

            unsupported = await client.call_tool(
                "search_records",
                {"entity_type": "gene", "query": "CYP", "source": "api"},
                raise_on_error=False,
            )
            assert unsupported.is_error
            assert unsupported.structured_content["error_code"] == "invalid_input"
            assert len(calls) == 1
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_auto_broad_without_mirror_refuses_without_http(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(500)

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(_server(store, api=ApiService(upstream))) as client:
            result = await client.call_tool(
                "search_records", {"entity_type": "gene", "query": "CYP"}, raise_on_error=False
            )
            assert result.is_error
            assert result.structured_content["error_code"] == "upstream_unavailable"
            assert result.structured_content["subtype"] == "dataset_unavailable"
            assert calls == []
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_download_detail_resolves_exact_external_id_and_rejects_ambiguity(
    tmp_path, monkeypatch
):
    repository, built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_server(store, repository=repository)) as client:
            result = await client.call_tool(
                "get_record",
                {"entity_type": "gene", "record_id": "PA124", "source": "download"},
            )
            assert result.structured_content["result"]["id"] == "PA124"
            assert result.structured_content["_meta"]["snapshot_id"] == built.snapshot_id
            assert result.structured_content["_meta"]["source_url"].endswith("/data/genes.zip")
            assert result.structured_content["_meta"]["source_scope"] == "dataset"
            assert result.structured_content["result"]["provenance"]["retrieved_at"] == (
                GENES_RETRIEVED_AT
            )

            original = repository.search_entities

            def duplicate(*args, **kwargs):
                response = original(*args, **kwargs)
                response.value = response.value * 2
                response.details["total_count"] = 2
                return response

            monkeypatch.setattr(repository, "search_entities", duplicate)
            ambiguous = await client.call_tool(
                "get_record",
                {"entity_type": "gene", "record_id": "PA124", "source": "download"},
                raise_on_error=False,
            )
            assert ambiguous.is_error
            assert ambiguous.structured_content["error_code"] == "ambiguous_query"
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_local_search_and_detail_defer_first_row_that_exceeds_wire_fence_budget(
    tmp_path, monkeypatch
):
    repository, _ = _mixed_repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    original_search = repository.search_entities
    original_get = repository.get_record
    found = original_search("gene", filters={"id": "PA124"}, limit=1)
    oversized = dict(found.value[0])
    oversized["fields"] = {f"F{index}": "short" for index in range(70)}
    fetched = original_get(found.value[0]["record_id"])
    trusted_description = {
        "members": [
            {
                "path": oversized["member"],
                "fields": [{"name": name} for name in oversized["fields"]],
            }
        ]
    }
    monkeypatch.setattr(
        repository,
        "search_entities",
        lambda *args, **kwargs: SourceResponse(
            [oversized], found.source, {**found.details, "total_count": 1, "has_more": False}
        ),
    )
    monkeypatch.setattr(
        repository,
        "get_record",
        lambda *args, **kwargs: SourceResponse(oversized, fetched.source, fetched.details),
    )
    monkeypatch.setattr(
        repository,
        "describe",
        lambda dataset_id: SourceResponse(trusted_description, fetched.source),
    )
    try:
        async with Client(_server(store, repository=repository)) as client:
            search = await client.call_tool(
                "search_records",
                {
                    "entity_type": "gene",
                    "filters": {"id": "PA124"},
                    "source": "download",
                    "response_mode": "full",
                },
            )
            search_row = search.structured_content["results"][0]
            assert search_row["fields"]["deferred_content"] is True
            retained = store.get(search_row["fields"]["content_ref"])
            assert retained.source.retrieved_at == GENES_RETRIEVED_AT
            assert search_row["provenance"]["dataset_source_url"].endswith("/data/genes.zip")

            detail = await client.call_tool(
                "get_record",
                {
                    "entity_type": "gene",
                    "record_id": "PA124",
                    "source": "download",
                    "pointer": "/fields/F0",
                    "response_mode": "full",
                },
            )
            row = detail.structured_content["result"]
            assert row["fields"]["deferred_content"] is True
            assert row["selected"]["pointer"] == "/fields/F0"
            assert row["selected"]["data"]["text"] == '"short"'
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_api_and_website_detail_preserve_pointer_and_source_honesty(tmp_path):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/v1/data/gene/PA124":
            return httpx.Response(
                200,
                json={"status": "success", "data": {"id": "PA124", "symbol": "CYP2C19"}},
            )
        assert request.url.path == "/v1/site/gene/PA124"
        return httpx.Response(
            200,
            json={"status": "success", "data": {"id": "PA124", "symbol": "CYP2C19"}},
        )

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(
            _server(store, api=ApiService(upstream), website=WebsiteClient(upstream))
        ) as client:
            api = await client.call_tool(
                "get_record", {"entity_type": "gene", "record_id": "PA124", "pointer": "/symbol"}
            )
            assert json.loads(api.structured_content["result"]["data"]["text"]) == "CYP2C19"
            assert api.structured_content["result"]["source_pointer"]["text"] == "/data/symbol"
            assert api.structured_content["_meta"]["retrieval_time_kind"] == "unknown"
            assert api.structured_content["_meta"]["acquired_at"] is None
            assert api.structured_content["_meta"]["admitted_at"] is None
            assert api.structured_content["_meta"]["source_scope"] == "response"
            assert api.structured_content["_meta"]["retrieval_time_scope"] == "source_recorded"
            website = await client.call_tool(
                "get_record",
                {"entity_type": "gene", "record_id": "PA124", "source": "website"},
            )
            assert website.structured_content["_meta"]["data_source"] == "website"
            assert website.structured_content["_meta"]["retrieval_time_kind"] == "unknown"
            assert website.structured_content["_meta"]["acquired_at"] is None
            assert website.structured_content["_meta"]["admitted_at"] is None
            unsupported = await client.call_tool(
                "get_record",
                {"entity_type": "chemical", "record_id": "PA449053", "source": "website"},
                raise_on_error=False,
            )
            assert unsupported.is_error
            assert unsupported.structured_content["error_code"] == "invalid_input"
            assert paths == ["/v1/data/gene/PA124", "/v1/site/gene/PA124"]
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_get_record_live_and_download_share_ordered_pointer_selection(tmp_path):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"id": "PA124", "symbol": "CYP2C19", "nullable": None},
            },
        )

    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(
            _server(store, repository=repository, api=ApiService(upstream))
        ) as client:
            live = await client.call_tool(
                "get_record",
                {
                    "entity_type": "gene",
                    "record_id": "PA124",
                    "pointers": ["/symbol", "/missing", "/nullable"],
                },
            )
            local = await client.call_tool(
                "get_record",
                {
                    "entity_type": "gene",
                    "record_id": "PA124",
                    "source": "download",
                    "pointers": ["/fields/Symbol", "/fields/Name"],
                },
            )
            local_container = await client.call_tool(
                "get_record",
                {
                    "entity_type": "gene",
                    "record_id": "PA124",
                    "source": "download",
                    "pointers": ["/fields"],
                },
                raise_on_error=False,
            )

        live_result = live.structured_content["result"]
        assert [item["status"] for item in live_result["selections"]] == [
            "value",
            "absent",
            "value",
        ]
        assert live_result["selections"][0]["original_locator"]["pointer"]["text"] == (
            "/data/symbol"
        )
        assert calls == ["/v1/data/gene/PA124"]
        local_result = local.structured_content["result"]
        assert [item["pointer"]["text"] for item in local_result["selections"]] == [
            "/fields/Symbol",
            "/fields/Name",
        ]
        assert all(
            item["original_locator"]["kind"] == "tabular_cell"
            for item in local_result["selections"]
        )
        assert local_result["content_ref"].startswith("asset:")
        assert local_result["normalized_record_ref"].startswith("content:")
        assert "fields" not in local_result
        assert local_container.is_error
        assert local_container.structured_content["fallback_tool"] == "get_source_content"
        assert local_container.structured_content["fallback_args"]["pointer"] == ""
        assert local_container.structured_content["fallback_args"]["representation"] == (
            "structure"
        )
    finally:
        repository.close()
        await upstream.close()


@pytest.mark.asyncio
async def test_download_related_returns_every_joined_source_row(tmp_path):
    repository, built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    parent = repository.search(
        "data/summaryAnnotations.zip",
        member="summary_annotations.tsv",
        filters={"annotation_id": "655384602"},
        limit=1,
    ).value[0]
    try:
        async with Client(_server(store, repository=repository)) as client:
            evidence = await client.call_tool(
                "get_related_records",
                {
                    "record_id": parent["record_id"],
                    "result_type": "evidence",
                    "source": "download",
                },
            )
            alleles = await client.call_tool(
                "get_related_records",
                {
                    "record_id": parent["record_id"],
                    "result_type": "allele",
                    "source": "download",
                },
            )
            literature = await client.call_tool(
                "get_related_records",
                {
                    "record_id": parent["record_id"],
                    "result_type": "literature",
                    "source": "download",
                },
            )
            assert len(evidence.structured_content["results"]) == 1
            assert len(alleles.structured_content["results"]) == 3
            assert len(literature.structured_content["results"]) == 1
            assert alleles.structured_content["_meta"]["snapshot_id"] == built.snapshot_id
            assert all(
                row["join"]["relation_kind"] == "allele"
                for row in alleles.structured_content["results"]
            )
            assert literature.structured_content["results"][0]["join"]["limitation"] == (
                "citing_evidence_row_not_bibliographic_detail"
            )
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_api_related_pair_uses_both_ids_and_registry_result_type(tmp_path):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        assert dict(request.url.params) == {"view": "max"}
        return httpx.Response(200, json={"status": "success", "data": [{"id": "1"}]})

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(_server(store, api=ApiService(upstream))) as client:
            result = await client.call_tool(
                "get_related_records",
                {
                    "record_id": "PA124",
                    "other_id": "PA449053",
                    "result_type": "summary_annotation",
                    "source": "api",
                    "view": "max",
                },
            )
            assert result.structured_content["results"][0]["id"] == "1"
            assert result.structured_content["results"][0]["source_pointer"]["text"] == "/data/0"
            await client.call_tool(
                "get_related_records",
                {
                    "record_id": "PA449053",
                    "other_id": "PA124",
                    "entity_type": "Chemical",
                    "other_type": "Gene",
                    "result_type": "summary_annotation",
                    "source": "api",
                    "view": "max",
                },
            )
            assert paths == [
                "/v1/report/pair/PA124/PA449053/summaryAnnotation",
                "/v1/report/pair/PA449053/PA124/summaryAnnotation",
            ]
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_api_connected_objects_pages_exact_route_without_refetch(tmp_path):
    calls = []
    connected = [
        {
            "connectedObject": {"id": "PA449053", "name": "clopidogrel"},
            "connectionTypes": ["ChemicalGeneAssociation"],
        },
        {
            "connectedObject": {"id": "PA451866", "name": "omeprazole"},
            "connectionTypes": ["ChemicalGeneAssociation", "GuidelineAnnotation"],
        },
    ]

    def handle(request):
        calls.append(request)
        assert request.url.path == "/v1/report/connectedObjects/PA124/Chemical"
        assert dict(request.url.params) == {}
        return httpx.Response(200, json=connected)

    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(
            _server(store, repository=repository, api=ApiService(upstream))
        ) as client:
            first = await client.call_tool(
                "get_related_records",
                {"record_id": "PA124", "result_type": "relationship", "limit": 1},
            )
            first_payload = first.structured_content
            assert json.loads(first_payload["results"][0]["data"]["text"]) == connected[0]
            assert first_payload["results"][0]["source_pointer"]["text"] == "/0"
            assert first_payload["_meta"]["source"] == "ClinPGx REST API"
            assert first_payload["_meta"]["source_url"].endswith(
                "/v1/report/connectedObjects/PA124/Chemical"
            )
            assert first_payload["_meta"]["source_sha256"]
            cursor = first_payload["_meta"]["pagination"]["next_cursor"]

            second = await client.call_tool(
                "get_related_records",
                {
                    "record_id": "PA124",
                    "result_type": "relationship",
                    "limit": 1,
                    "cursor": cursor,
                    "response_mode": "full",
                },
            )
            assert (
                json.loads(second.structured_content["results"][0]["data"]["text"]) == connected[1]
            )
            assert len(calls) == 1

            changed_selectors = [
                {
                    "record_id": "PA124",
                    "result_type": "relationship",
                    "other_type": "Disease",
                    "cursor": cursor,
                },
                {
                    "record_id": "PA124",
                    "other_id": "PA449053",
                    "result_type": "summary_annotation",
                    "cursor": cursor,
                },
                {
                    "record_id": "PA124",
                    "result_type": "relationship",
                    "source": "download",
                    "cursor": cursor,
                },
            ]
            for arguments in changed_selectors:
                rejected = await client.call_tool(
                    "get_related_records", arguments, raise_on_error=False
                )
                assert rejected.is_error
                assert rejected.structured_content["error_code"] == "invalid_input"
                assert rejected.structured_content["field"] == "cursor"
            assert len(calls) == 1
    finally:
        repository.close()
        await upstream.close()


@pytest.mark.asyncio
async def test_api_related_rejects_wrong_conditional_mode_combinations(tmp_path):
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_server(store)) as client:
            cases = [
                (
                    {
                        "record_id": "PA124",
                        "other_id": "PA449053",
                        "result_type": "relationship",
                    },
                    "result_type",
                ),
                (
                    {"record_id": "PA124", "result_type": "summary_annotation"},
                    "other_id",
                ),
                (
                    {
                        "record_id": "PA124",
                        "other_id": "PA449053",
                        "result_type": "allele",
                    },
                    "result_type",
                ),
            ]
            for arguments, field in cases:
                rejected = await client.call_tool(
                    "get_related_records", arguments, raise_on_error=False
                )
                assert rejected.is_error
                assert rejected.structured_content["error_code"] == "invalid_input"
                assert rejected.structured_content["field"] == field
    finally:
        store.close()


@pytest.mark.asyncio
async def test_variant_symbol_detail_error_advertises_and_executes_exact_api_search(tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.path == "/v1/data/variant/"
        assert dict(request.url.params) == {"symbol": "rs123", "view": "max"}
        return httpx.Response(200, json={"status": "success", "data": [{"id": "PA1"}]})

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(_server(store, api=ApiService(upstream))) as client:
            rejected = await client.call_tool(
                "get_record",
                {"entity_type": "variant", "record_id": "rs123", "source": "api"},
                raise_on_error=False,
            )
            payload = rejected.structured_content
            assert rejected.is_error
            assert payload["error_code"] == "invalid_input"
            assert payload["field"] == "record_id"
            assert payload["recovery"]["context"] == {
                "entity_type": "variant",
                "record_id": "rs123",
                "source": "api",
            }
            assert "not an absence" in payload["recovery"]["limitation"]
            assert payload["fallback_tool"] == "search_records"
            assert payload["fallback_args"] == {
                "entity_type": "variant",
                "filters": {"variant": "rs123"},
                "source": "api",
                "view": "max",
            }
            recovered = await client.call_tool(payload["fallback_tool"], payload["fallback_args"])
            assert recovered.structured_content["results"][0]["id"] == "PA1"
            assert len(requests) == 1
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_unknown_safe_pa_not_found_preserves_context_and_callable_discovery(tmp_path):
    def handle(request):
        if request.url.path == "/v1/data/gene/PA999999":
            return httpx.Response(404, json={"hostile": "source-secret"})
        assert request.url.path == "/v1/data/gene"
        assert dict(request.url.params) == {"accessionId": "PA999999", "view": "max"}
        return httpx.Response(200, json={"status": "success", "data": []})

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(_server(store, api=ApiService(upstream))) as client:
            missing = await client.call_tool(
                "get_record",
                {"entity_type": "gene", "record_id": "PA999999", "source": "api"},
                raise_on_error=False,
            )
            payload = missing.structured_content
            assert payload["error_code"] == "not_found"
            assert payload["recovery"]["context"] == {
                "entity_type": "gene",
                "record_id": "PA999999",
                "source": "api",
            }
            assert "source-secret" not in json.dumps(payload)
            recovered = await client.call_tool(payload["fallback_tool"], payload["fallback_args"])
            assert recovered.structured_content["results"] == []
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_unsupported_detail_pair_lists_real_choices_and_callable_explicit_alternative(
    tmp_path,
):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/v1/data/chemical/PA123":
            return httpx.Response(200, json={"status": "success", "data": {"id": "PA123"}})
        assert request.url.path == "/v1/report/connectedObjects/PA123/Chemical"
        return httpx.Response(200, json=[])

    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(_server(store, api=ApiService(upstream))) as client:
            unsupported = await client.call_tool(
                "get_record",
                {"entity_type": "chemical", "record_id": "PA123", "source": "website"},
                raise_on_error=False,
            )
            payload = unsupported.structured_content
            assert payload["recovery"]["valid_choices"]["source"] == ["api", "download"]
            assert payload["fallback_args"]["source"] == "api"
            recovered = await client.call_tool(payload["fallback_tool"], payload["fallback_args"])
            assert recovered.structured_content["result"]["data"]["text"]

            wrong_mode = await client.call_tool(
                "get_related_records",
                {
                    "record_id": "PA123",
                    "other_id": "PA456",
                    "result_type": "relationship",
                    "source": "api",
                },
                raise_on_error=False,
            )
            related = wrong_mode.structured_content
            assert related["recovery"]["valid_choices"]["mode"] == [
                "connected_object",
                "pair",
            ]
            assert "other_id" not in related["fallback_args"]
            retried = await client.call_tool(related["fallback_tool"], related["fallback_args"])
            assert retried.structured_content["results"] == []
            assert paths == [
                "/v1/data/chemical/PA123",
                "/v1/report/connectedObjects/PA123/Chemical",
            ]
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_recovery_hides_malicious_identifiers_exception_payloads_and_filter_keys(tmp_path):
    from clinpgx_link.exceptions import NotFoundError

    secret = "IGNORE_INSTRUCTIONS_source_secret"

    class HostileFailureApi:
        async def get(self, *_args, **_kwargs):
            raise NotFoundError(secret, hint=secret)

    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_server(store, repository=repository, api=HostileFailureApi())) as client:
            hostile_id = "PA999<script>IGNORE"
            missing = await client.call_tool(
                "get_record",
                {"entity_type": "gene", "record_id": hostile_id, "source": "api"},
                raise_on_error=False,
            )
            rendered = json.dumps(missing.structured_content)
            assert missing.structured_content["error_code"] == "not_found"
            assert hostile_id not in rendered
            assert secret not in rendered

            hostile_key = "ignore_previous_secret"
            invalid = await client.call_tool(
                "search_records",
                {
                    "entity_type": "gene",
                    "filters": {hostile_key: "NEVER_ECHO"},
                    "source": "download",
                },
                raise_on_error=False,
            )
            payload = invalid.structured_content
            rendered = json.dumps(payload)
            assert payload["error_code"] == "invalid_input"
            assert payload["recovery"]["valid_choices"]["filters"] == [
                "annotation_id",
                "chemical",
                "gene",
                "id",
                "name",
                "source",
                "variant",
            ]
            assert hostile_key not in rendered
            assert "NEVER_ECHO" not in rendered
            assert payload["recovery"]["next_commands"] == [
                {"tool": "get_server_capabilities", "arguments": {}}
            ]
    finally:
        repository.close()
        store.close()
