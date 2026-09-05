"""Installed source bytes are retrieved through actual MCP calls, offline."""

import base64

import pytest
from fastmcp import Client

from clinpgx_link.content.assets import AssetReference
from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp.facade import create_mcp
from tests.unit.test_repository import FIXTURES, _repository


@pytest.mark.asyncio
async def test_installed_member_reconstructs_offline_through_mcp(tmp_path):
    repository, built = _repository(tmp_path)
    member = repository.describe("data/genes.zip").value["members"][0]
    reference = AssetReference(
        built.snapshot_id, "data/genes.zip", "genes.tsv", member["sha256"]
    ).encode()
    store = ContentStore(tmp_path / "cache.sqlite")
    try:
        async with Client(create_mcp(content_store=store, repository=repository)) as client:
            chunks = []
            start = 0
            while True:
                call = await client.call_tool(
                    "get_source_content",
                    {
                        "content_ref": reference,
                        "representation": "base64",
                        "start": start,
                        "length": 127,
                    },
                )
                part = call.structured_content["result"]
                assert part["offline_available"] is True
                assert part["snapshot_id"] == built.snapshot_id
                assert part["expires_at"] is None
                chunks.append(base64.b64decode(part["base64"]))
                if not part["has_more"]:
                    break
                assert part["next_start"] > start
                start = part["next_start"]
            assert b"".join(chunks) == (FIXTURES / "genes.tsv").read_bytes()
            text = await client.call_tool(
                "get_source_content",
                {"content_ref": reference, "representation": "text", "length": 40},
            )
            assert (
                text.structured_content["result"]["text"]["text"]
                == ((FIXTURES / "genes.tsv").read_text()[:40])
            )
            out_of_range = await client.call_tool(
                "get_source_content",
                {
                    "content_ref": reference,
                    "representation": "base64",
                    "start": len(b"".join(chunks)) + 1,
                },
                raise_on_error=False,
            )
            assert out_of_range.is_error
            assert out_of_range.structured_content["error_code"] == "invalid_input"
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_asset_digest_mismatch_and_stale_snapshot_fail_closed(tmp_path):
    repository, built = _repository(tmp_path)
    member = repository.describe("data/genes.zip").value["members"][0]
    store = ContentStore(tmp_path / "cache.sqlite")
    try:
        async with Client(create_mcp(content_store=store, repository=repository)) as client:
            for snapshot, digest in (
                (built.snapshot_id, "0" * 64),
                ("sha256:" + "0" * 64, member["sha256"]),
            ):
                reference = AssetReference(snapshot, "data/genes.zip", "genes.tsv", digest).encode()
                call = await client.call_tool(
                    "get_source_content",
                    {"content_ref": reference, "representation": "base64"},
                    raise_on_error=False,
                )
                assert call.is_error
                assert call.structured_content["error_code"] == "upstream_unavailable"
    finally:
        repository.close()
        store.close()
