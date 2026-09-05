"""Atomic, deterministic, loss-preserving snapshot construction."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parents[1] / "fixtures" / "exports" / "sourced"
RELEASE_TAG = "data-clinpgx-core-0123456789abcdef"


def _archive(path: Path, members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 5, 8, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100444 << 16
            archive.writestr(info, members[name])
    raw = output.getvalue()
    path.write_bytes(raw)
    return raw


def _source(path: Path, *, published_at: str = "2026-09-05T00:37:36-07:00"):
    from clinpgx_link.data.catalog import SourceInput

    return SourceInput.from_path(
        dataset_id=f"data/{path.name}",
        path=path,
        source_url=f"https://api.clinpgx.org/v1/download/file/data/{path.name}",
        retrieved_at="2026-09-05T08:00:00Z",
        published_at=published_at,
        media_type="application/zip",
        license_id="operator-local-only",
        tier="approved_registry",
    )


def _fixture_sources(tmp_path: Path):
    tmp_path.mkdir(parents=True)
    genes = tmp_path / "genes.zip"
    _archive(
        genes,
        {
            "CREATED_2026-09-05.txt": b"Created on 09/05/2026 at 00:37:36 PDT.\n",
            "genes.tsv": (FIXTURES / "genes.tsv").read_bytes(),
        },
    )
    summaries = tmp_path / "summaryAnnotations.zip"
    _archive(
        summaries,
        {
            "summary_annotations.tsv": (FIXTURES / "summary_annotations.tsv").read_bytes(),
            "summary_ann_alleles.tsv": (FIXTURES / "summary_ann_alleles.tsv").read_bytes(),
            "summary_ann_evidence.tsv": (FIXTURES / "summary_ann_evidence.tsv").read_bytes(),
        },
    )
    guidelines = tmp_path / "guidelineAnnotations.json.zip"
    _archive(guidelines, {"PA166363221.json": (FIXTURES / "PA166363221.json").read_bytes()})
    pathways = tmp_path / "pathways.json.zip"
    _archive(pathways, {"pathways.json": (FIXTURES / "pathways.json").read_bytes()})
    relationships = tmp_path / "relationships.zip"
    _archive(relationships, {"relationships.tsv": (FIXTURES / "relationships.tsv").read_bytes()})
    return [
        _source(genes),
        _source(summaries, published_at="2026-08-05T01:12:00-07:00"),
        _source(guidelines, published_at="2026-08-05T01:11:00-07:00"),
        _source(pathways, published_at="2026-08-05T01:10:00-07:00"),
        _source(relationships, published_at="2026-08-05T01:09:00-07:00"),
    ]


def test_build_stores_exact_source_and_member_blobs_with_stable_row_identity(
    tmp_path: Path,
) -> None:
    """Catch hash-only retention, normalized-only rows, or record IDs lacking source ordinals."""
    from clinpgx_link.ingest.builder import build_snapshot

    sources = _fixture_sources(tmp_path / "inputs")
    built = build_snapshot(sources, tmp_path / "candidates", RELEASE_TAG)

    assert built.database == tmp_path / "candidates" / RELEASE_TAG / "clinpgx.sqlite"
    with sqlite3.connect(built.database) as connection:
        archive_row = connection.execute(
            "SELECT raw, sha256 FROM source_archive WHERE dataset_id = ?",
            ("data/genes.zip",),
        ).fetchone()
        member_row = connection.execute(
            "SELECT raw, sha256 FROM source_member WHERE dataset_id = ? AND path = ?",
            ("data/genes.zip", "genes.tsv"),
        ).fetchone()
        records = connection.execute(
            "SELECT record_id, ordinal, fields_json FROM record "
            "WHERE dataset_id = ? AND member = ? ORDER BY ordinal",
            ("data/genes.zip", "genes.tsv"),
        ).fetchall()

    archive_bytes = sources[0].read_verified()
    member_bytes = (FIXTURES / "genes.tsv").read_bytes()
    assert archive_row == (archive_bytes, hashlib.sha256(archive_bytes).hexdigest())
    assert member_row == (member_bytes, hashlib.sha256(member_bytes).hexdigest())
    assert [row[1] for row in records] == [1, 2]
    assert len({row[0] for row in records}) == 2
    assert json.loads(records[1][2])["Alternate Names"] == ""


def test_same_frozen_inputs_create_identical_database_and_snapshot_identity(tmp_path: Path) -> None:
    """Catch build paths or observation clocks leaking into immutable candidate identity."""
    from clinpgx_link.ingest.builder import build_snapshot

    sources = _fixture_sources(tmp_path / "inputs")
    first = build_snapshot(sources, tmp_path / "one", RELEASE_TAG)
    second = build_snapshot(sources, tmp_path / "two", RELEASE_TAG)

    assert first.snapshot_id == second.snapshot_id
    assert first.manifest == second.manifest
    assert first.database.read_bytes() == second.database.read_bytes()


def test_snapshot_identity_binds_ingest_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch different parser admission policies producing the same release identity."""
    from clinpgx_link.ingest import builder as builder_module

    sources = _fixture_sources(tmp_path / "inputs")
    first = builder_module.build_snapshot(sources, tmp_path / "one", RELEASE_TAG)
    monkeypatch.setattr(
        builder_module.settings,
        "max_ingest_rows",
        builder_module.settings.max_ingest_rows - 1,
    )
    second = builder_module.build_snapshot(sources, tmp_path / "two", RELEASE_TAG)

    assert first.snapshot_id != second.snapshot_id
    assert first.manifest["build_config"] != second.manifest["build_config"]


