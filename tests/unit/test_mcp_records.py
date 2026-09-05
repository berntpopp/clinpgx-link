"""Public entity-oriented MCP tools over live and immutable sources."""

from __future__ import annotations

import json

import httpx
import pytest
from fastmcp import Client, FastMCP

from clinpgx_link.api.client import ClinPGxClient
from clinpgx_link.api.website import WebsiteClient
from clinpgx_link.config import Settings
from clinpgx_link.content.store import ContentStore
from clinpgx_link.models import SourceResponse
from clinpgx_link.services.api import ApiService
from tests.unit.test_repository import RELEASE_TAG, _archive, _repository


def _server(store, *, repository=None, api=None, website=None):
    from clinpgx_link.mcp.record_tools import register_record_tools

    server = FastMCP("entity-record-test", mask_error_details=True, dereference_schemas=False)
    register_record_tools(server, repository, store, api, website)
    return server


def _data(row, key):
    return row["fields"][key]["text"]


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
    finally:
        store.close()


@pytest.mark.asyncio
async def test_auto_local_multivalue_search_and_snapshot_cursor(tmp_path):
    repository, built = _repository(tmp_path)
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
            assert _data(broad.structured_content["results"][0], "Symbol") == "CYP2C19"

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
    repository, _ = _repository(tmp_path)
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
                },
            )
            assert search.structured_content["results"][0]["fields"]["deferred_content"] is True

            detail = await client.call_tool(
                "get_record",
                {
                    "entity_type": "gene",
                    "record_id": "PA124",
                    "source": "download",
                    "pointer": "/fields/F0",
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
            website = await client.call_tool(
                "get_record",
                {"entity_type": "gene", "record_id": "PA124", "source": "website"},
            )
            assert website.structured_content["_meta"]["data_source"] == "website"
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
