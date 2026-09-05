"""Pinned-snapshot search, provenance, and validated join behavior."""

from __future__ import annotations

import base64
import hashlib
import io
import sqlite3
import zipfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parents[1] / "fixtures" / "exports" / "sourced"
RELEASE_TAG = "data-clinpgx-core-0123456789abcdef"


def _archive(path: Path, members: dict[str, bytes]) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 5, 8, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100444 << 16
            archive.writestr(info, members[name])
    path.write_bytes(output.getvalue())


def _repository(tmp_path: Path):
    from clinpgx_link.data.catalog import SourceInput
    from clinpgx_link.data.repository import DatasetRepository
    from clinpgx_link.ingest.builder import build_snapshot

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    definitions = {
        "genes.zip": {"genes.tsv": (FIXTURES / "genes.tsv").read_bytes()},
        "summaryAnnotations.zip": {
            "summary_annotations.tsv": (FIXTURES / "summary_annotations.tsv").read_bytes(),
            "summary_ann_alleles.tsv": (FIXTURES / "summary_ann_alleles.tsv").read_bytes(),
            "summary_ann_evidence.tsv": (FIXTURES / "summary_ann_evidence.tsv").read_bytes(),
        },
        "guidelineAnnotations.json.zip": {
            "PA166363221.json": (FIXTURES / "PA166363221.json").read_bytes()
        },
        "relationships.zip": {"relationships.tsv": (FIXTURES / "relationships.tsv").read_bytes()},
    }
    sources = []
    for name, members in definitions.items():
        path = inputs / name
        _archive(path, members)
        sources.append(
            SourceInput.from_path(
                dataset_id=f"data/{name}",
                path=path,
                source_url=f"https://api.clinpgx.org/v1/download/file/data/{name}",
                retrieved_at="2026-09-05T08:00:00Z",
                published_at=(
                    "2026-09-05T00:37:36-07:00"
                    if name == "genes.zip"
                    else "2026-08-05T01:12:00-07:00"
                ),
                media_type="application/zip",
                license_id="operator-local-only",
                tier="approved_registry",
            )
        )
    built = build_snapshot(sources, tmp_path / "candidates", RELEASE_TAG)
    return DatasetRepository(built.database), built


def test_repository_lists_and_describes_only_the_pinned_snapshot(tmp_path: Path) -> None:
    """Catch mutable-current reads or catalog metadata presented as imported coverage."""
    repository, built = _repository(tmp_path)

    status = repository.status()
    listed = repository.list_datasets()
    described = repository.describe("data/genes.zip")

    assert status["snapshot_id"] == built.snapshot_id
    assert status["release_tag"] == RELEASE_TAG
    assert len(listed.value) == 4
    assert listed.source.release_tag == RELEASE_TAG
    assert listed.source.sha256 == built.snapshot_id.removeprefix("sha256:")
    assert described.value["dataset_id"] == "data/genes.zip"
    assert described.value["source_date"] == "2026-09-05T00:37:36-07:00"
    genes = described.value["members"][0]
    assert genes["path"] == "genes.tsv"
    assert genes["record_count"] == 2
    assert genes["fields"][5] == {
        "name": "Symbol",
        "match_modes": ["exact"],
        "tokenizer": None,
        "semantic_target": "gene",
    }


def test_repository_search_returns_standard_rows_counts_and_stable_pages(tmp_path: Path) -> None:
    """Catch double pagination, unstable ordering, or normalized rows without original fields."""
    repository, built = _repository(tmp_path)

    first = repository.search("data/genes.zip", member="genes.tsv", limit=1, offset=0)
    second = repository.search("data/genes.zip", member="genes.tsv", limit=1, offset=1)

    assert first.details == {
        "total_count": 2,
        "offset": 0,
        "returned": 1,
        "has_more": True,
        "snapshot_id": built.snapshot_id,
    }
    assert second.details["total_count"] == 2
    assert second.details["has_more"] is False
    assert first.value[0]["record_id"] != second.value[0]["record_id"]
    assert first.value[0] == {
        "record_id": first.value[0]["record_id"],
        "dataset_id": "data/genes.zip",
        "member": "genes.tsv",
        "ordinal": 1,
        "fields": first.value[0]["fields"],
        "id": "PA124",
    }
    assert first.value[0]["fields"]["Alternate Names"] == "CPCJ;CYPIIC17;P450C2C"