def test_builder_returns_frozen_per_source_manifest_without_a_global_date(tmp_path: Path) -> None:
    """Catch collapsing independently published archives into a fabricated global source date."""
    from clinpgx_link.ingest.builder import build_snapshot

    built = build_snapshot(_fixture_sources(tmp_path / "inputs"), tmp_path / "out", RELEASE_TAG)
    artifacts = built.manifest["artifacts"]

    assert built.manifest["snapshot_id"] == built.snapshot_id
    assert "published_at" not in built.manifest
    assert {item["dataset_id"]: item["published_at"] for item in artifacts}[
        "data/genes.zip"
    ] == "2026-09-05T00:37:36-07:00"
    assert {item["dataset_id"]: item["published_at"] for item in artifacts}[
        "data/summaryAnnotations.zip"
    ] == "2026-08-05T01:12:00-07:00"
    assert all(item["sha256"] and item["members"] for item in artifacts)


def test_json_profiles_retain_needed_children_without_pathway_row_explosion(tmp_path: Path) -> None:
    """Catch losing useful children or duplicating every nested pathway object as a row."""
    from clinpgx_link.ingest.builder import build_snapshot

    built = build_snapshot(_fixture_sources(tmp_path / "inputs"), tmp_path / "out", RELEASE_TAG)
    with sqlite3.connect(built.database) as connection:
        guideline = connection.execute(
            "SELECT json_pointer, parent_pointer, fields_json FROM record "
            "WHERE dataset_id = ? AND member = ? AND json_pointer = ''",
            ("data/guidelineAnnotations.json.zip", "PA166363221.json"),
        ).fetchone()
        guideline_child = connection.execute(
            "SELECT json_pointer, parent_pointer FROM record "
            "WHERE dataset_id = ? AND member = ? AND json_pointer = '/citations/0'",
            ("data/guidelineAnnotations.json.zip", "PA166363221.json"),
        ).fetchone()
        pathway_count = connection.execute(
            "SELECT count(*) FROM record WHERE dataset_id = ?",
            ("data/pathways.json.zip",),
        ).fetchone()[0]

    assert guideline[:2] == ("", None)
    assert json.loads(guideline[2])["guideline"]["id"] == "PA166363221"
    assert guideline_child == ("/citations/0", "")
    assert pathway_count == 2


def test_json_decimal_normalization_uses_an_explicit_lossless_representation(
    tmp_path: Path,
) -> None:
    """Catch exact source decimals becoming rounded or non-serializable during ingestion."""
    from clinpgx_link.ingest.builder import build_snapshot

    source_path = tmp_path / "precision.json.zip"
    _archive(
        source_path,
        {"precision.json": b'[{"frequency":0.12345678901234567890123456789}]'},
    )
    built = build_snapshot([_source(source_path)], tmp_path / "out", RELEASE_TAG)
    with sqlite3.connect(built.database) as connection:
        fields_json = connection.execute("SELECT fields_json FROM record").fetchone()[0]

    assert json.loads(fields_json) == {
        "frequency": {"$clinpgxJsonNumber": "0.12345678901234567890123456789"}
    }


