"""MCP catalog and dataset-description contract tests."""

from __future__ import annotations

import base64
import hashlib

import pytest
from fastmcp import Client, FastMCP

from clinpgx_link.content.assets import AssetReference
from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp.facade import create_mcp
from clinpgx_link.models import SourceInfo, SourceResponse
from tests.unit.test_repository import (
    FIXTURES,
    GENES_RETRIEVED_AT,
    PATHWAYS_RETRIEVED_AT,
    _mixed_repository,
    _repository,
)


def _dataset_server(repository, store):
    from clinpgx_link.mcp.dataset_tools import register_dataset_tools

    server = FastMCP("dataset-test", mask_error_details=True, dereference_schemas=False)
    register_dataset_tools(server, repository, store)
    return server


@pytest.mark.asyncio
async def test_catalog_and_description_page_with_authenticated_cursor(tmp_path):
    repository, built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_dataset_server(repository, store)) as client:
            catalog = await client.call_tool("list_datasets", {"limit": 2})
            payload = catalog.structured_content
            assert payload["success"] is True
            assert [item["dataset_id"] for item in payload["results"]] == [
                "data/genes.zip",
                "data/guidelineAnnotations.json.zip",
            ]
            assert payload["_meta"]["source_sha256"] == built.snapshot_id.removeprefix("sha256:")
            catalog_page = payload["_meta"]["pagination"]
            assert catalog_page["total_count"] == 4
            assert catalog_page["returned"] == 2
            assert catalog_page["has_more"] is True
            next_catalog = await client.call_tool(
                "list_datasets",
                {"limit": 2, "cursor": catalog_page["next_cursor"]},
            )
            assert [item["dataset_id"] for item in next_catalog.structured_content["results"]] == [
                "data/relationships.zip",
                "data/summaryAnnotations.zip",
            ]
            repository._snapshot_id = "sha256:" + "f" * 64
            stale_catalog = await client.call_tool(
                "list_datasets",
                {"limit": 2, "cursor": catalog_page["next_cursor"]},
                raise_on_error=False,
            )
            assert stale_catalog.is_error
            assert stale_catalog.structured_content["subtype"] == "snapshot_mismatch"
            repository._snapshot_id = built.snapshot_id

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
            assert (
                second.structured_content["result"]["members"][0]["path"]
                != first_value["members"][0]["path"]
            )
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_mixed_catalog_rows_use_owning_archive_provenance_for_external_text(tmp_path):
    repository, built = _mixed_repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_dataset_server(repository, store)) as client:
            call = await client.call_tool("list_datasets", {"limit": 20})
            payload = call.structured_content
            genes = next(
                item for item in payload["results"] if item["dataset_id"] == "data/genes.zip"
            )

            assert payload["_meta"]["source_url"] == "https://www.clinpgx.org/downloads"
            assert payload["_meta"]["source_sha256"] == built.snapshot_id.removeprefix("sha256:")
            assert payload["_meta"]["retrieved_at"] == PATHWAYS_RETRIEVED_AT
            assert genes["provenance"] == {
                "dataset_source_url": "https://api.clinpgx.org/v1/download/file/data/genes.zip",
                "archive_sha256": hashlib.sha256(
                    (tmp_path / "inputs" / "genes.zip").read_bytes()
                ).hexdigest(),
                "published_at": "2026-09-05T00:37:36-07:00",
                "retrieved_at": GENES_RETRIEVED_AT,
                "retrieval_time_kind": "unknown",
                "acquired_at": None,
                "admitted_at": None,
                "source_scope": "dataset",
                "retrieval_time_scope": "source_recorded",
            }
            assert genes["warnings"][0]["text"] == "upstream_anomaly:genes_is_vip_all_yes"
            assert genes["limitations"][0]["text"].startswith("zz-unsupported.bin:")
            for item in [*genes["warnings"], *genes["limitations"]]:
                assert item["provenance"]["retrieved_at"] == GENES_RETRIEVED_AT
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_catalog_defaults_to_including_legacy_and_cursor_binds_selectors(
    tmp_path, monkeypatch
):
    repository, built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    source = SourceInfo(
        "ClinPGx local snapshot",
        "https://example.test/catalog",
        "2026-09-05T08:00:00Z",
        built.snapshot_id.removeprefix("sha256:"),
        "download",
        source_scope="snapshot",
        retrieval_time_scope="aggregate_snapshot",
    )
    catalog = [
        {
            "dataset_id": "data/current.zip",
            "file_name": "current.zip",
            "source_date": "2026-09-05",
            "byte_count": 1,
            "sha256": "a" * 64,
            "license_id": "test",
            "tier": "approved_registry",
            "record_count": 1,
            "limitations": [],
            "warnings": [],
        },
        {
            "dataset_id": "data/legacy.zip",
            "file_name": "legacy.zip",
            "source_date": "2020-01-01",
            "byte_count": 1,
            "sha256": "b" * 64,
            "license_id": "test",
            "tier": "legacy",
            "record_count": 1,
            "limitations": [],
            "warnings": [],
        },
    ]
    dataset_sources = {
        item["dataset_id"]: SourceInfo(
            "ClinPGx local snapshot",
            "https://api.clinpgx.org/v1/download/file/" + item["dataset_id"],
            "2026-09-05T07:00:00Z",
            item["sha256"],
            "download",
            source_scope="dataset",
        )
        for item in catalog
    }
    monkeypatch.setattr(
        repository,
        "list_datasets",
        lambda: SourceResponse(catalog, source, {"dataset_sources": dataset_sources}),
    )
    try:
        async with Client(_dataset_server(repository, store)) as client:
            default = await client.call_tool("list_datasets", {"limit": 20})
            assert [item["dataset_id"] for item in default.structured_content["results"]] == [
                "data/current.zip",
                "data/legacy.zip",
            ]
            assert [
                item["provenance"]["archive_sha256"]
                for item in default.structured_content["results"]
            ] == ["a" * 64, "b" * 64]
            first = await client.call_tool("list_datasets", {"limit": 1, "query": "data"})
            cursor = first.structured_content["_meta"]["pagination"]["next_cursor"]
            mismatch = await client.call_tool(
                "list_datasets",
                {"limit": 1, "query": "current", "cursor": cursor},
                raise_on_error=False,
            )
            assert mismatch.is_error
            assert mismatch.structured_content["error_code"] == "invalid_input"
            with_offset = await client.call_tool(
                "list_datasets",
                {"limit": 1, "cursor": cursor, "offset": 1},
                raise_on_error=False,
            )
            assert with_offset.is_error
            assert with_offset.structured_content["error_code"] == "invalid_input"
    finally:
        repository.close()
        store.close()