def test_unknown_filter_is_not_successful_empty(tmp_path: Path) -> None:
    """Catch misspelled canonical filters being reported as a valid empty result."""
    from clinpgx_link.exceptions import InvalidInputError

    repository, _ = _repository(tmp_path)
    with pytest.raises(InvalidInputError):
        repository.search("data/genes.zip", member="genes.tsv", filters={"Symobl": "CYP2C19"})


def test_unknown_json_shape_cannot_create_or_advertise_guessed_entities(tmp_path: Path) -> None:
    """Catch generic id/name keys and path substrings becoming undeclared search semantics."""
    from clinpgx_link.data.catalog import SourceInput
    from clinpgx_link.data.repository import DatasetRepository
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.ingest.builder import build_snapshot

    path = tmp_path / "unknown.json.zip"
    _archive(
        path,
        {"unknown.json": (b'{"id":"PA1","name":"not an entity","relatedGenes":[{"id":"PA2"}]}')},
    )
    source = SourceInput.from_path(
        dataset_id="data/unknown.json.zip",
        path=path,
        source_url="https://api.clinpgx.org/v1/download/file/data/unknown.json.zip",
        retrieved_at="2026-09-05T08:00:00Z",
        published_at=None,
        media_type="application/zip",
        license_id="operator-local-only",
        tier="approved_registry",
    )
    built = build_snapshot([source], tmp_path / "out", RELEASE_TAG)
    repository = DatasetRepository(built.database)

    with pytest.raises(InvalidInputError):
        repository.search("data/unknown.json.zip", filters={"gene": "PA2"})
    assert repository.search_entities("gene").details["total_count"] == 0
    repository.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"match": "substring"},
        {"filters": {"gene": ""}},
    ],
)
def test_search_rejects_invalid_bounds_and_match_semantics(tmp_path: Path, kwargs: dict) -> None:
    """Catch unbounded pages or ambiguous matching modes reaching SQLite."""
    from clinpgx_link.exceptions import InvalidInputError

    repository, _ = _repository(tmp_path)
    with pytest.raises(InvalidInputError):
        repository.search("data/genes.zip", **kwargs)


def test_declared_member_match_finds_multivalue_gene_drug_row(tmp_path: Path) -> None:
    """Catch whole-cell comparison or generic substring matching of semicolon memberships."""
    repository, _ = _repository(tmp_path)

    exact = repository.search(
        "data/summaryAnnotations.zip",
        member="summary_annotations.tsv",
        filters={"chemical": "olanzapine"},
        match="exact",
    )
    member = repository.search(
        "data/summaryAnnotations.zip",
        member="summary_annotations.tsv",
        filters={"gene": "RGS4", "chemical": "olanzapine"},
        match="member",
    )

    assert exact.details["total_count"] == 0
    assert member.details["total_count"] == 1
    assert member.value[0]["id"] == "655384607"
    assert member.value[0]["fields"]["Drug(s)"].startswith("antipsychotics;")


def test_literal_fts_uses_and_tokens_without_accepting_fts_syntax(tmp_path: Path) -> None:
    """Catch OR broadening or caller-controlled FTS operators entering the query grammar."""
    repository, _ = _repository(tmp_path)

    found = repository.search(
        "data/summaryAnnotations.zip",
        member="summary_ann_evidence.tsv",
        query="response risperidone",
    )
    hostile = repository.search(
        "data/summaryAnnotations.zip",
        member="summary_ann_evidence.tsv",
        query='response" OR *',
    )

    assert found.details["total_count"] == 1
    assert found.value[0]["fields"]["Evidence ID"] == "655387128"
    assert hostile.details["total_count"] == 0


def test_expected_snapshot_mismatch_fails_before_dataset_lookup_or_rows(tmp_path: Path) -> None:
    """Catch count/select against a new snapshot after a cursor was decoded for an old one."""
    from clinpgx_link.exceptions import UpstreamUnavailableError

    repository, _ = _repository(tmp_path)
    with pytest.raises(UpstreamUnavailableError) as failure:
        repository.search(
            "data/not-present.zip",
            expected_snapshot="sha256:" + "0" * 64,
        )
    assert failure.value.subtype == "snapshot_mismatch"