def test_failed_candidate_build_preserves_previous_snapshot_and_never_activates(
    tmp_path: Path,
) -> None:
    """Catch a failed refresh overwriting current data or builder-owned activation."""
    from clinpgx_link.data.catalog import SourceInput
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.ingest.builder import build_snapshot

    current = tmp_path / "releases" / "current" / "clinpgx.sqlite"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"known-good-snapshot")
    before = current.read_bytes()
    malformed = tmp_path / "bad.zip"
    malformed.write_bytes(b"not a ZIP archive")
    source = SourceInput.from_path(
        dataset_id="data/bad.zip",
        path=malformed,
        source_url="https://api.clinpgx.org/v1/download/file/data/bad.zip",
        retrieved_at="2026-09-05T08:00:00Z",
        published_at=None,
        media_type="application/zip",
        license_id="operator-local-only",
        tier="approved_registry",
    )

    with pytest.raises(DataValidationError):
        build_snapshot([source], tmp_path / "releases", "data-clinpgx-core-fedcba9876543210")
    assert current.read_bytes() == before
    assert not (tmp_path / "releases" / "data-clinpgx-core-fedcba9876543210").exists()


@pytest.mark.parametrize(
    "release_tag",
    [
        "data-clinpgx-invalid tag",
        "data-clinpgx-core-0123456789abcde",
        "data-clinpgx-other-0123456789abcdef",
        "data-clinpgx-core-0123456789ABCDEf",
    ],
)
def test_builder_rejects_release_tags_runtime_cannot_activate(
    tmp_path: Path, release_tag: str
) -> None:
    """Catch creation of candidates that the production readiness contract rejects."""
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.ingest.builder import build_snapshot

    with pytest.raises(InvalidInputError, match="canonical"):
        build_snapshot(_fixture_sources(tmp_path / "inputs"), tmp_path / "out", release_tag)


def test_repeated_memberships_do_not_duplicate_external_record_ids_in_every_index(
    tmp_path: Path,
) -> None:
    """Catch the real-data 2.59 GB layout caused by repeating long IDs in membership indexes."""
    from clinpgx_link.ingest.builder import build_snapshot

    header = (FIXTURES / "relationships.tsv").read_text().splitlines()[0]
    row = (FIXTURES / "relationships.tsv").read_text().splitlines()[1]
    tabular = (header + "\n" + "\n".join(row for _ in range(5_000)) + "\n").encode()
    path = tmp_path / "relationships.zip"
    _archive(path, {"relationships.tsv": tabular})
    built = build_snapshot([_source(path)], tmp_path / "out", RELEASE_TAG)

    assert built.database.stat().st_size < len(tabular) * 16


def test_builder_retains_platform_metadata_without_treating_it_as_tabular_data(
    tmp_path: Path,
) -> None:
    """Catch AppleDouble metadata aborting published frequency-archive ingestion."""
    from clinpgx_link.ingest.builder import build_snapshot

    path = tmp_path / "frequencies.zip"
    apple_double = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        \x00\xa7"
    _archive(
        path,
        {
            "__MACOSX/frequencies/._data.tsv": apple_double,
            "frequencies/data.tsv": b"ID\tFrequency\nPA1\t0.25\n",
        },
    )

    built = build_snapshot([_source(path)], tmp_path / "out", RELEASE_TAG)
    with sqlite3.connect(built.database) as connection:
        rows = connection.execute(
            "SELECT path,raw,parser_status,limitation,record_count FROM source_member ORDER BY path"
        ).fetchall()

    assert rows[0] == (
        "__MACOSX/frequencies/._data.tsv",
        apple_double,
        "preserved",
        "Platform metadata retained without tabular parsing",
        0,
    )
    assert rows[1][2:] == ("indexed", None, 1)


