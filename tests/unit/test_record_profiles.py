"""Candidate-bound record-profile declarations and drift receipts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from tests.unit.test_builder import FIXTURES, RELEASE_TAG, _archive, _source


def _pharmcat_candidate(tmp_path: Path, body: bytes):
    from clinpgx_link.ingest.builder import build_snapshot

    inputs = tmp_path / "inputs"
    inputs.mkdir(parents=True)
    path = inputs / "pharmcat.zip"
    archive = _archive(path, {"phenotypes.json": body})
    return build_snapshot([_source(path)], tmp_path / "candidates", RELEASE_TAG), archive


def _receipt(database: Path) -> dict:
    with sqlite3.connect(database) as connection:
        raw = connection.execute(
            "SELECT value FROM metadata WHERE key='record_profile_validation_json'"
        ).fetchone()[0]
    return json.loads(raw)


def test_declarations_are_single_code_owned_authority() -> None:
    from clinpgx_link.data.record_profiles import RECORD_PROFILES, profile_for_row
    from clinpgx_link.mcp.dataset_record_fields import trusted_fields_for_row

    assert len(RECORD_PROFILES) == 9
    profile = profile_for_row("data/pharmcat.zip", "phenotypes.json", "/0/diplotypes/1")
    assert profile is not None
    assert profile.profile_id == "pharmcat.diplotype.v1"
    assert profile.required_fields == (
        "diplotype",
        "diplotypekey",
        "generesult",
        "lookupkey",
        "phenotype",
    )
    assert profile.optional_fields == ("activityScore",)
    assert profile.modes.full.include_all_reachable is True
    assert trusted_fields_for_row(
        {
            "dataset_id": "data/pharmcat.zip",
            "member": "phenotypes.json",
            "json_pointer": "/0/diplotypes/1",
        }
    ) == frozenset((*profile.required_fields, *profile.optional_fields))
    assert profile_for_row("data/pharmcat.zip", "phenotypes.json", "/0/namedAlleles/0") is None


@pytest.mark.asyncio
async def test_current_haplotype_members_share_active_profile_and_selection(tmp_path: Path) -> None:
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.data.repository import DatasetRepository
    from clinpgx_link.ingest.builder import build_snapshot
    from clinpgx_link.mcp.facade import create_mcp

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    body = (FIXTURES / "clinpgx_haplotypes.tsv").read_bytes()
    path = inputs / "clinpgxHaplotypes.zip"
    _archive(
        path,
        {
            "clinpgxHaplotypes_named_alleles.tsv": body,
            "clinpgxHaplotypes_star_alleles.tsv": body,
        },
    )
    built = build_snapshot([_source(path)], tmp_path / "candidates", RELEASE_TAG)
    receipt = _receipt(built.database)
    assert receipt["gate_status"] == "passing"
    assert [item["member"] for item in receipt["profiles"]] == [
        "clinpgxHaplotypes_named_alleles.tsv",
        "clinpgxHaplotypes_star_alleles.tsv",
    ]
    assert {item["status"] for item in receipt["profiles"]} == {"active"}

    repository = DatasetRepository(built.database)
    store = ContentStore(tmp_path / "content.sqlite")
    try:
        async with Client(create_mcp(content_store=store, repository=repository)) as client:
            for member in (
                "clinpgxHaplotypes_named_alleles.tsv",
                "clinpgxHaplotypes_star_alleles.tsv",
            ):
                row = repository.search("data/clinpgxHaplotypes.zip", member=member, limit=1).value[
                    0
                ]
                call = await client.call_tool(
                    "get_dataset_record",
                    {
                        "record_id": row["record_id"],
                        "include_fields": ["Accession ID", "Gene", "Allele Name"],
                    },
                )
                result = call.structured_content["result"]
                assert [item["status"] for item in result["selections"]] == [
                    "value",
                    "value",
                    "value",
                ]
                assert all(
                    item["original_locator"]["content_ref"] == result["content_ref"]
                    for item in result["selections"]
                )
    finally:
        repository.close()
        store.close()


def test_pharmcat_candidate_receipt_is_active_and_bound_to_retained_bytes(tmp_path: Path) -> None:
    from clinpgx_link.data.repository import DatasetRepository

    body = (FIXTURES / "pharmcat_phenotypes.json").read_bytes()
    built, archive = _pharmcat_candidate(tmp_path, body)
    receipt = _receipt(built.database)

    assert built.manifest["record_profile_validation"] == receipt
    assert receipt["schema_version"] == 1
    assert receipt["snapshot_id"] == built.snapshot_id
    assert receipt["gate_status"] == "passing"
    assessment = receipt["profiles"][0]
    assert assessment == {
        "profile_id": "pharmcat.diplotype.v1",
        "dataset_id": "data/pharmcat.zip",
        "member": "phenotypes.json",
        "shape_id": "diplotype",
        "status": "active",
        "missing_required_fields": [],
    }
    assert receipt["profile_definition_digest"].startswith("sha256:")
    assert receipt["unprofiled_shapes"] == [
        {
            "dataset_id": "data/pharmcat.zip",
            "member": "phenotypes.json",
            "shape_id": "unprofiled_json",
        }
    ]

    with sqlite3.connect(built.database) as connection:
        archive_row = connection.execute(
            "SELECT raw,sha256 FROM source_archive WHERE dataset_id='data/pharmcat.zip'"
        ).fetchone()
        member_row = connection.execute(
            "SELECT raw,sha256,parser_status,record_count FROM source_member "
            "WHERE dataset_id='data/pharmcat.zip' AND path='phenotypes.json'"
        ).fetchone()
    assert archive_row == (archive, hashlib.sha256(archive).hexdigest())
    assert member_row == (body, hashlib.sha256(body).hexdigest(), "indexed", 4)

    repository = DatasetRepository(built.database)
    try:
        state = repository.record_profile("data/pharmcat.zip", "phenotypes.json", "/0/diplotypes/0")
        assert state is not None
        assert state["status"] == "active"
        row = repository.search(
            "data/pharmcat.zip", member="phenotypes.json", filters={"name": "*1/*1"}
        ).value[0]
        assert row["json_pointer"] == "/0/diplotypes/0"
        assert row["fields"]["diplotypekey"] == {"*1": 2}
        assert repository.asset_content("data/pharmcat.zip", member="phenotypes.json").value == body
    finally:
        repository.close()


def test_one_incomplete_pharmcat_row_drifts_without_losing_index_or_bytes(tmp_path: Path) -> None:
    from clinpgx_link.data.repository import DatasetRepository

    original = (FIXTURES / "pharmcat_phenotypes.json").read_bytes()
    document = json.loads(original)
    document[0]["diplotypes"][0].pop("lookupkey")
    body = json.dumps(document, separators=(",", ":")).encode()
    built, archive = _pharmcat_candidate(tmp_path, body)
    receipt = _receipt(built.database)

    assert receipt["gate_status"] == "nonpassing"
    assert receipt["profiles"][0]["status"] == "profile_drift"
    assert receipt["profiles"][0]["missing_required_fields"] == ["lookupkey"]
    assert "*1/*1" not in json.dumps(receipt, sort_keys=True)

    with sqlite3.connect(built.database) as connection:
        assert connection.execute(
            "SELECT raw,sha256 FROM source_archive WHERE dataset_id='data/pharmcat.zip'"
        ).fetchone() == (archive, hashlib.sha256(archive).hexdigest())
        assert connection.execute(
            "SELECT raw,sha256,parser_status,record_count FROM source_member "
            "WHERE dataset_id='data/pharmcat.zip' AND path='phenotypes.json'"
        ).fetchone() == (body, hashlib.sha256(body).hexdigest(), "indexed", 4)

    repository = DatasetRepository(built.database)
    try:
        described = repository.describe("data/pharmcat.zip").value
        assert described["profile_gate_status"] == "nonpassing"
        profile = described["members"][0]["record_profiles"][0]
        assert profile["status"] == "profile_drift"
        assert profile["missing_required_fields"] == ["lookupkey"]
        assert (
            repository.get_record(
                repository.search(
                    "data/pharmcat.zip", member="phenotypes.json", filters={"name": "*1/*1"}
                ).value[0]["record_id"]
            ).value["json_pointer"]
            == "/0/diplotypes/0"
        )
    finally:
        repository.close()


def test_optional_absent_and_extra_json_key_do_not_grant_or_remove_authority(
    tmp_path: Path,
) -> None:
    from clinpgx_link.data.repository import DatasetRepository
    from clinpgx_link.mcp.dataset_record_fields import trusted_fields_for_row

    body = json.dumps(
        [
            {
                "gene": "TPMT",
                "diplotypes": [
                    {
                        "diplotype": "*1/*1",
                        "diplotypekey": {"*1": 2},
                        "generesult": "Normal Metabolizer",
                        "lookupkey": "Normal Metabolizer",
                        "phenotype": "Normal Metabolizer",
                        "sourceAddedKey": "not authoritative",
                    }
                ],
            }
        ],
        separators=(",", ":"),
    ).encode()
    built, _ = _pharmcat_candidate(tmp_path, body)
    assert _receipt(built.database)["gate_status"] == "passing"
    repository = DatasetRepository(built.database)
    try:
        row = repository.search("data/pharmcat.zip", member="phenotypes.json").value
        diplotype = next(item for item in row if item.get("json_pointer") == "/0/diplotypes/0")
        trusted = trusted_fields_for_row(diplotype)
        assert "activityScore" in trusted
        assert "sourceAddedKey" not in trusted
    finally:
        repository.close()


def test_declared_shape_with_no_matching_row_is_drifted(tmp_path: Path) -> None:
    body = b'[{"gene":"TPMT","diplotypes":[]}]'
    built, _ = _pharmcat_candidate(tmp_path, body)
    receipt = _receipt(built.database)

    assert receipt["gate_status"] == "nonpassing"
    assert receipt["profiles"][0]["status"] == "profile_drift"
    assert receipt["profiles"][0]["missing_required_fields"] == [
        "diplotype",
        "diplotypekey",
        "generesult",
        "lookupkey",
        "phenotype",
    ]


def test_tabular_missing_required_header_is_drift_not_parser_failure(tmp_path: Path) -> None:
    from clinpgx_link.ingest.builder import build_snapshot

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    body = b"PharmGKB Accession Id\tName\nPA1\tGene One\n"
    path = inputs / "genes.zip"
    _archive(path, {"genes.tsv": body})
    built = build_snapshot([_source(path)], tmp_path / "candidates", RELEASE_TAG)
    receipt = _receipt(built.database)

    assert receipt["gate_status"] == "nonpassing"
    assert receipt["profiles"][0]["missing_required_fields"] == ["Symbol"]
    with sqlite3.connect(built.database) as connection:
        assert connection.execute(
            "SELECT raw,parser_status,record_count FROM source_member"
        ).fetchone() == (body, "indexed", 1)


def test_receipt_from_another_snapshot_cannot_confer_active_authority(tmp_path: Path) -> None:
    from clinpgx_link.data.repository import DatasetRepository

    original = (FIXTURES / "pharmcat_phenotypes.json").read_bytes()
    active, _ = _pharmcat_candidate(tmp_path / "active", original)
    changed = json.loads(original)
    changed[0]["diplotypes"][0].pop("lookupkey")
    drift, _ = _pharmcat_candidate(
        tmp_path / "drift", json.dumps(changed, separators=(",", ":")).encode()
    )
    transplanted = _receipt(active.database)
    assert transplanted["snapshot_id"] == active.snapshot_id
    assert active.snapshot_id != drift.snapshot_id
    with sqlite3.connect(drift.database) as connection:
        connection.execute(
            "UPDATE metadata SET value=? WHERE key='record_profile_validation_json'",
            (json.dumps(transplanted, separators=(",", ":")),),
        )
        connection.commit()

    repository = DatasetRepository(drift.database)
    try:
        described = repository.describe("data/pharmcat.zip").value
        assert described["profile_gate_status"] == "unknown"
        assert described["members"][0]["record_profile_status"] == "profile_drift"
        assert (
            repository.record_profile("data/pharmcat.zip", "phenotypes.json", "/0/diplotypes/1")[
                "status"
            ]
            == "profile_drift"
        )
    finally:
        repository.close()


def test_multiple_profiles_on_one_member_match_all_selectors_without_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from clinpgx_link.data import profile_validation, record_profiles
    from clinpgx_link.data.record_profiles import (
        FieldDeclaration,
        ModeFieldPolicy,
        ProfileModes,
        ShapeSelector,
    )

    diplotype = record_profiles.profile_for_row(
        "data/pharmcat.zip", "phenotypes.json", "/0/diplotypes/0"
    )
    assert diplotype is not None
    second = replace(
        diplotype,
        profile_id="test.pharmcat.named_allele.v1",
        shape_id="named_allele",
        selector=ShapeSelector(
            "json_pointer",
            r"/[0-9]+/namedAlleles/[0-9]+",
            "Test-only second shape.",
        ),
        fields=(FieldDeclaration("name", True, "Test name.", "Test identity."),),
        modes=ProfileModes(
            *(ModeFieldPolicy(("name",)) for _ in range(3)),
            ModeFieldPolicy(("name",), include_all_reachable=True),
        ),
        description="Test-only second profile sharing the real member.",
    )
    profiles = (diplotype, second)
    monkeypatch.setattr(profile_validation, "RECORD_PROFILES", profiles)
    monkeypatch.setattr(
        record_profiles,
        "_PROFILES_BY_MEMBER",
        {("data/pharmcat.zip", "phenotypes.json"): profiles},
    )
    monkeypatch.setattr(
        record_profiles,
        "_PROFILES_BY_KEY",
        {(profile.dataset_id, profile.member, profile.shape_id): profile for profile in profiles},
        raising=False,
    )
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE source_member(dataset_id,path,headers_json,parser_status,record_count);"
        "CREATE TABLE record(dataset_id,member,ordinal,json_pointer,fields_json);"
    )
    connection.execute(
        "INSERT INTO source_member VALUES (?,?,?,?,?)",
        ("data/pharmcat.zip", "phenotypes.json", None, "indexed", 2),
    )
    connection.executemany(
        "INSERT INTO record VALUES (?,?,?,?,?)",
        [
            (
                "data/pharmcat.zip",
                "phenotypes.json",
                1,
                "/0/diplotypes/0",
                json.dumps(
                    {
                        "diplotype": "*1/*1",
                        "diplotypekey": {"*1": 2},
                        "generesult": "Normal Metabolizer",
                        "lookupkey": "Normal Metabolizer",
                        "phenotype": "Normal Metabolizer",
                    }
                ),
            ),
            (
                "data/pharmcat.zip",
                "phenotypes.json",
                2,
                "/0/namedAlleles/0",
                '{"name":"*1"}',
            ),
        ],
    )
    try:
        receipt = profile_validation.validate_candidate_profiles(connection, "sha256:" + "a" * 64)
    finally:
        connection.close()

    assert [item["profile_id"] for item in receipt["profiles"]] == [
        "pharmcat.diplotype.v1",
        "test.pharmcat.named_allele.v1",
    ]
    assert {item["status"] for item in receipt["profiles"]} == {"active"}
    assert receipt["unprofiled_shapes"] == []
    assert (
        record_profiles.profile_for_row("data/pharmcat.zip", "phenotypes.json", "/0/namedAlleles/0")
        == second
    )
    assert (
        record_profiles.profile_for_shape("data/pharmcat.zip", "phenotypes.json", "named_allele")
        == second
    )


@pytest.mark.parametrize("receipt_kind", ["absent", "digest_mismatch", "malformed"])
def test_repository_invalid_receipt_never_confers_active_authority(
    tmp_path: Path, receipt_kind: str
) -> None:
    from clinpgx_link.data.repository import DatasetRepository

    built, _ = _pharmcat_candidate(tmp_path, (FIXTURES / "pharmcat_phenotypes.json").read_bytes())
    with sqlite3.connect(built.database) as connection:
        if receipt_kind == "absent":
            connection.execute("DELETE FROM metadata WHERE key='record_profile_validation_json'")
        elif receipt_kind == "digest_mismatch":
            receipt = _receipt(built.database)
            receipt["profile_definition_digest"] = "sha256:" + "0" * 64
            connection.execute(
                "UPDATE metadata SET value=? WHERE key='record_profile_validation_json'",
                (json.dumps(receipt),),
            )
        else:
            connection.execute(
                "UPDATE metadata SET value=? WHERE key='record_profile_validation_json'",
                ('{"schema_version":1,"gate_status":"passing","profiles":"bad"}',),
            )
        connection.commit()

    repository = DatasetRepository(built.database)
    try:
        described = repository.describe("data/pharmcat.zip").value
        assert described["profile_gate_status"] == "unknown"
        assert described["members"][0]["record_profile_status"] == "profile_drift"
        assert described["members"][0]["record_profiles"][0]["status"] == "profile_drift"
        assert (
            repository.record_profile("data/pharmcat.zip", "phenotypes.json", "/0/diplotypes/0")[
                "status"
            ]
            == "profile_drift"
        )
        assert (
            repository.asset_content("data/pharmcat.zip", member="phenotypes.json").value
            == (FIXTURES / "pharmcat_phenotypes.json").read_bytes()
        )
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_real_get_dataset_discloses_candidate_profile_state_within_bound(
    tmp_path: Path,
) -> None:
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.data.repository import DatasetRepository
    from clinpgx_link.mcp.dataset_tools import register_dataset_tools

    document = json.loads((FIXTURES / "pharmcat_phenotypes.json").read_bytes())
    document[0]["diplotypes"][1].pop("lookupkey")
    built, _ = _pharmcat_candidate(tmp_path, json.dumps(document, separators=(",", ":")).encode())
    repository = DatasetRepository(built.database)
    store = ContentStore(tmp_path / "content.sqlite")
    server = FastMCP("profile-test", mask_error_details=True, dereference_schemas=False)
    register_dataset_tools(server, repository, store)
    try:
        async with Client(server) as client:
            call = await client.call_tool("get_dataset", {"dataset_id": "data/pharmcat.zip"})
        payload = call.structured_content
        assert payload["success"] is True
        assert payload["result"]["profile_gate_status"] == "nonpassing"
        profile = payload["result"]["members"][0]["record_profiles"][0]
        assert profile["status"] == "profile_drift"
        assert profile["missing_required_fields"] == ["lookupkey"]
        assert payload["result"]["members"][0]["content_ref"].startswith("asset:")
        assert len(json.dumps(payload, separators=(",", ":")).encode()) < 100_000
    finally:
        repository.close()
        store.close()


@pytest.mark.asyncio
async def test_real_get_dataset_active_profile_matches_repository_metadata(tmp_path: Path) -> None:
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.data.repository import DatasetRepository
    from clinpgx_link.mcp.dataset_tools import register_dataset_tools

    built, _ = _pharmcat_candidate(tmp_path, (FIXTURES / "pharmcat_phenotypes.json").read_bytes())
    repository = DatasetRepository(built.database)
    expected = repository.describe("data/pharmcat.zip").value
    store = ContentStore(tmp_path / "content.sqlite")
    server = FastMCP("active-profile-test", mask_error_details=True, dereference_schemas=False)
    register_dataset_tools(server, repository, store)
    try:
        async with Client(server) as client:
            call = await client.call_tool("get_dataset", {"dataset_id": "data/pharmcat.zip"})
        result = call.structured_content["result"]
        assert result["profile_gate_status"] == expected["profile_gate_status"] == "passing"
        assert result["members"][0]["record_profile_status"] == "active"
        assert result["members"][0]["record_profiles"] == expected["members"][0]["record_profiles"]
        assert result["members"][0]["record_profiles"][0]["fields"][0]["name"] == "diplotype"
        assert len(json.dumps(call.structured_content, separators=(",", ":")).encode()) < 100_000
    finally:
        repository.close()
        store.close()