def test_get_record_round_trips_record_identity_and_snapshot_provenance(tmp_path: Path) -> None:
    """Catch record lookup detached from the immutable source row or snapshot identity."""
    repository, built = _repository(tmp_path)
    row = repository.search("data/genes.zip", member="genes.tsv", filters={"id": "PA124"}).value[0]
    fetched = repository.get_record(row["record_id"], expected_snapshot=built.snapshot_id)

    assert fetched.value == row
    assert fetched.source.release_tag == RELEASE_TAG
    assert fetched.details["snapshot_id"] == built.snapshot_id


@pytest.mark.parametrize(("result_type", "expected"), [("evidence", 1), ("allele", 3)])
def test_summary_annotation_joins_return_each_original_child_once_with_provenance(
    tmp_path: Path, result_type: str, expected: int
) -> None:
    """Catch text-similarity joins, duplicate child rows, or flattened joined prose."""
    repository, built = _repository(tmp_path)
    parent = repository.search(
        "data/summaryAnnotations.zip",
        member="summary_annotations.tsv",
        filters={"annotation_id": "655384602"},
    ).value[0]

    joined = repository.related(
        parent["record_id"],
        result_type=result_type,
        expected_snapshot=built.snapshot_id,
    )

    assert joined.details["total_count"] == expected
    assert len({row["record_id"] for row in joined.value}) == expected
    assert all(row["join"]["parent_record_id"] == parent["record_id"] for row in joined.value)
    assert all(row["join"]["relation_kind"] == result_type for row in joined.value)
    assert all(row["join"]["source_row_ids"] == [row["record_id"]] for row in joined.value)
    assert all(row["fields"]["Summary Annotation ID"] == "655384602" for row in joined.value)


def test_summary_join_without_parent_annotation_identity_fails_closed(tmp_path: Path) -> None:
    """Catch an incomplete installed join key broadening into every evidence row."""
    from clinpgx_link.data.catalog import SourceInput
    from clinpgx_link.data.repository import DatasetRepository
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.ingest.builder import build_snapshot

    archive = tmp_path / "summaryAnnotations.zip"
    _archive(
        archive,
        {
            "summary_annotations.tsv": b"Summary Annotation ID\tGene\nparent\tCYP2C19\n",
            "summary_ann_evidence.tsv": (
                b"Summary Annotation ID\tEvidence ID\tPMID\none\tE1\t1\ntwo\tE2\t2\n"
            ),
        },
    )
    source = SourceInput.from_path(
        dataset_id="data/summaryAnnotations.zip",
        path=archive,
        source_url="https://api.clinpgx.org/v1/download/file/data/summaryAnnotations.zip",
        retrieved_at="2026-09-05T08:00:00Z",
        published_at=None,
        media_type="application/zip",
        license_id="operator-local-only",
        tier="approved_registry",
    )
    built = build_snapshot([source], tmp_path / "out", RELEASE_TAG)
    with sqlite3.connect(built.database) as connection:
        connection.execute("DELETE FROM membership WHERE kind='annotation_id' AND value='parent'")
    repository = DatasetRepository(built.database)
    parent = repository.search(
        "data/summaryAnnotations.zip", member="summary_annotations.tsv"
    ).value[0]

    with pytest.raises(DataValidationError, match="identity"):
        repository.related(parent["record_id"], result_type="evidence")
    repository.close()


def test_reverse_relationship_result_preserves_published_endpoint_direction(tmp_path: Path) -> None:
    """Catch swapping a matching endpoint and implying causal or biological direction."""
    repository, _ = _repository(tmp_path)
    gene = repository.search_entities("gene", filters={"id": "PA142672624"}).value[0]
    relationships = repository.related(gene["record_id"], result_type="relationship")

    assert relationships.details["total_count"] == 1
    fields = relationships.value[0]["fields"]
    assert fields["Entity1_id"] == "PA142672624"
    assert fields["Entity1_type"] == "Gene"
    assert fields["Entity2_id"] == "PA449899"
    assert fields["Entity2_type"] == "Chemical"


def test_all_yes_vip_export_is_preserved_as_anomaly_not_normalized_truth(tmp_path: Path) -> None:
    """Catch interpreting the observed defective all-Yes column as verified VIP status."""
    repository, _ = _repository(tmp_path)
    response = repository.search("data/genes.zip", member="genes.tsv")

    assert {row["fields"]["Is VIP"] for row in response.value} == {"Yes"}
    assert "upstream_anomaly:genes_is_vip_all_yes" in response.source.warnings