def _synthetic_description(dataset_id: str, members: list[dict]) -> dict:
    return {
        "dataset_id": dataset_id,
        "file_name": dataset_id.rsplit("/", 1)[-1],
        "source_url": "https://example.test/" + dataset_id,
        "source_date": "2026-09-05",
        "retrieved_at": "2026-09-05T08:00:00Z",
        "sha256": "c" * 64,
        "byte_count": 3,
        "license_id": "test",
        "tier": "approved_registry",
        "record_count": 3,
        "limitations": [],
        "warnings": [],
        "members": members,
    }


def _synthetic_member(path: str, *, fields=None, sheets=None) -> dict:
    result = {
        "path": path,
        "media_type": "text/tab-separated-values",
        "byte_count": 1,
        "sha256": hashlib.sha256(path.encode()).hexdigest(),
        "is_directory": False,
        "parser_status": "parsed",
        "limitation": None,
        "fields": fields or [],
        "record_count": 1,
    }
    if sheets is not None:
        result["sheets"] = sheets
    return result


@pytest.mark.asyncio
async def test_selected_member_only_and_structured_sheet_metadata_is_preserved(
    tmp_path, monkeypatch
):
    repository, built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    source = SourceInfo(
        "ClinPGx local snapshot",
        "https://example.test/dataset",
        "2026-09-05T08:00:00Z",
        built.snapshot_id.removeprefix("sha256:"),
        "download",
    )
    description = _synthetic_description(
        "data/synthetic.zip",
        [
            _synthetic_member(
                "one.tsv",
                fields=[
                    {
                        "name": "A",
                        "match_modes": ["exact"],
                        "tokenizer": None,
                        "semantic_target": None,
                    }
                ],
                sheets=[{"name": "Sheet 1", "columns": [{"name": "A", "width": 12}]}],
            ),
            _synthetic_member("two.tsv"),
            _synthetic_member("three.tsv"),
        ],
    )
    monkeypatch.setattr(
        repository,
        "describe",
        lambda dataset_id: SourceResponse(description, source, {"snapshot_id": built.snapshot_id}),
    )
    try:
        async with Client(_dataset_server(repository, store)) as client:
            result = await client.call_tool(
                "get_dataset", {"dataset_id": "data/synthetic.zip", "limit": 1}
            )
            members = result.structured_content["result"]["members"]
            assert len(members) == 1
            assert members[0]["path"]["text"] == "one.tsv"
            assert members[0]["sheets"][0]["name"]["text"] == "Sheet 1"
            assert members[0]["sheets"][0]["columns"][0]["name"]["text"] == "A"
            assert members[0]["sheets"][0]["columns"][0]["width"] == 12
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_deferred_member_metadata_has_retained_pointer(tmp_path, monkeypatch):
    repository, built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    source = SourceInfo(
        "ClinPGx local snapshot",
        "https://example.test/dataset",
        "2026-09-05T08:00:00Z",
        built.snapshot_id.removeprefix("sha256:"),
        "download",
    )
    description = _synthetic_description(
        "data/large.zip",
        [
            _synthetic_member(
                "large.tsv",
                fields=[
                    {
                        "name": "x" * 120_001,
                        "match_modes": ["exact"],
                        "tokenizer": None,
                        "semantic_target": None,
                    }
                ],
            )
        ],
    )
    monkeypatch.setattr(
        repository,
        "describe",
        lambda dataset_id: SourceResponse(description, source, {"snapshot_id": built.snapshot_id}),
    )
    try:
        async with Client(_dataset_server(repository, store)) as client:
            result = await client.call_tool("get_dataset", {"dataset_id": "data/large.zip"})
            member = result.structured_content["result"]["members"][0]
            assert member["deferred_metadata"] is True
            assert member["metadata_ref"].startswith("content:")
            assert member["metadata_pointer"] == "/members/0"
            assert member["fallback_args"] == {
                "content_ref": member["metadata_ref"],
                "pointer": "/members/0",
                "representation": "structure",
            }
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
            call = await client.call_tool("get_dataset", {"dataset_id": "data/genes.zip"})
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
            assert (
                base64.b64decode(archive_call.structured_content["result"]["base64"])
                == (tmp_path / "inputs" / "genes.zip").read_bytes()
            )
            member_call = await client.call_tool(
                "get_source_content",
                {"content_ref": member["content_ref"], "representation": "base64"},
            )
            assert (
                base64.b64decode(member_call.structured_content["result"]["base64"])
                == (FIXTURES / "genes.tsv").read_bytes()
            )
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
async def test_dataset_description_validates_snapshot_details_not_archive_digest(
    tmp_path, monkeypatch
):
    repository, built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    original = repository.describe
    try:
        async with Client(_dataset_server(repository, store)) as client:
            valid = await client.call_tool("get_dataset", {"dataset_id": "data/genes.zip"})
            assert valid.structured_content["_meta"]["source_scope"] == "dataset"
            assert valid.structured_content["_meta"][
                "source_sha256"
            ] != built.snapshot_id.removeprefix("sha256:")

            def mismatched(dataset_id):
                response = original(dataset_id)
                response.details["snapshot_id"] = "sha256:" + "f" * 64
                return response

            monkeypatch.setattr(repository, "describe", mismatched)
            invalid = await client.call_tool(
                "get_dataset", {"dataset_id": "data/genes.zip"}, raise_on_error=False
            )
            assert invalid.is_error
            assert invalid.structured_content["subtype"] == "snapshot_mismatch"
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


