"""Source-defined gene alias membership and candidate compatibility behavior."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastmcp import Client

from clinpgx_link.content.store import ContentStore
from clinpgx_link.data.catalog import SourceInput
from clinpgx_link.data.repository import DatasetRepository
from clinpgx_link.exceptions import UpstreamUnavailableError
from clinpgx_link.ingest.builder import BuiltSnapshot, build_snapshot
from clinpgx_link.mcp.facade import create_mcp

RELEASE_TAG = "data-clinpgx-core-0123456789abcdef"
PROFILE_DIGEST = "sha256:7c41497dd406e371f9964beffbd30bfbd1be29697855d6993932354f802c7895"
TOKENIZATION_CONTRACT = "literal-comma-split-strip-nonempty-v1"
DEGRADED_GUIDANCE = (
    "Gene alias member search is unavailable for this installed snapshot. Rebuild and install "
    "a compatible immutable snapshot; raw records and exact source-field search remain available."
)

GENES_TSV = (
    b"PharmGKB Accession Id\tNCBI Gene ID\tHGNC ID\tEnsembl Id\tName\tSymbol\t"
    b"Alternate Names\tAlternate Symbols\tIs VIP\tHas Variant Annotation\n"
    b"PA145\t1806\t3012\tENSG00000188641\tdihydropyrimidine dehydrogenase\tDPYD\t"
    b"Dihydrothymine dehydrogenase, Dihydrouracil dehydrogenase\tDHPDHase, DPD\tNo\tYes\n"
    b"PA999\t83483\t13635\tENSG00000130300\tplasmalemma vesicle associated protein\tPLVAP\t"
    b"fenestrated-endothelial linked structure protein; PV-1 protein\tPV1\tNo\tNo\n"
)
SUMMARY_TSV = b"Summary Annotation ID\tGene\n42\tCYP2C19;CYP2D6\n"


def _archive(path: Path, members: dict[str, bytes]) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 5, 8, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100444 << 16
            archive.writestr(info, members[name])
    path.write_bytes(output.getvalue())


def _source(path: Path) -> SourceInput:
    return SourceInput.from_path(
        dataset_id=f"data/{path.name}",
        path=path,
        source_url=f"https://api.clinpgx.org/v1/download/file/data/{path.name}",
        retrieved_at="2026-09-05T08:00:00Z",
        published_at=None,
        media_type="application/zip",
        license_id="operator-local-only",
        tier="approved_registry",
    )


def _build(tmp_path: Path, *, include_summary: bool = False) -> BuiltSnapshot:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    genes = inputs / "genes.zip"
    _archive(genes, {"genes.tsv": GENES_TSV})
    sources = [_source(genes)]
    if include_summary:
        summary = inputs / "summaryAnnotations.zip"
        _archive(summary, {"summary_annotations.tsv": SUMMARY_TSV})
        sources.append(_source(summary))
    return build_snapshot(sources, tmp_path / "candidates", RELEASE_TAG)


def _set_receipt(database: Path, raw: str | None) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM metadata WHERE key='gene_alias_membership_json'")
        if raw is not None:
            connection.execute(
                "INSERT INTO metadata(key,value) VALUES ('gene_alias_membership_json',?)", (raw,)
            )


def test_builder_materializes_source_defined_gene_aliases_and_binds_receipt(
    tmp_path: Path,
) -> None:
    """Catch semicolon splitting, whole-cell member rows, or unbound build semantics."""
    built = _build(tmp_path)
    repository = DatasetRepository(built.database)
    try:
        for field, aliases in {
            "Alternate Symbols": ("DHPDHase", "DPD"),
            "Alternate Names": (
                "Dihydrothymine dehydrogenase",
                "Dihydrouracil dehydrogenase",
            ),
        }.items():
            for alias in aliases:
                source_match = repository.search(
                    "data/genes.zip",
                    member="genes.tsv",
                    filters={field: alias},
                    match="member",
                )
                canonical_kind = "gene" if field == "Alternate Symbols" else "name"
                canonical_match = repository.search(
                    "data/genes.zip", filters={canonical_kind: alias}, match="member"
                )
                assert [row["id"] for row in source_match.value] == ["PA145"]
                assert [row["id"] for row in canonical_match.value] == ["PA145"]

        complete = "DHPDHase, DPD"
        assert (
            repository.search(
                "data/genes.zip",
                member="genes.tsv",
                filters={"Alternate Symbols": complete},
                match="member",
            ).value
            == []
        )
        exact = repository.search(
            "data/genes.zip",
            member="genes.tsv",
            filters={"Alternate Symbols": complete},
            match="exact",
        )
        assert [row["id"] for row in exact.value] == ["PA145"]

        semicolon_name = "fenestrated-endothelial linked structure protein; PV-1 protein"
        whole = repository.search(
            "data/genes.zip",
            member="genes.tsv",
            filters={"Alternate Names": semicolon_name},
            match="member",
        )
        fragment = repository.search(
            "data/genes.zip",
            member="genes.tsv",
            filters={"Alternate Names": "PV-1 protein"},
            match="member",
        )
        assert [row["id"] for row in whole.value] == ["PA999"]
        assert fragment.value == []
        assert repository.asset_content("data/genes.zip", member="genes.tsv").value == GENES_TSV

        receipt = built.manifest["gene_alias_membership"]
        assert receipt == {
            "schema_version": 1,
            "snapshot_id": built.snapshot_id,
            "profile_definition_digest": PROFILE_DIGEST,
            "tokenization_contract_version": TOKENIZATION_CONTRACT,
        }
        with sqlite3.connect(built.database) as connection:
            stored = connection.execute(
                "SELECT value FROM metadata WHERE key='gene_alias_membership_json'"
            ).fetchone()[0]
        assert json.loads(stored) == receipt

        described = repository.describe("data/genes.zip").value
        member = described["members"][0]
        assert described["gene_alias_membership_status"] == "compatible"
        assert member["gene_alias_membership_status"] == "compatible"
        fields = {item["name"]: item for item in member["fields"]}
        for field in ("Alternate Names", "Alternate Symbols"):
            assert fields[field]["match_modes"] == ["exact", "member"]
            assert fields[field]["tokenizer"] == "csv"
    finally:
        repository.close()


def test_missing_receipt_fails_closed_only_for_affected_membership_paths(tmp_path: Path) -> None:
    """Catch old alias indexes returning incomplete results or disabling unaffected access."""
    built = _build(tmp_path, include_summary=True)
    _set_receipt(built.database, None)
    repository = DatasetRepository(built.database)
    try:
        affected = (
            lambda: repository.search(
                "data/genes.zip",
                member="genes.tsv",
                filters={"Alternate Symbols": "DPD"},
                match="member",
            ),
            lambda: repository.search(
                "data/genes.zip",
                member="genes.tsv",
                filters={"Alternate Names": "Dihydrouracil dehydrogenase"},
                match="member",
            ),
            lambda: repository.search("data/genes.zip", filters={"gene": "DPD"}, match="member"),
            lambda: repository.search(
                "data/genes.zip",
                filters={"name": "Dihydrouracil dehydrogenase"},
                match="member",
            ),
            lambda: repository.search_entities("gene", filters={"gene": "DPD"}),
        )
        for operation in affected:
            with pytest.raises(UpstreamUnavailableError) as failure:
                operation()
            assert failure.value.subtype == "membership_profile_mismatch"
            assert "DPD" not in str(failure.value)

        assert (
            repository.search(
                "data/genes.zip",
                member="genes.tsv",
                filters={"Alternate Symbols": "DHPDHase, DPD"},
                match="exact",
            ).details["total_count"]
            == 1
        )
        assert repository.search("data/genes.zip", query="DHPDHase").details["total_count"] == 1
        assert (
            repository.search("data/genes.zip", filters={"id": "PA145"}).details["total_count"] == 1
        )
        assert (
            repository.search_entities("gene", filters={"id": "PA145"}).details["total_count"] == 1
        )
        assert repository.search_entities("gene", filters={"id": "DPD"}).value == []
        assert (
            repository.search(
                "data/summaryAnnotations.zip",
                member="summary_annotations.tsv",
                filters={"gene": "CYP2D6"},
                match="member",
            ).details["total_count"]
            == 1
        )
        assert (
            repository.search_entities("annotation_id", filters={"gene": "CYP2D6"}).details[
                "total_count"
            ]
            == 1
        )
        assert repository.asset_content("data/genes.zip", member="genes.tsv").value == GENES_TSV

        described = repository.describe("data/genes.zip").value
        member = described["members"][0]
        assert described["gene_alias_membership_status"] == "unknown"
        assert described["gene_alias_membership_limitation"] == DEGRADED_GUIDANCE
        assert member["gene_alias_membership_status"] == "unknown"
        fields = {item["name"]: item for item in member["fields"]}
        assert fields["Alternate Symbols"]["match_modes"] == ["exact"]
        assert fields["Alternate Symbols"]["tokenizer"] is None
        assert fields["Symbol"]["match_modes"] == ["exact"]
    finally:
        repository.close()


@pytest.mark.parametrize(
    "raw",
    [
        '{"profile_definition_digest":"' + PROFILE_DIGEST + '",'
        '"schema_version":1,"schema_version":1,"snapshot_id":"REPLACE",'
        '"tokenization_contract_version":"' + TOKENIZATION_CONTRACT + '"}',
        '{"schema_version":2,"snapshot_id":"REPLACE",'
        f'"profile_definition_digest":"{PROFILE_DIGEST}",'
        f'"tokenization_contract_version":"{TOKENIZATION_CONTRACT}"}}',
        '{"schema_version":true,"snapshot_id":"hostile",'
        f'"profile_definition_digest":"{PROFILE_DIGEST}",'
        f'"tokenization_contract_version":"{TOKENIZATION_CONTRACT}"}}',
        '{"schema_version":1,"snapshot_id":"REPLACE",'
        '"profile_definition_digest":true,'
        f'"tokenization_contract_version":"{TOKENIZATION_CONTRACT}"}}',
        '{"schema_version":1,"snapshot_id":"hostile",'
        f'"profile_definition_digest":"{PROFILE_DIGEST}",'
        f'"tokenization_contract_version":"{TOKENIZATION_CONTRACT}"}}',
        '{"schema_version":1,"snapshot_id":"REPLACE",'
        '"profile_definition_digest":"sha256:' + "0" * 64 + '",'
        f'"tokenization_contract_version":"{TOKENIZATION_CONTRACT}"}}',
        '{"schema_version":1,"snapshot_id":"REPLACE",'
        f'"profile_definition_digest":"{PROFILE_DIGEST}",'
        '"tokenization_contract_version":"wrong"}',
        '{"extra":0,"schema_version":1,"snapshot_id":"REPLACE",'
        f'"profile_definition_digest":"{PROFILE_DIGEST}",'
        f'"tokenization_contract_version":"{TOKENIZATION_CONTRACT}"}}',
        "[1,2,3]",
    ],
)
def test_malformed_or_stale_receipt_is_mismatched_without_input_reflection(
    tmp_path: Path, raw: str
) -> None:
    """Catch permissive receipt parsing, bool-as-int, duplicate keys, or reflected input."""
    built = _build(tmp_path)
    supplied = raw.replace("REPLACE", built.snapshot_id)
    _set_receipt(built.database, supplied)
    repository = DatasetRepository(built.database)
    try:
        described = repository.describe("data/genes.zip").value
        assert described["gene_alias_membership_status"] == "mismatched"
        with pytest.raises(UpstreamUnavailableError) as failure:
            repository.search("data/genes.zip", filters={"gene": "DHPDHase"}, match="member")
        assert failure.value.subtype == "membership_profile_mismatch"
        assert "hostile" not in str(failure.value)
        assert "wrong" not in str(failure.value)
    finally:
        repository.close()


def test_receipt_parser_is_bounded(tmp_path: Path) -> None:
    """Catch loading an unbounded compatibility receipt during repository initialization."""
    built = _build(tmp_path)
    _set_receipt(built.database, "x" * 16_385)
    repository = DatasetRepository(built.database)
    try:
        assert (
            repository.describe("data/genes.zip").value["gene_alias_membership_status"]
            == "mismatched"
        )
    finally:
        repository.close()


def test_repository_loads_gene_alias_receipt_once_at_initialization(tmp_path: Path) -> None:
    """Catch per-request metadata reloads that could mix compatibility within one handle."""
    built = _build(tmp_path)
    repository = DatasetRepository(built.database)
    try:
        _set_receipt(built.database, None)
        assert (
            repository.describe("data/genes.zip").value["gene_alias_membership_status"]
            == "compatible"
        )
        assert (
            repository.search("data/genes.zip", filters={"gene": "DPD"}, match="member").details[
                "total_count"
            ]
            == 1
        )
    finally:
        repository.close()

    reopened = DatasetRepository(built.database)
    try:
        assert (
            reopened.describe("data/genes.zip").value["gene_alias_membership_status"] == "unknown"
        )
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_mcp_exposes_alias_degradation_and_fixed_rebuild_guidance(tmp_path: Path) -> None:
    """Catch the MCP boundary hiding compatibility status or returning an unsafe empty result."""
    built = _build(tmp_path)
    _set_receipt(built.database, None)
    repository = DatasetRepository(built.database)
    store = ContentStore(tmp_path / "content.sqlite")
    server = create_mcp(content_store=store, repository=repository)
    try:
        async with Client(server) as client:
            described = await client.call_tool(
                "get_dataset", {"dataset_id": "data/genes.zip", "response_mode": "full"}
            )
            failed = await client.call_tool(
                "search_dataset",
                {
                    "dataset_id": "data/genes.zip",
                    "member": "genes.tsv",
                    "filters": {"Alternate Symbols": "DPD"},
                    "match": "member",
                },
                raise_on_error=False,
            )
        assert described.structured_content["result"]["gene_alias_membership_status"] == "unknown"
        assert (
            described.structured_content["result"]["gene_alias_membership_limitation"]
            == DEGRADED_GUIDANCE
        )
        assert failed.structured_content["error_code"] == "upstream_unavailable"
        assert failed.structured_content["subtype"] == "membership_profile_mismatch"
        assert failed.structured_content["message"] == DEGRADED_GUIDANCE
    finally:
        repository.close()
        store.close()
