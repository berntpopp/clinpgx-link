"""Verified ClinPGx website and CPIC reference adapters."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parents[1] / "fixtures/api"


def _settings(tmp_path: Path):
    from clinpgx_link.config import Settings

    return Settings(
        _env_file=None,
        cache_root=tmp_path / "cache",
        request_timeout_seconds=2,
        request_deadline_seconds=10,
    )


def _store(tmp_path: Path):
    from clinpgx_link.content.store import ContentStore

    return ContentStore(tmp_path / "content.sqlite", max_bytes=2_000_000, max_entries=50)


def test_website_registry_has_honest_inventory_and_defensive_descriptions():
    from clinpgx_link.api.website_operations import WebsiteRegistry

    registry = WebsiteRegistry()
    operations = registry.list_operations()
    assert sum(item["source"] == "clinpgx_website" for item in operations) == 36
    assert sum(item["source"] == "cpic" for item in operations) == 40
    root = registry.describe("CPIC GET /")
    assert root["callable"] is False
    assert root["status"] == "inventory_only"
    root["callable"] = True
    assert registry.describe("CPIC GET /")["callable"] is False


def test_website_registry_binds_verified_site_routes_only():
    from clinpgx_link.api.website_operations import WebsiteRegistry
    from clinpgx_link.exceptions import InvalidInputError

    registry = WebsiteRegistry()
    bound = registry.bind(
        "GET /site/guideline/{id}",
        {"id": "PA166251454"},
        {"view": "base"},
    )
    assert bound.path == "/site/guideline/PA166251454"
    assert bound.params == {"view": "base"}
    assert bound.representation == "json"
    with pytest.raises(InvalidInputError):
        registry.bind("GET /site/guideline/{id}", {"id": "../secret"}, {})
    with pytest.raises(InvalidInputError):
        registry.bind("GET /site/unknown", {}, {})


def test_website_registry_preserves_verified_non_json_decoder():
    from clinpgx_link.api.website_operations import WebsiteRegistry

    registry = WebsiteRegistry()
    allele_function = registry.bind("GET /site/alleleFunction/{geneId}", {"geneId": "PA128"}, {})
    attachment = registry.bind(
        "GET /site/haplotypeFrequency/_download/{id}",
        {"id": "PA166170351"},
        {"source": "CPIC"},
    )
    assert allele_function.representation == "text"
    assert attachment.representation == "text"


def test_cpic_registry_applies_bounded_pagination_and_row_filters():
    from clinpgx_link.api.website_operations import WebsiteRegistry
    from clinpgx_link.exceptions import InvalidInputError

    registry = WebsiteRegistry()
    bound = registry.bind("CPIC GET /guideline", {}, {"clinpgxid": "eq.PA166251454"})
    assert bound.path == "/guideline"
    assert bound.params == {
        "clinpgxid": "eq.PA166251454",
        "limit": "20",
        "offset": "0",
    }
    explicit = registry.bind("CPIC GET /gene", {}, {"limit": 100, "offset": 25})
    assert explicit.params["limit"] == "100"
    assert explicit.params["offset"] == "25"
    for invalid in (
        {"select": "*"},
        {"symbol": "CYP2D6"},
        {"limit": 101},
        {"offset": -1},
    ):
        with pytest.raises(InvalidInputError):
            registry.bind("CPIC GET /gene", {}, invalid)
    with pytest.raises(InvalidInputError) as unavailable:
        registry.bind("CPIC GET /rpc/test_alert_lookup", {}, {"lookup": "x"})
    assert unavailable.value.subtype == "operation_unavailable"


@pytest.mark.asyncio
async def test_website_client_decodes_text_plain_json_and_sets_clinpgx_license(tmp_path):
    raw = b'{"status":"success","data":{"gene":"CYP2D6","alleles":[]}}'

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.clinpgx.org/v1/site/alleleFunction/PA128"
        assert request.headers["accept"] == "text/plain"
        return httpx.Response(200, content=raw, headers={"content-type": "text/plain"})

    from clinpgx_link.api.client import ClinPGxClient
    from clinpgx_link.api.website import WebsiteClient

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source_client = ClinPGxClient(
        _settings(tmp_path), http_client=http_client, content_store=_store(tmp_path)
    )
    client = WebsiteClient(source_client)
    result = await client.call("GET /site/alleleFunction/{geneId}", {"geneId": "PA128"}, {})
    assert result.value == {"gene": "CYP2D6", "alleles": []}
    assert result.source.source == "ClinPGx website API"
    assert result.details["license"]["spdx"] == "CC-BY-SA-4.0"
    assert result.details["source_pointer"] == "/data"
    assert source_client.content_store.get(result.details["content_ref"]).raw == raw
    await client.close()


@pytest.mark.asyncio
async def test_text_plain_json_uses_one_exact_capacity_admission_and_cached_reference(tmp_path):
    raw = b'{"alleles":[{"name":"*2"}]}'
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=raw, headers={"content-type": "text/plain"})

    from clinpgx_link.api.client import ClinPGxClient
    from clinpgx_link.api.website import WebsiteClient
    from clinpgx_link.content.store import ContentStore

    store = ContentStore(
        tmp_path / "content.sqlite", max_bytes=len(raw), max_entries=1, ttl_seconds=3600
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source_client = ClinPGxClient(_settings(tmp_path), http_client=http_client, content_store=store)
    client = WebsiteClient(source_client)

    first = await client.call("GET /site/alleleFunction/{geneId}", {"geneId": "PA128"}, {})
    second = await client.call("GET /site/alleleFunction/{geneId}", {"geneId": "PA128"}, {})

    assert calls == 1
    assert first.value == {"alleles": [{"name": "*2"}]}
    assert second.source.data_source == "cache"
    assert second.details["content_ref"] == first.details["content_ref"]
    assert first.details["media_type"] == "application/json"
    assert first.details["upstream_media_type"] == "text/plain"
    assert first.details["source_pointer"] == ""
    stored = store.get(first.details["content_ref"])
    assert stored.raw == raw
    assert stored.media_type == "application/json"
    await client.close()


@pytest.mark.asyncio
async def test_cpic_client_preserves_exact_206_pagination_and_separate_license(tmp_path):
    raw = (FIXTURES / "cpic_guideline_page.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == (
            "https://api.cpicpgx.org/v1/guideline?clinpgxid=eq.PA166251454&limit=20&offset=0"
        )
        assert request.headers["prefer"] == "count=exact"
        assert request.headers["range-unit"] == "items"
        return httpx.Response(
            206,
            content=raw,
            headers={"content-type": "application/json", "content-range": "0-0/1"},
        )

    from clinpgx_link.api.client import ClinPGxClient
    from clinpgx_link.api.website import WebsiteClient

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source_client = ClinPGxClient(
        _settings(tmp_path), http_client=http_client, content_store=_store(tmp_path)
    )
    client = WebsiteClient(source_client)
    result = await client.call("CPIC GET /guideline", {}, {"clinpgxid": "eq.PA166251454"})
    assert result.value == json.loads(raw)
    assert result.source.source == "CPIC API"
    assert result.details["content_range"] == "0-0/1"
    assert result.details["license"]["spdx"] == "CC0-1.0"
    cached = await client.call("CPIC GET /guideline", {}, {"clinpgxid": "eq.PA166251454"})
    assert cached.source.data_source == "cache"
    await client.close()


@pytest.mark.asyncio
async def test_website_envelope_route_rejects_unwrapped_success_json(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "PA128"})

    from clinpgx_link.api.client import ClinPGxClient
    from clinpgx_link.api.website import WebsiteClient
    from clinpgx_link.exceptions import DataValidationError

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source_client = ClinPGxClient(
        _settings(tmp_path), http_client=http_client, content_store=_store(tmp_path)
    )
    client = WebsiteClient(source_client)
    with pytest.raises(DataValidationError):
        await client.call("GET /site/gene/{id}", {"id": "PA128"}, {})
    await client.close()
