"""Opt-in bounded live drift probes for separately approved source origins."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CLINPGX_RUN_LIVE_TESTS") != "1",
    reason="set CLINPGX_RUN_LIVE_TESTS=1 for bounded read-only source probes",
)
@pytest.mark.asyncio
async def test_live_website_and_cpic_reference_routes(tmp_path: Path) -> None:
    from clinpgx_link.api.client import ClinPGxClient
    from clinpgx_link.api.website import WebsiteClient
    from clinpgx_link.config import Settings
    from clinpgx_link.content.store import ContentStore

    settings = Settings(
        _env_file=None,
        cache_root=tmp_path / "cache",
        api_requests_per_second=1,
        request_timeout_seconds=15,
        request_deadline_seconds=45,
        max_response_bytes=2_000_000,
    )
    store = ContentStore(tmp_path / "content.sqlite", max_bytes=4_000_000, max_entries=4)
    source_client = ClinPGxClient(settings, content_store=store)
    client = WebsiteClient(source_client)
    try:
        gene = await client.call("GET /site/gene/{id}", {"id": "PA128"}, {})
        guideline = await client.call(
            "CPIC GET /guideline", {}, {"clinpgxid": "eq.PA166251454", "limit": 1}
        )
        assert gene.value["gene"]["id"] == "PA128"
        assert guideline.value and guideline.value[0]["clinpgxid"] == "PA166251454"
        assert guideline.details["content_range"].endswith("/1")
        assert gene.source.sha256 and guideline.source.sha256
    finally:
        await client.close()
