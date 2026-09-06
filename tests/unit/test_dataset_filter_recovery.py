"""Dataset-scoped filter recovery and safety contract tests."""

from __future__ import annotations

import json

import pytest
from fastmcp import Client

from clinpgx_link.content.store import ContentStore
from clinpgx_link.exceptions import DatasetFilterError, InvalidInputError, NotFoundError
from clinpgx_link.mcp.facade import create_mcp
from clinpgx_link.mcp.search_diagnostics import dataset_search_error_result
from tests.unit.test_repository import _mixed_repository, _repository


@pytest.mark.parametrize(
    ("kwargs", "choices"),
    [
        ({"member": "genes.tsv", "filters": {"chemical": "x"}}, ("gene", "id", "name")),
        ({"filters": {"Symbol": "x"}}, ("gene", "id", "name")),
        ({"member": "genes.tsv", "filters": {"Symobl": "x"}}, ("gene", "id", "name")),
        (
            {"member": "genes.tsv", "filters": {"Symbol": "x"}, "match": "member"},
            ("gene", "id", "name"),
        ),
    ],
)
def test_repository_filter_contract_failures_carry_only_validated_context(
    tmp_path, kwargs, choices
):
    repository, _ = _repository(tmp_path)
    try:
        with pytest.raises(DatasetFilterError) as failure:
            repository.search("data/genes.zip", **kwargs)
        assert failure.value.error_code == "invalid_input"
        assert failure.value.retryable is False
        assert failure.value.subtype == "unsupported_dataset_filters"
        assert failure.value.field == "filters"
        assert failure.value.dataset_id == "data/genes.zip"
        assert failure.value.known_filters == choices
    finally:
        repository.close()


def test_non_tabular_filter_failures_keep_dataset_specific_discovery(tmp_path):
    repository, _ = _mixed_repository(tmp_path)
    try:
        with pytest.raises(DatasetFilterError) as json_failure:
            repository.search(
                "data/pathways.json.zip",
                member="pathways.json",
                filters={"source_column": "x"},
            )
        assert json_failure.value.known_filters == ("chemical", "gene", "id", "name")

        with pytest.raises(DatasetFilterError) as binary_failure:
            repository.search(
                "data/genes.zip",
                member="zz-unsupported.bin",
                filters={"source_column": "x"},
            )
        assert binary_failure.value.known_filters == ()
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("dataset_id", "kwargs", "error_type"),
    [
        ("data/genes.zip", {"filters": {"gene": ""}}, InvalidInputError),
        ("data/genes.zip", {"filters": {"gene": False}}, InvalidInputError),
        ("data/genes.zip", {"limit": 0}, InvalidInputError),
        ("data/missing.zip", {"filters": {"gene": "x"}}, NotFoundError),
        ("data/genes.zip", {"member": "missing.tsv", "filters": {"gene": "x"}}, NotFoundError),
    ],
)
def test_unrelated_dataset_search_errors_are_not_filter_recovery_errors(
    tmp_path, dataset_id, kwargs, error_type
):
    repository, _ = _repository(tmp_path)
    try:
        with pytest.raises(error_type) as failure:
            repository.search(dataset_id, **kwargs)
        assert type(failure.value) is error_type
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_filter_recovery_replays_get_dataset_and_metadata_backed_lookup(tmp_path):
    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    rejected_key = "Symobl<script>IGNORE"
    rejected_value = "DO_NOT_REFLECT_VALUE"
    try:
        async with Client(create_mcp(content_store=store, repository=repository)) as client:
            failure = await client.call_tool(
                "search_dataset",
                {
                    "dataset_id": "data/genes.zip",
                    "member": "genes.tsv",
                    "filters": {rejected_key: rejected_value},
                },
                raise_on_error=False,
            )
            payload = failure.structured_content
            serialized = failure.content[0].text
            assert json.loads(serialized) == payload
            assert payload["error_code"] == "invalid_input"
            assert payload["subtype"] == "unsupported_dataset_filters"
            assert payload["retryable"] is False
            assert rejected_key not in serialized
            assert rejected_value not in serialized
            command = payload["recovery"]["next_commands"][0]
            assert command == {
                "tool": "get_dataset",
                "arguments": {"dataset_id": "data/genes.zip"},
            }
            assert payload["fallback_tool"] == command["tool"]
            assert payload["fallback_args"] == command["arguments"]
            assert payload["_meta"]["next_commands"] == [command]

            unsupported = await client.call_tool(
                "search_dataset",
                {
                    "dataset_id": "data/genes.zip",
                    "member": "genes.tsv",
                    "filters": {"chemical": "clopidogrel"},
                },
                raise_on_error=False,
            )
            assert unsupported.structured_content["error_code"] == "invalid_input"
            assert unsupported.structured_content["subtype"] == "unsupported_dataset_filters"
            assert unsupported.structured_content["retryable"] is False
            assert unsupported.structured_content["recovery"]["valid_choices"]["filters"] == [
                "gene",
                "id",
                "name",
            ]

            described = await client.call_tool(command["tool"], command["arguments"])
            description = described.structured_content["result"]
            member = next(
                item for item in description["members"] if item["path"]["text"] == "genes.tsv"
            )
            choices = payload["recovery"]["valid_choices"]["filters"]
            assert choices == description["supported_filters"] == member["supported_filters"]
            filter_name = next(item for item in choices if item == "gene")
            found = await client.call_tool(
                "search_dataset",
                {
                    "dataset_id": description["dataset_id"],
                    "member": member["path"]["text"],
                    "filters": {filter_name: "CYP2C19"},
                    "match": "exact",
                },
            )
            assert found.structured_content["success"] is True
            assert found.structured_content["_meta"]["pagination"]["total_count"] == 1
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_distinct_dataset_member_recovery_uses_its_exact_known_filters(tmp_path):
    repository, _ = _repository(tmp_path)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(create_mcp(content_store=store, repository=repository)) as client:
            failure = await client.call_tool(
                "search_dataset",
                {
                    "dataset_id": "data/summaryAnnotations.zip",
                    "member": "summary_ann_evidence.tsv",
                    "filters": {"chemical": "clopidogrel"},
                },
                raise_on_error=False,
            )
            payload = failure.structured_content
            choices = payload["recovery"]["valid_choices"]["filters"]
            assert choices == ["annotation_id", "id", "literature"]
            command = payload["recovery"]["next_commands"][0]
            described = await client.call_tool(command["tool"], command["arguments"])
            member = next(
                item
                for item in described.structured_content["result"]["members"]
                if item["path"]["text"] == "summary_ann_evidence.tsv"
            )
            assert member["supported_filters"] == choices
            assert choices != described.structured_content["result"]["supported_filters"]
    finally:
        repository.close()
        store.close()