def test_builder_quarantines_known_bad_readme_json_and_xlsx_with_exact_bytes(
    tmp_path: Path,
) -> None:
    """Catch known unusable members being skipped or represented as parsed data."""
    from clinpgx_link.ingest.builder import build_snapshot

    path = tmp_path / "haplotypes.zip"
    readme = b'{"status":"fail", invalid}'
    workbook = (FIXTURES.parents[0] / "adversarial" / "not_really_xlsx.xlsx").read_bytes()
    _archive(path, {"README.json": readme, "CYP2C19.xlsx": workbook})

    built = build_snapshot([_source(path)], tmp_path / "out", RELEASE_TAG)
    with sqlite3.connect(built.database) as connection:
        rows = connection.execute(
            "SELECT path,raw,parser_status,limitation,record_count FROM source_member ORDER BY path"
        ).fetchall()

    assert rows[0][0:3] == ("CYP2C19.xlsx", workbook, "quarantined")
    assert "OOXML" in rows[0][3]
    assert rows[0][4] == 0
    assert rows[1][0:3] == ("README.json", readme, "quarantined")
    assert "JSON" in rows[1][3]
    assert rows[1][4] == 0


def test_xlsx_row_limit_aborts_candidate_instead_of_quarantining_partial_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch the XLSX quarantine path swallowing the global normalized-row limit."""
    from openpyxl import Workbook

    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.ingest import builder as builder_module

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["ID"])
    sheet.append(["PA1"])
    sheet.append(["PA2"])
    workbook_bytes = io.BytesIO()
    workbook.save(workbook_bytes)
    path = tmp_path / "haplotypes.zip"
    _archive(path, {"CYP2C19.xlsx": workbook_bytes.getvalue()})
    monkeypatch.setattr(builder_module.settings, "max_ingest_rows", 1)

    with pytest.raises(DataValidationError, match="row limit"):
        builder_module.build_snapshot([_source(path)], tmp_path / "out", RELEASE_TAG)

    assert not (tmp_path / "out" / RELEASE_TAG).exists()


def test_quarantined_member_rolls_back_rows_inserted_before_late_parse_failure(
    tmp_path: Path,
) -> None:
    """Catch quarantined members retaining partial normalized rows outside their counts."""
    from clinpgx_link.ingest.builder import build_snapshot

    nested: dict[str, object] = {"id": "PA1"}
    for _ in range(129):
        nested = {"children": [nested]}
    path = tmp_path / "haplotypes.zip"
    _archive(path, {"README.json": json.dumps(nested).encode()})

    built = build_snapshot([_source(path)], tmp_path / "out", RELEASE_TAG)
    with sqlite3.connect(built.database) as connection:
        normalized = connection.execute("SELECT count(*) FROM record").fetchone()[0]
        member = connection.execute(
            "SELECT parser_status,record_count FROM source_member WHERE path='README.json'"
        ).fetchone()
        dataset = connection.execute("SELECT record_count FROM dataset").fetchone()[0]

    assert normalized == 0
    assert member == ("quarantined", 0)
    assert dataset == 0


@pytest.mark.parametrize(
    ("member", "body"),
    [
        ("summary_annotations.tsv", b"Summary Annotation ID\tGene\n\tCYP2C19\n"),
        ("summary_ann_evidence.tsv", b"Summary Annotation ID\tEvidence ID\n\tE1\n"),
        ("summary_ann_alleles.tsv", b"Summary Annotation ID\tGenotype/Allele\n\t*1\n"),
    ],
)
def test_summary_join_members_require_nonempty_annotation_identity(
    tmp_path: Path, member: str, body: bytes
) -> None:
    """Catch source rows that cannot participate in the declared identity join."""
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.ingest.builder import build_snapshot

    path = tmp_path / "summaryAnnotations.zip"
    _archive(path, {member: body})

    with pytest.raises(DataValidationError, match="annotation identity"):
        build_snapshot([_source(path)], tmp_path / "out", RELEASE_TAG)


def test_auxiliary_rows_gain_only_profiled_gene_allele_and_drug_memberships(
    tmp_path: Path,
) -> None:
    """Catch parsed auxiliary tables remaining unqueryable or gaining guessed semantics."""
    from openpyxl import Workbook

    from clinpgx_link.data.repository import DatasetRepository
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.ingest.builder import build_snapshot

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    definitions: dict[str, dict[str, bytes]] = {
        "clinpgxHaplotypes.zip": {
            "clinpgxHaplotypes_star_alleles.tsv": (
                b"Accession ID\tGene\tAllele Name\tHGVS\tStructural Variation\tAMP Level\n"
                b"PA165980634\tCYP2C19\t*1\tNC_000010.11:g.=\t\t\n"
            )
        },
        "pharmgkb_haplotype_frequencies_AllOfUs.zip": {
            "AllOfUs_Frequencies_v7/allele/CYP2C19_allele.tsv": (
                b"biogeographic_group\tallele\tn_haplotype\tfrequencies\tin_cpic\tnotes\t"
                b"n_subjects_genotyped\n afr\t*1\t10\t0.5\t\t\t10.0\n"
            )
        },
        "pharmgkb_haplotype_frequencies_UKBB.zip": {
            "CYP2C19_UKBB_frequencies.tsv": (
                b"Source\tPopulation\tAllele\tAlleles Observed\tAlleles Total\tFrequency\n"
                b"UKBB\tAll populations\t*1\t10\t20\t0.5\n"
            )
        },
        "pharmcat.zip": {
            "phenotypes.json": (
                b'[{"gene":"TPMT","namedAlleles":['
                b'{"name":"*1","functionValue":"Normal function"}]}]'
            )
        },
    }
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Drug or Ingredient", "Source", "Code Type", "Code"])
    sheet.append(["clopidogrel", "RxNorm", "RxCUI", "32968"])
    sheet.append(["clopidogrel", "DrugBank", "Accession Number", "DB00758"])
    sheet.append(["clopidogrel", "ATC", "ATC Code", "B01AC04"])
    sheet.append(["clopidogrel", "PharmGKB", "PharmGKB Accession ID", "PA449053"])
    workbook_bytes = io.BytesIO()
    workbook.save(workbook_bytes)
    definitions["cpic.drug.mapping.zip"] = {
        "drug.mapping.clopidogrel.xlsx": workbook_bytes.getvalue()
    }
    sources = []
    for name, members in definitions.items():
        path = inputs / name
        _archive(path, members)
        sources.append(_source(path))

    built = build_snapshot(sources, tmp_path / "out", RELEASE_TAG)
    with sqlite3.connect(built.database) as connection:
        memberships = set(
            connection.execute(
                "SELECT r.dataset_id,r.member,r.json_pointer,m.kind,m.value,m.source_field "
                "FROM membership m JOIN record r ON r.record_pk=m.record_pk"
            ).fetchall()
        )

    assert (
        "data/clinpgxHaplotypes.zip",
        "clinpgxHaplotypes_star_alleles.tsv",
        None,
        "allele",
        "*1",
        "Allele Name",
    ) in memberships
    assert any(
        row[0] == "data/pharmgkb_haplotype_frequencies_AllOfUs.zip"
        and row[3:5] == ("gene", "CYP2C19")
        for row in memberships
    )
    assert any(
        row[0] == "data/pharmgkb_haplotype_frequencies_UKBB.zip" and row[3:5] == ("allele", "*1")
        for row in memberships
    )
    assert any(
        row[0] == "data/pharmcat.zip"
        and "/namedAlleles/" in (row[2] or "")
        and row[3:5] == ("gene", "TPMT")
        for row in memberships
    )
    assert any(
        row[0] == "data/cpic.drug.mapping.zip" and row[3:5] == ("chemical", "clopidogrel")
        for row in memberships
    )
    assert any(
        row[0] == "data/cpic.drug.mapping.zip" and row[3:5] == ("id", "PA449053")
        for row in memberships
    )

    repository = DatasetRepository(built.database)
    assert (
        repository.search(
            "data/clinpgxHaplotypes.zip",
            filters={"gene": "CYP2C19", "name": "*1"},
        ).details["total_count"]
        == 1
    )
    assert (
        repository.search(
            "data/cpic.drug.mapping.zip",
            filters={"chemical": "clopidogrel", "id": "PA449053"},
            match="member",
        ).details["total_count"]
        == 1
    )
    assert (
        repository.search_entities(
            "allele",
            filters={"gene": "TPMT", "name": "*1"},
        ).details["total_count"]
        == 1
    )
    with pytest.raises(InvalidInputError):
        repository.search_entities("allele", filters={"chemical": "clopidogrel"})
    assert (
        repository.search(
            "data/pharmgkb_haplotype_frequencies_UKBB.zip",
            filters={"gene": "CYP2C19", "name": "*1"},
        ).details["total_count"]
        == 1
    )
    repository.close()