def test_asset_content_survives_build_cache_deletion_and_upstream_replacement(
    tmp_path: Path,
) -> None:
    """Catch historical retrieval that depends on mutable local paths or upstream URLs."""
    repository, built = _repository(tmp_path)
    original_archive = (tmp_path / "inputs" / "genes.zip").read_bytes()
    deleted_archive = (tmp_path / "inputs" / "summaryAnnotations.zip").read_bytes()
    (tmp_path / "inputs" / "genes.zip").write_bytes(b"upstream replacement")
    (tmp_path / "inputs" / "summaryAnnotations.zip").unlink()

    archive = repository.asset_content("data/genes.zip", expected_snapshot=built.snapshot_id)
    member = repository.asset_content(
        "data/genes.zip", member="genes.tsv", expected_snapshot=built.snapshot_id
    )
    deleted = repository.asset_content(
        "data/summaryAnnotations.zip", expected_snapshot=built.snapshot_id
    )

    assert archive.value == original_archive
    assert archive.source.sha256 == hashlib.sha256(original_archive).hexdigest()
    assert archive.details["media_type"] == "application/zip"
    assert member.value == (FIXTURES / "genes.tsv").read_bytes()
    assert member.source.sha256 == hashlib.sha256(member.value).hexdigest()
    assert member.details["snapshot_id"] == built.snapshot_id
    assert deleted.value == deleted_archive
    assert deleted.source.sha256 == hashlib.sha256(deleted_archive).hexdigest()


def test_read_asset_chunks_exact_retained_bytes_with_progress(tmp_path: Path) -> None:
    """Catch derived bytes, skipped offsets, or non-progressing asset continuation."""
    repository, built = _repository(tmp_path)
    expected = (FIXTURES / "genes.tsv").read_bytes()
    offset = 0
    chunks: list[bytes] = []
    while True:
        response = repository.read_asset(
            "data/genes.zip",
            member="genes.tsv",
            start=offset,
            length=17,
            expected_snapshot=built.snapshot_id,
        )
        value = response.value
        chunk = base64.b64decode(value["data"], validate=True)
        assert value["offset"] == offset
        assert value["returned"] == len(chunk)
        chunks.append(chunk)
        if value["next_offset"] is None:
            break
        assert value["next_offset"] > offset
        offset = value["next_offset"]
    assert b"".join(chunks) == expected
    assert value["sha256"] == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize("kwargs", [{"start": -1}, {"length": 0}, {"length": 8193}])
def test_read_asset_rejects_invalid_chunk_bounds(tmp_path: Path, kwargs: dict) -> None:
    """Catch negative, empty, or oversized source-asset responses."""
    from clinpgx_link.exceptions import InvalidInputError

    repository, _ = _repository(tmp_path)
    with pytest.raises(InvalidInputError):
        repository.read_asset("data/genes.zip", **kwargs)


def test_record_detail_binds_derived_row_to_exact_member_asset(tmp_path: Path) -> None:
    """Catch normalized JSON presented without the original member identity it derives from."""
    repository, _ = _repository(tmp_path)
    row = repository.search("data/genes.zip", member="genes.tsv", filters={"id": "PA124"}).value[0]
    fetched = repository.get_record(row["record_id"])
    expected = (FIXTURES / "genes.tsv").read_bytes()

    assert fetched.details["asset"] == {
        "dataset_id": "data/genes.zip",
        "member": "genes.tsv",
        "sha256": hashlib.sha256(expected).hexdigest(),
        "total_bytes": len(expected),
        "media_type": "text/tab-separated-values",
    }


def test_asset_content_enforces_full_body_limit_before_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch a retained member bypassing the configured full-body memory boundary."""
    from clinpgx_link.data import repository as repository_module
    from clinpgx_link.exceptions import ResponseTooLargeError

    repository, _ = _repository(tmp_path)
    monkeypatch.setattr(repository_module.settings, "max_archive_member_bytes", 10)
    with pytest.raises(ResponseTooLargeError):
        repository.asset_content("data/genes.zip", member="genes.tsv")
