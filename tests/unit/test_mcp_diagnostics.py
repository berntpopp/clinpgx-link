"""Diagnostics expose safe operational evidence and probe only when requested."""

import json

import httpx
import pytest
from fastmcp import Client

from clinpgx_link.api.client import ClinPGxClient
from clinpgx_link.config import Settings
from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp.facade import create_mcp
from clinpgx_link.services.api import ApiService
from tests.unit.test_repository import _repository


@pytest.mark.asyncio
async def test_diagnostics_reports_no_snapshot_without_local_path_disclosure(tmp_path):
    store = ContentStore(tmp_path / "private-location" / "cache.sqlite")
    try:
        async with Client(create_mcp(content_store=store)) as client:
            call = await client.call_tool("get_diagnostics", {})
            result = call.structured_content["result"]
            assert result["local_snapshot"] == {"ready": False, "reason": "not_configured"}
            assert result["api_configured"] is False
            assert result["upstream_probe"]["status"] == "not_requested"
            assert str(tmp_path) not in json.dumps(call.structured_content)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_diagnostics_uses_live_probe_only_on_explicit_request(tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.path == "/v1/data/gene/PA124"
        return httpx.Response(200, json={"status": "success", "data": {"id": "PA124"}})

    store = ContentStore(tmp_path / "cache.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        store,
    )
    try:
        async with Client(
            create_mcp(content_store=store, api_service=ApiService(upstream))
        ) as client:
            await client.call_tool("get_diagnostics", {})
            assert not requests
            call = await client.call_tool("get_diagnostics", {"probe_upstream": True})
            assert len(requests) == 1
            probe = call.structured_content["result"]["upstream_probe"]
            assert probe["status"] == "reachable"
            assert probe["source_sha256"]
            recovered = await client.call_tool(
                "get_source_content",
                {
                    "content_ref": probe["content_ref"],
                    "pointer": "/data/id",
                    "representation": "text",
                },
            )
            assert recovered.structured_content["result"]["text"]["text"] == "PA124"
            cached = await client.call_tool("get_diagnostics", {"probe_upstream": True})
            cached_probe = cached.structured_content["result"]["upstream_probe"]
            assert len(requests) == 1
            assert cached_probe["status"] == "cached_evidence"
            assert cached_probe["retrieved_at"] == probe["retrieved_at"]
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_diagnostics_reports_pinned_snapshot_without_database_path(tmp_path):
    repository, built = _repository(tmp_path)
    store = ContentStore(tmp_path / "cache.sqlite")
    try:
        async with Client(create_mcp(content_store=store, repository=repository)) as client:
            call = await client.call_tool("get_diagnostics", {})
            status = call.structured_content["result"]["local_snapshot"]
            assert status["snapshot_id"] == built.snapshot_id
            assert status["ready"] is True
            assert "database" not in status
            assert str(tmp_path) not in json.dumps(call.structured_content)
    finally:
        repository.close()
        store.close()