@pytest.mark.asyncio
async def test_get_dataset_members_include_member_sha256(tmp_path):
    repository, _built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_dataset_server(repository, store)) as client:
            result = await client.call_tool(
                "get_dataset", {"dataset_id": "data/genes.zip", "limit": 1}
            )
            member = result.structured_content["result"]["members"][0]
            assert "member_sha256" in member
            assert member["member_sha256"] == member["sha256"]
            assert member["member_sha256"] != member["path"]["raw_sha256"]
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_list_datasets_minimal_mode_omits_unparsed_member_limitations(tmp_path, monkeypatch):
    repository, _built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    raw_list = repository.list_datasets

    def _mock_list():
        resp = raw_list()
        for item in resp.value:
            if item["dataset_id"] == "data/genes.zip":
                item["limitations"] = [
                    "unparsed_member:foo.txt: unsupported",
                    "unparsed_member:bar.txt: unsupported",
                    "archive_scope_limitation",
                ]
        return resp

    monkeypatch.setattr(repository, "list_datasets", _mock_list)
    try:
        async with Client(_dataset_server(repository, store)) as client:
            res_minimal = await client.call_tool(
                "list_datasets", {"response_mode": "minimal", "limit": 10}
            )
            payload_min = res_minimal.structured_content
            genes_min = next(
                item for item in payload_min["results"] if item["dataset_id"] == "data/genes.zip"
            )
            assert len(genes_min["limitations"]) == 1
            assert genes_min["limitations"][0]["text"] == "archive_scope_limitation"
            assert genes_min.get("unparsed_member_count") == 2

            res_compact = await client.call_tool(
                "list_datasets", {"response_mode": "compact", "limit": 10}
            )
            payload_comp = res_compact.structured_content
            genes_comp = next(
                item for item in payload_comp["results"] if item["dataset_id"] == "data/genes.zip"
            )
            assert len(genes_comp["limitations"]) == 3
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_get_dataset_with_member_filter(tmp_path):
    repository, _built = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(_dataset_server(repository, store)) as client:
            res = await client.call_tool(
                "get_dataset", {"dataset_id": "data/genes.zip", "member": "genes.tsv"}
            )
            members = res.structured_content["result"]["members"]
            assert len(members) == 1
            assert members[0]["path"]["text"] == "genes.tsv"
            assert "member_sha256" in members[0]

            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "get_dataset",
                    {"dataset_id": "data/genes.zip", "member": "missing.tsv"},
                )
            assert "not_found" in str(exc_info.value)
    finally:
        repository.close()
        store.close()
