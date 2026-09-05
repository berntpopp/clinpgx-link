"""MCP dataset-row search and retrieval contract tests."""

from __future__ import annotations

import base64

import pytest
from fastmcp import Client, FastMCP

from clinpgx_link.content.assets import AssetReference
from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp.facade import create_mcp
from clinpgx_link.models import SourceResponse
from tests.unit.test_repository import _repository


def _record_server(repository, store):
    from clinpgx_link.mcp.dataset_record_tools import register_dataset_record_tools

    server = FastMCP("record-test", mask_error_details=True, dereference_schemas=False)
    register_dataset_record_tools(server, repository, store)
    return server


@pytest.mark.asyncio
async def test_search_returns_complete_standard_rows_and_two_pages(tmp_path):
    repository, built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_record_server(repository, store)) as client:
            first = await client.call_tool(
                "search_dataset",
                {"dataset_id": "data/genes.zip", "member": "genes.tsv", "limit": 1},
            )
            payload = first.structured_content
            row = payload["results"][0]
            assert {"record_id", "dataset_id", "member", "ordinal", "fields", "id"} <= row.keys()
            assert row["dataset_id"] == "data/genes.zip"
            assert row["member"]["text"] == "genes.tsv"
            assert row["id"] == "PA124"
            assert payload["_meta"]["pagination"]["total_count"] == 2
            assert payload["_meta"]["pagination"]["snapshot_id"] == built.snapshot_id
            asset = AssetReference.decode(row["content_ref"])
            assert asset.snapshot_id == built.snapshot_id
            assert asset.dataset_id == "data/genes.zip"
            assert asset.member == "genes.tsv"
            second = await client.call_tool(
                "search_dataset",
                {
                    "dataset_id": "data/genes.zip",
                    "member": "genes.tsv",
                    "limit": 1,
                    "cursor": payload["_meta"]["pagination"]["next_cursor"],
                },
            )
            assert second.structured_content["results"][0]["record_id"] != row["record_id"]
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_search_cursor_binds_selectors_and_snapshot(tmp_path):
    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_record_server(repository, store)) as client:
            args = {"dataset_id": "data/genes.zip", "member": "genes.tsv", "limit": 1}
            first = await client.call_tool("search_dataset", args)
            cursor = first.structured_content["_meta"]["pagination"]["next_cursor"]
            changed = await client.call_tool(
                "search_dataset",
                {**args, "filters": {"id": "PA124"}, "cursor": cursor},
                raise_on_error=False,
            )
            assert changed.is_error
            assert changed.structured_content["error_code"] == "invalid_input"
            repository._snapshot_id = "sha256:" + "f" * 64
            stale = await client.call_tool(
                "search_dataset", {**args, "cursor": cursor}, raise_on_error=False
            )
            assert stale.is_error
            assert stale.structured_content["error_code"] == "upstream_unavailable"
            assert stale.structured_content["subtype"] == "snapshot_mismatch"
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_search_rejects_typo_filter_and_cursor_offset(tmp_path):
    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_record_server(repository, store)) as client:
            args = {
                "dataset_id": "data/genes.zip",
                "member": "genes.tsv",
                "filters": {"Symobl": "CYP2C19"},
            }
            typo = await client.call_tool("search_dataset", args, raise_on_error=False)
            assert typo.is_error
            assert typo.structured_content["error_code"] == "invalid_input"
            first = await client.call_tool("search_dataset", {**args, "filters": {}, "limit": 1})
            cursor = first.structured_content["_meta"]["pagination"]["next_cursor"]
            with_offset = await client.call_tool(
                "search_dataset",
                {**args, "filters": {}, "limit": 1, "cursor": cursor, "offset": 1},
                raise_on_error=False,
            )
            assert with_offset.is_error
            assert with_offset.structured_content["error_code"] == "invalid_input"
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_get_record_preserves_row_and_pointer_is_explicitly_derived(tmp_path):
    repository, built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        row = repository.search("data/genes.zip", member="genes.tsv", limit=1).value[0]
        async with Client(_record_server(repository, store)) as client:
            call = await client.call_tool("get_dataset_record", {"record_id": row["record_id"]})
            result = call.structured_content["result"]
            assert result["record_id"] == row["record_id"]
            assert result["fields"]["Symbol"]["text"] == row["fields"]["Symbol"]
            assert result["fields"].keys() == row["fields"].keys()
            assert result["content_ref"].startswith("asset:")
            assert call.structured_content["_meta"]["snapshot_id"] == built.snapshot_id
            selected = await client.call_tool(
                "get_dataset_record",
                {"record_id": row["record_id"], "pointer": "/fields/Symbol"},
            )
            selected_value = selected.structured_content["result"]["selected"]
            assert selected_value["pointer"] == "/fields/Symbol"
            assert selected_value["representation"] == "normalized_record_json"
            assert selected_value["derived"] is True
            assert selected_value["data"]["text"] == '"CYP2C19"'
            assert selected_value["content_ref"].startswith("content:")
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_oversized_field_is_a_progressing_recoverable_descriptor(tmp_path, monkeypatch):
    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    original = repository.get_record
    row = repository.search("data/genes.zip", member="genes.tsv", limit=1).value[0]
    response = original(row["record_id"])
    oversized = dict(response.value)
    oversized["fields"] = {**oversized["fields"], "Evidence": "x" * 148_743}
    monkeypatch.setattr(
        repository,
        "get_record",
        lambda record_id, expected_snapshot=None: SourceResponse(
            oversized, response.source, response.details
        ),
    )
    try:
        server = create_mcp(content_store=store, repository=repository)
        from clinpgx_link.mcp.dataset_record_tools import register_dataset_record_tools

        register_dataset_record_tools(server, repository, store)
        async with Client(server) as client:
            call = await client.call_tool("get_dataset_record", {"record_id": row["record_id"]})
            result = call.structured_content["result"]
            descriptor = result["fields"]
            assert descriptor["deferred_content"] is True
            assert descriptor["pointer"] == "/fields"
            assert descriptor["representation"] == "normalized_record_json"
            assert descriptor["content_ref"].startswith("content:")
            recovered = await client.call_tool(
                "get_source_content",
                {
                    "content_ref": descriptor["content_ref"],
                    "pointer": descriptor["pointer"],
                    "representation": "structure",
                    "length": 8192,
                },
            )
            assert recovered.structured_content["result"]["type"] == "object"
            assert recovered.structured_content["result"]["length"] == len(oversized["fields"])
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [
        {f"F{i}": "short" for i in range(150)},
        {f"F{i}": "aggregate" * 125 for i in range(120)},
    ],
)
async def test_aggregate_field_budget_uses_progressing_fields_descriptor(
    tmp_path, monkeypatch, fields
):
    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    original = repository.get_record
    row = repository.search("data/genes.zip", member="genes.tsv", limit=1).value[0]
    response = original(row["record_id"])
    oversized = dict(response.value)
    oversized["fields"] = fields
    monkeypatch.setattr(
        repository,
        "get_record",
        lambda record_id, expected_snapshot=None: SourceResponse(
            oversized, response.source, response.details
        ),
    )
    try:
        server = create_mcp(content_store=store, repository=repository)
        from clinpgx_link.mcp.dataset_record_tools import register_dataset_record_tools

        register_dataset_record_tools(server, repository, store)
        async with Client(server) as client:
            call = await client.call_tool("get_dataset_record", {"record_id": row["record_id"]})
            descriptor = call.structured_content["result"]["fields"]
            assert descriptor["deferred_content"] is True
            assert descriptor["pointer"] == "/fields"
            assert descriptor["fallback_args"]["representation"] == "structure"
            recovered = await client.call_tool("get_source_content", descriptor["fallback_args"])
            assert recovered.structured_content["result"]["type"] == "object"
            assert recovered.structured_content["result"]["length"] == len(fields)
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("field_name", ["hostile/name~key", "x" * 5000])
async def test_hostile_field_key_is_fenced_and_recovered_without_raw_pointer(
    tmp_path, monkeypatch, field_name
):
    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    original = repository.get_record
    row = repository.search("data/genes.zip", member="genes.tsv", limit=1).value[0]
    response = original(row["record_id"])
    oversized = dict(response.value)
    oversized["fields"] = {field_name: "value"}
    monkeypatch.setattr(
        repository,
        "get_record",
        lambda record_id, expected_snapshot=None: SourceResponse(
            oversized, response.source, response.details
        ),
    )
    try:
        server = create_mcp(content_store=store, repository=repository)
        from clinpgx_link.mcp.dataset_record_tools import register_dataset_record_tools

        register_dataset_record_tools(server, repository, store)
        async with Client(server) as client:
            call = await client.call_tool("get_dataset_record", {"record_id": row["record_id"]})
            descriptor = call.structured_content["result"]["fields"]
            assert descriptor["deferred_content"] is True
            if len(field_name) > 4096:
                assert descriptor["pointer"] == ""
                assert descriptor["fallback_args"]["representation"] == "base64"
                recovered = await client.call_tool(
                    "get_source_content", {**descriptor["fallback_args"], "length": 8192}
                )
                raw = base64.b64decode(recovered.structured_content["result"]["base64"])
                assert field_name.encode() in raw
            else:
                assert descriptor["pointer"] == "/fields"
                recovered = await client.call_tool(
                    "get_source_content", descriptor["fallback_args"]
                )
                item = recovered.structured_content["result"]["items"][0]
                assert item["key"]["text"] == field_name
    finally:
        repository.close()
        store.close()
