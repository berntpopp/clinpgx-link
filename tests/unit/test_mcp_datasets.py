"""MCP catalog and dataset-description contract tests."""

from __future__ import annotations

import base64

import pytest
from fastmcp import Client, FastMCP

from clinpgx_link.content.assets import AssetReference
from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp.facade import create_mcp
from tests.unit.test_repository import FIXTURES, _repository


def _dataset_server(repository, store):
    from clinpgx_link.mcp.dataset_tools import register_dataset_tools

    server = FastMCP(
        "dataset-test", mask_error_details=True, dereference_schemas=False
    )
    register_dataset_tools(server, repository, store)
    return server


@pytest.mark.asyncio
async def test_catalog_and_description_page_with_authenticated_cursor(tmp_path):
    repository, built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_dataset_server(repository, store)) as client:
            catalog = await client.call_tool("list_datasets", {})
            payload = catalog.structured_content
            assert payload["success"] is True
            assert [item["dataset_id"] for item in payload["results"]] == [
                "data/genes.zip",
                "data/guidelineAnnotations.json.zip",
                "data/relationships.zip",
                "data/summaryAnnotations.zip",
            ]
            assert payload["_meta"]["source_sha256"] == built.snapshot_id.removeprefix(
                "sha256:"
            )

            first = await client.call_tool(
                "get_dataset",
                {"dataset_id": "data/summaryAnnotations.zip", "limit": 1},
            )
            first_value = first.structured_content["result"]
            page = first.structured_content["_meta"]["pagination"]
            assert first_value["dataset_id"] == "data/summaryAnnotations.zip"
            assert page["total_count"] == 3
            assert page["offset"] == 0
            assert page["returned"] == 1
            assert page["has_more"] is True
            assert page["next_cursor"]

            second = await client.call_tool(
                "get_dataset",
                {
                    "dataset_id": "data/summaryAnnotations.zip",
                    "limit": 1,
                    "cursor": page["next_cursor"],
                },
            )
            second_page = second.structured_content["_meta"]["pagination"]
            assert second_page["offset"] == 1
            assert second_page["returned"] == 1
            assert second.structured_content["result"]["members"][0]["path"] != first_value[
                "members"
            ][0]["path"]
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_description_fences_member_and_field_prose_and_preserves_metadata(tmp_path):
    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_dataset_server(repository, store)) as client:
            call = await client.call_tool(
                "get_dataset", {"dataset_id": "data/genes.zip", "limit": 20}
            )
            result = call.structured_content["result"]
            assert result["dataset_id"] == "data/genes.zip"
            assert result["license_id"] == "operator-local-only"
            assert result["record_count"] == 2
            member = result["members"][0]
            assert member["path"]["kind"] == "untrusted_text"
            assert member["path"]["text"] == "genes.tsv"
            symbol = next(field for field in member["fields"] if field["name"]["text"] == "Symbol")
            assert symbol["name"]["kind"] == "untrusted_text"
            assert symbol["match_modes"] == ["exact"]
            assert symbol["tokenizer"] is None
            assert symbol["semantic_target"] == "gene"
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_archive_and_member_references_recover_exact_bytes(tmp_path):
    repository, built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        server = create_mcp(content_store=store, repository=repository)
        from clinpgx_link.mcp.dataset_tools import register_dataset_tools

        register_dataset_tools(server, repository, store)
        async with Client(server) as client:
            call = await client.call_tool(
                "get_dataset", {"dataset_id": "data/genes.zip"}
            )
            result = call.structured_content["result"]
            archive = AssetReference.decode(result["archive_ref"])
            member = result["members"][0]
            member_ref = AssetReference.decode(member["content_ref"])
            assert archive == AssetReference(
                built.snapshot_id, "data/genes.zip", None, result["sha256"]
            )
            assert member_ref.snapshot_id == built.snapshot_id
            assert member_ref.dataset_id == "data/genes.zip"
            assert member_ref.member == "genes.tsv"

            archive_call = await client.call_tool(
                "get_source_content",
                {
                    "content_ref": result["archive_ref"],
                    "representation": "base64",
                },
            )
            assert base64.b64decode(
                archive_call.structured_content["result"]["base64"]
            ) == (tmp_path / "inputs" / "genes.zip").read_bytes()
            member_call = await client.call_tool(
                "get_source_content",
                {"content_ref": member["content_ref"], "representation": "base64"},
            )
            assert base64.b64decode(
                member_call.structured_content["result"]["base64"]
            ) == (FIXTURES / "genes.tsv").read_bytes()
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_stale_cursor_is_rejected_after_snapshot_changes(tmp_path):
    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_dataset_server(repository, store)) as client:
            first = await client.call_tool(
                "get_dataset", {"dataset_id": "data/summaryAnnotations.zip", "limit": 1}
            )
            cursor = first.structured_content["_meta"]["pagination"]["next_cursor"]
            repository._snapshot_id = "sha256:" + "f" * 64
            stale = await client.call_tool(
                "get_dataset",
                {"dataset_id": "data/summaryAnnotations.zip", "cursor": cursor, "limit": 1},
                raise_on_error=False,
            )
            assert stale.is_error
            assert stale.structured_content["error_code"] == "upstream_unavailable"
            assert stale.structured_content["subtype"] == "snapshot_mismatch"
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_dataset_tools_remain_registered_and_typed_when_repository_missing(tmp_path):
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_dataset_server(None, store)) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert {"list_datasets", "get_dataset"} <= names
            for name, arguments in (
                ("list_datasets", {}),
                ("get_dataset", {"dataset_id": "data/genes.zip"}),
            ):
                result = await client.call_tool(name, arguments, raise_on_error=False)
                assert result.is_error
                assert result.structured_content["error_code"] == "upstream_unavailable"
                assert result.structured_content["subtype"] == "dataset_unavailable"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_response_modes_are_closed_and_do_not_change_selectors(tmp_path):
    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_dataset_server(repository, store)) as client:
            for mode in ("minimal", "compact", "standard", "full"):
                result = await client.call_tool(
                    "get_dataset",
                    {"dataset_id": "data/genes.zip", "response_mode": mode},
                )
                assert result.structured_content["result"]["response_mode"] == mode
            invalid = await client.call_tool(
                "get_dataset",
                {"dataset_id": "data/genes.zip", "response_mode": "verbose"},
                raise_on_error=False,
            )
            assert invalid.is_error
    finally:
        repository.close()
        store.close()