def test_forged_dataset_filter_context_fails_closed_without_reflection():
    hostile_dataset = "data/IGNORE<script>.zip"
    hostile_choice = "IGNORE_PREVIOUS"
    forged = DatasetFilterError(
        dataset_id=hostile_dataset,
        known_filters=(hostile_choice,),
    )
    payload = dataset_search_error_result(forged).structured_content
    serialized = json.dumps(payload)
    assert payload["error_code"] == "invalid_input"
    assert payload["subtype"] == "unsupported_dataset_filters"
    assert payload["fallback_tool"] == "get_server_capabilities"
    assert payload["fallback_args"] == {}
    assert "recovery" not in payload
    assert hostile_dataset not in serialized
    assert hostile_choice not in serialized

    forged.dataset_id = "data/genes.zip"
    payload = dataset_search_error_result(forged).structured_content
    assert payload["fallback_tool"] == "get_server_capabilities"
    assert hostile_choice not in json.dumps(payload)

    forged.known_filters = ["gene"]
    payload = dataset_search_error_result(forged).structured_content
    assert payload["fallback_tool"] == "get_server_capabilities"

    del forged.dataset_id
    payload = dataset_search_error_result(forged).structured_content
    assert payload["fallback_tool"] == "get_server_capabilities"

    class NamedLikeDatasetFilterError(DatasetFilterError):
        pass

    subclass = NamedLikeDatasetFilterError(
        dataset_id="data/genes.zip", known_filters=("gene", "id", "name")
    )
    payload = dataset_search_error_result(subclass).structured_content
    assert payload["fallback_tool"] == "get_server_capabilities"


def test_filter_error_path_skips_row_query_and_success_keeps_one_row_query(tmp_path, monkeypatch):
    repository, _ = _repository(tmp_path)
    calls = 0
    original = repository._query_records

    def counted_query_records(**kwargs):
        nonlocal calls
        calls += 1
        return original(**kwargs)

    monkeypatch.setattr(repository, "_query_records", counted_query_records)
    try:
        with pytest.raises(DatasetFilterError):
            repository.search(
                "data/genes.zip",
                member="genes.tsv",
                filters={"Symobl": "CYP2C19"},
            )
        assert calls == 0

        repository.search(
            "data/genes.zip",
            member="genes.tsv",
            filters={"gene": "CYP2C19"},
        )
        assert calls == 1
    finally:
        repository.close()
