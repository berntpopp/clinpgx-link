"""Installed source bytes are retrieved through actual MCP calls, offline."""

import base64
import hashlib
import json

import pytest
from fastmcp import Client

from clinpgx_link.content.assets import AssetReference
from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp.facade import create_mcp
from clinpgx_link.models import SourceInfo, SourceResponse
from tests.unit.test_repository import FIXTURES, _repository


class _LongReferenceJsonRepository:
    def __init__(self, raw: bytes, reference: AssetReference) -> None:
        self.raw = raw
        self.reference = reference

    def asset_content(self, dataset_id, *, member=None, expected_snapshot=None):
        assert dataset_id == self.reference.dataset_id
        assert member == self.reference.member
        assert expected_snapshot == self.reference.snapshot_id
        return SourceResponse(
            self.raw,
            SourceInfo(
                "ClinPGx",
                "https://api.clinpgx.org/v1/download/file/data/test.json.zip",
                "2026-09-05T10:00:00Z",
                self.reference.sha256,
                "download",
                warnings=tuple(f"warning-{index}" for index in range(5)),
            ),
            {"media_type": "application/json"},
        )


def _fence_count(value):
    if isinstance(value, dict):
        return int(value.get("kind") == "untrusted_text") + sum(
            _fence_count(child) for child in value.values()
        )
    if isinstance(value, list):
        return sum(_fence_count(child) for child in value)
    return 0


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
                assert call.structured_content["_meta"]["snapshot_id"] == built.snapshot_id
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
async def test_long_asset_reference_structure_pages_stay_mirrored_and_progress(tmp_path):
    values = {f"field-{index}": f"value-{index}" for index in range(12)}
    raw = json.dumps(values).encode()
    reference = AssetReference(
        "sha256:" + "a" * 64,
        "data/test.json.zip",
        "m" * 4096,
        hashlib.sha256(raw).hexdigest(),
    )
    encoded = reference.encode()
    repository = _LongReferenceJsonRepository(raw, reference)
    store = ContentStore(tmp_path / "cache.sqlite")
    returned_keys = []
    start = 0
    try:
        async with Client(create_mcp(content_store=store, repository=repository)) as client:
            while True:
                call = await client.call_tool(
                    "get_source_content",
                    {
                        "content_ref": encoded,
                        "representation": "structure",
                        "start": start,
                        "length": 8192,
                    },
                )
                envelope = call.structured_content
                payload = envelope["result"]
                assert json.loads(call.content[0].text) == envelope
                assert len(call.content[0].text.encode()) <= 100_000
                assert _fence_count(envelope) <= 128
                assert payload["returned"] > 0
                returned_keys.extend(item["key"]["text"] for item in payload["items"])
                if not payload["has_more"]:
                    break
                assert payload["next_start"] == start + payload["returned"]
                start = payload["next_start"]
    finally:
        store.close()

    assert len(encoded) > 5_000
    assert returned_keys == list(values)


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
                    {"content_ref": reference, "pointer": "/0", "representation": "base64"},
                    raise_on_error=False,
                )
                assert call.is_error
                assert call.structured_content["error_code"] == "upstream_unavailable"
                assert call.structured_content["fallback_tool"] == "get_server_capabilities"
                assert call.structured_content["_meta"]["next_commands"] == [
                    {"tool": "get_server_capabilities", "arguments": {}}
                ]
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_valid_asset_base64_pointer_offers_executable_exact_byte_recovery(tmp_path):
    repository, built = _repository(tmp_path)
    member = repository.describe("data/genes.zip").value["members"][0]
    reference = AssetReference(
        built.snapshot_id, "data/genes.zip", "genes.tsv", member["sha256"]
    ).encode()
    store = ContentStore(tmp_path / "cache.sqlite")
    try:
        async with Client(create_mcp(content_store=store, repository=repository)) as client:
            rejected = await client.call_tool(
                "get_source_content",
                {"content_ref": reference, "pointer": "/0", "representation": "base64"},
                raise_on_error=False,
            )
            command = rejected.structured_content["_meta"]["next_commands"][0]
            recovered = await client.call_tool(command["tool"], command["arguments"])

        assert rejected.is_error
        assert json.loads(rejected.content[0].text) == rejected.structured_content
        assert rejected.structured_content["subtype"] == "base64_pointer_unsupported"
        assert command == {
            "tool": "get_source_content",
            "arguments": {
                "content_ref": reference,
                "pointer": "",
                "representation": "base64",
            },
        }
        assert json.loads(recovered.content[0].text) == recovered.structured_content
        assert (
            base64.b64decode(recovered.structured_content["result"]["base64"])
            == (FIXTURES / "genes.tsv").read_bytes()
        )
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_malformed_asset_reference_never_advertises_content_recovery(tmp_path):
    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "cache.sqlite")
    try:
        async with Client(create_mcp(content_store=store, repository=repository)) as client:
            rejected = await client.call_tool(
                "get_source_content",
                {"content_ref": "asset:not-valid", "pointer": "/0", "representation": "base64"},
                raise_on_error=False,
            )

        assert rejected.is_error
        assert rejected.structured_content["fallback_tool"] == "get_server_capabilities"
        assert rejected.structured_content["_meta"]["next_commands"] == [
            {"tool": "get_server_capabilities", "arguments": {}}
        ]
    finally:
        repository.close()
        store.close()
