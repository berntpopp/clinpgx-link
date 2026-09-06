"""Pinned-snapshot search, provenance, and validated join behavior."""

from __future__ import annotations

import base64
import hashlib
import io
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock
from time import sleep

import pytest

FIXTURES = Path(__file__).parents[1] / "fixtures" / "exports" / "sourced"
RELEASE_TAG = "data-clinpgx-core-0123456789abcdef"
GENES_RETRIEVED_AT = "2026-09-05T08:00:00Z"
PATHWAYS_RETRIEVED_AT = "2026-09-06T09:00:00Z"


class _OverlapRejectingConnection:
    """Make unsafe concurrent use deterministic while still executing real SQL."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._guard = Lock()
        self._active = False

    def execute(self, *args, **kwargs):
        with self._guard:
            if self._active:
                raise RuntimeError("concurrent sqlite connection use")
            self._active = True
        try:
            sleep(0.005)
            return self._connection.execute(*args, **kwargs)
        finally:
            with self._guard:
                self._active = False

    def close(self) -> None:
        self._connection.close()


def _archive(path: Path, members: dict[str, bytes]) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 5, 8, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100444 << 16
            archive.writestr(info, members[name])
    path.write_bytes(output.getvalue())


def _repository(tmp_path: Path, *, include_pathways: bool = False):
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
    if include_pathways:
        definitions["genes.zip"]["zz-unsupported.bin"] = b"retained external limitation"
        definitions["pathways.json.zip"] = {
            "pathways.json": (FIXTURES / "pathways.json").read_bytes()
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
                retrieved_at=(
                    PATHWAYS_RETRIEVED_AT if name == "pathways.json.zip" else GENES_RETRIEVED_AT
                ),
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


def _mixed_repository(tmp_path: Path):
    return _repository(tmp_path, include_pathways=True)


def test_repository_lists_and_describes_only_the_pinned_snapshot(tmp_path: Path) -> None:
    """Catch mutable-current reads or catalog metadata presented as imported coverage."""
    repository, built = _mixed_repository(tmp_path)

    status = repository.status()
    listed = repository.list_datasets()
    described = repository.describe("data/genes.zip")

    assert status["snapshot_id"] == built.snapshot_id
    assert status["release_tag"] == RELEASE_TAG
    assert len(listed.value) == 5
    assert listed.source.release_tag == RELEASE_TAG
    assert listed.source.url == "https://www.clinpgx.org/downloads"
    assert listed.source.retrieved_at == PATHWAYS_RETRIEVED_AT
    assert listed.source.retrieval_time_kind == "unknown"
    assert listed.source.acquired_at is None
    assert listed.source.admitted_at is None
    assert listed.source.source_scope == "snapshot"
    assert listed.source.retrieval_time_scope == "aggregate_snapshot"
    assert listed.source.sha256 == built.snapshot_id.removeprefix("sha256:")
    dataset_sources = listed.details["dataset_sources"]
    assert set(dataset_sources) == {item["dataset_id"] for item in listed.value}
    genes_source = dataset_sources["data/genes.zip"]
    assert genes_source.url.endswith("/data/genes.zip")
    assert genes_source.retrieved_at == GENES_RETRIEVED_AT
    assert (
        genes_source.sha256
        == hashlib.sha256((tmp_path / "inputs" / "genes.zip").read_bytes()).hexdigest()
    )
    assert genes_source.source_scope == "dataset"
    assert described.value["dataset_id"] == "data/genes.zip"
    assert described.details["snapshot_id"] == built.snapshot_id
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
    assert first.value[0]["fields"]["Alternate Names"] == "CPCJ, CYPIIC17, P450C2C"
    assert first.source.url.endswith("/data/genes.zip")
    assert first.source.retrieved_at == GENES_RETRIEVED_AT
    assert first.source.retrieval_time_kind == "unknown"
    assert first.source.source_scope == "dataset"
    assert first.source.retrieval_time_scope == "source_recorded"
    assert (
        first.source.sha256
        == hashlib.sha256((tmp_path / "inputs" / "genes.zip").read_bytes()).hexdigest()
    )


def test_unknown_filter_is_not_successful_empty(tmp_path: Path) -> None:
    """Catch misspelled canonical filters being reported as a valid empty result."""
    from clinpgx_link.exceptions import InvalidInputError

    repository, _ = _repository(tmp_path)
    with pytest.raises(InvalidInputError):
        repository.search("data/genes.zip", member="genes.tsv", filters={"Symobl": "CYP2C19"})


def test_search_dataset_accepts_exact_advertised_text_fields_with_explicit_member(
    tmp_path: Path,
) -> None:
    """Exact source headers query raw TSV cells and combine with canonical filters."""
    repository, _ = _repository(tmp_path)

    gene = repository.search("data/genes.zip", member="genes.tsv", filters={"Symbol": "CYP2C19"})
    summary = repository.search(
        "data/summaryAnnotations.zip",
        member="summary_annotations.tsv",
        filters={"Summary Annotation ID": "655384607", "gene": "RGS4"},
    )

    assert gene.details["total_count"] == 1
    assert gene.value[0]["id"] == "PA124"
    assert summary.details["total_count"] == 1
    assert summary.value[0]["id"] == "655384607"


def test_entity_search_skips_filter_discovery_for_no_filters_and_id_only(tmp_path: Path) -> None:
    """Universal-only searches must not discover kinds across every entity record."""
    repository, _ = _repository(tmp_path)
    statements: list[str] = []
    try:
        repository._connection.set_trace_callback(statements.append)
        repository.search_entities("gene", limit=1)
        repository.search_entities("gene", filters={"id": "PA124"}, limit=1)
    finally:
        repository._connection.set_trace_callback(None)
        repository.close()

    assert not any("SELECT DISTINCT candidate.kind" in statement for statement in statements)


def test_entity_identity_anchors_count_and_page_queries_on_identity_membership(
    tmp_path: Path,
) -> None:
    """Identity lookups must not start count or page retrieval from the broad entity lens."""
    repository, _ = _repository(tmp_path)
    statements: list[str] = []
    try:
        repository._connection.set_trace_callback(statements.append)
        repository.search_entities("gene", filters={"id": "PA124"}, limit=1)
    finally:
        repository._connection.set_trace_callback(None)
        repository.close()

    record_queries = [
        " ".join(statement.split())
        for statement in statements
        if statement.startswith(("SELECT count(*) FROM record", "SELECT r.* FROM record"))
    ]
    identity_anchor = (
        "r.record_pk IN (SELECT identity.record_pk FROM membership identity "
        "WHERE identity.value='PA124' AND identity.kind IN ('id','gene') "
        "AND identity.match_mode='exact')"
    )
    assert len(record_queries) == 2
    assert all(identity_anchor in statement for statement in record_queries)
    assert all(
        "r.record_pk IN (SELECT entity.record_pk FROM membership entity WHERE entity.kind='gene')"
        not in statement
        for statement in record_queries
    )


def test_entity_identity_preserves_missing_known_and_source_rows(tmp_path: Path) -> None:
    """Selective identity anchoring must retain absence and exact source-row identity."""
    repository, built = _repository(tmp_path)
    try:
        missing = repository.search_entities("gene", filters={"id": "PA-missing"})
        known = repository.search_entities("gene", filters={"id": "PA124"})

        assert missing.value == []
        assert missing.details["total_count"] == 0
        assert [row["id"] for row in known.value] == ["PA124"]
        assert known.value[0] == repository.get_record(known.value[0]["record_id"]).value
        assert known.details["snapshot_id"] == built.snapshot_id
        assert known.source.sha256 == built.snapshot_id.removeprefix("sha256:")
    finally:
        repository.close()


def test_entity_identity_preserves_multiplicity_order_and_pages(tmp_path: Path) -> None:
    """Repeated evidence identity must retain its full count and stable page ordering."""
    repository, _ = _repository(tmp_path)
    try:
        all_rgs4 = repository.search_entities("gene", filters={"id": "RGS4"})
        first = repository.search_entities("gene", filters={"id": "RGS4"}, limit=1)
        second = repository.search_entities("gene", filters={"id": "RGS4"}, limit=1, offset=1)

        assert all_rgs4.details["total_count"] == 2
        assert [row["id"] for row in all_rgs4.value] == ["655384602", "655384607"]
        assert first.details["total_count"] == second.details["total_count"] == 2
        assert first.details["has_more"] is True
        assert second.details["has_more"] is False
        assert first.value + second.value == all_rgs4.value
    finally:
        repository.close()


def test_entity_identity_preserves_gene_exactness_and_filter_conjunction(tmp_path: Path) -> None:
    """Gene identity stays exact while canonical filters retain member conjunction."""
    repository, _ = _repository(tmp_path)
    try:
        conjunction = repository.search_entities(
            "gene", filters={"id": "RGS4", "chemical": "olanzapine"}
        )

        assert [row["id"] for row in conjunction.value] == ["655384607"]
        assert repository.search_entities("gene", filters={"id": "CYP2C"}).value == []
        alias = repository.search_entities("gene", filters={"gene": "CYP2C"})
        assert [row["id"] for row in alias.value] == ["PA124"]
    finally:
        repository.close()


def test_non_gene_identity_preserves_member_identity_matching(tmp_path: Path) -> None:
    """The gene-only exact guard must not narrow other entity identity semantics."""
    repository, _ = _repository(tmp_path)
    try:
        chemical = repository.search_entities("chemical", filters={"id": "olanzapine"})

        assert chemical.details["total_count"] == 1
        assert [row["id"] for row in chemical.value] == ["655384607"]
    finally:
        repository.close()


def test_entity_search_still_rejects_filters_not_installed_for_entity(tmp_path: Path) -> None:
    """Skipping universal discovery must not fabricate support for other canonical filters."""
    from clinpgx_link.exceptions import InvalidInputError

    repository, _ = _repository(tmp_path)
    try:
        with pytest.raises(InvalidInputError, match="not installed") as unsupported:
            repository.search_entities("literature", filters={"source": "DPWG"})
        assert unsupported.value.field == "source"
    finally:
        repository.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"member": "genes.tsv", "filters": {"Symobl": "CYP2C19"}},
        {"filters": {"Symbol": "CYP2C19"}},
        {"member": "genes.tsv", "filters": {"Symbol": "CYP2C19"}, "match": "member"},
    ],
)
def test_search_dataset_rejects_unknown_unscoped_or_untokenized_source_fields(
    tmp_path: Path, kwargs: dict
) -> None:
    """Source fields cannot become guessed, cross-member, or generic token selectors."""
    from clinpgx_link.exceptions import InvalidInputError

    repository, _ = _repository(tmp_path)
    with pytest.raises(InvalidInputError):
        repository.search("data/genes.zip", **kwargs)


def test_search_dataset_member_matches_only_profiled_multivalue_source_field(
    tmp_path: Path,
) -> None:
    """A published Gene header uses only its declared semicolon membership parser."""
    from clinpgx_link.data.catalog import SourceInput
    from clinpgx_link.data.repository import DatasetRepository
    from clinpgx_link.ingest.builder import build_snapshot

    archive = tmp_path / "summaryAnnotations.zip"
    _archive(
        archive,
        {
            "summary_annotations.tsv": (
                b"Summary Annotation ID\tGene\tDrug(s)\n42\tCYP2C19;CYP2D6\tclopidogrel\n"
            )
        },
    )
    source = SourceInput.from_path(
        dataset_id="data/summaryAnnotations.zip",
        path=archive,
        source_url="https://api.clinpgx.org/v1/download/file/data/summaryAnnotations.zip",
        retrieved_at="2026-09-05T08:00:00Z",
        published_at="2026-08-05T01:12:00-07:00",
        media_type="application/zip",
        license_id="operator-local-only",
        tier="approved_registry",
    )
    built = build_snapshot([source], tmp_path / "out", RELEASE_TAG)
    repository = DatasetRepository(built.database)

    found = repository.search(
        "data/summaryAnnotations.zip",
        member="summary_annotations.tsv",
        filters={"Gene": "CYP2D6"},
        match="member",
    )

    assert found.details["total_count"] == 1
    assert found.value[0]["fields"]["Gene"] == "CYP2C19;CYP2D6"
    repository.close()


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
    """Catch OR broadening while ASCII-star syntax is rejected by the corrected contract."""
    from clinpgx_link.exceptions import InvalidInputError

    repository, _ = _repository(tmp_path)

    found = repository.search(
        "data/summaryAnnotations.zip",
        member="summary_ann_evidence.tsv",
        query="response risperidone",
    )
    with pytest.raises(InvalidInputError) as hostile:
        repository.search(
            "data/summaryAnnotations.zip",
            member="summary_ann_evidence.tsv",
            query='response" OR *',
        )

    assert found.details["total_count"] == 1
    assert found.value[0]["fields"]["Evidence ID"] == "655387128"
    assert hostile.value.subtype == "wildcard_query_unsupported"


@pytest.mark.parametrize("query", ["*1", "*1/*1", "CYP2C19*2"])
def test_literal_fts_rejects_ascii_star_with_fixed_exact_filter_guidance(
    tmp_path: Path, query: str
) -> None:
    """ASCII star allele notation cannot be silently tokenized into a broader query."""
    from clinpgx_link.exceptions import InvalidInputError

    repository, _ = _repository(tmp_path)
    with pytest.raises(InvalidInputError) as failure:
        repository.search("data/genes.zip", member="genes.tsv", query=query)

    assert failure.value.field == "query"
    assert failure.value.subtype == "wildcard_query_unsupported"
    assert failure.value.hint == "Omit query and use an exact gene or name filter."


@pytest.mark.parametrize(
    ("query", "expression"),
    [
        ("Reference/Reference", '"Reference" AND "Reference"'),
        ("5-fluorouracil", '"5" AND "fluorouracil"'),
        ("N-acetyltransferase", '"N" AND "acetyltransferase"'),
        ("warfarin/clopidogrel", '"warfarin" AND "clopidogrel"'),
        ("rs123", '"rs123"'),
    ],
)
def test_literal_fts_keeps_nonstar_punctuation_as_token_search(query: str, expression: str) -> None:
    """Allowed punctuation remains token syntax and is never represented as exact matching."""
    from clinpgx_link.data.repository import DatasetRepository

    assert DatasetRepository._fts_query(query) == expression


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


def test_repository_serializes_parallel_queries_on_its_shared_connection(tmp_path: Path) -> None:
    """Known rows cannot become false misses or mixed results under parallel MCP reads."""
    repository, _ = _repository(tmp_path)
    rows = repository.search("data/genes.zip", member="genes.tsv", limit=2).value
    expected_ids = {str(row["record_id"]) for row in rows}
    repository._connection = _OverlapRejectingConnection(repository._connection)  # type: ignore[assignment]
    ready = Barrier(8)

    def query(index: int) -> set[str]:
        ready.wait()
        if index % 2:
            found = repository.search("data/genes.zip", member="genes.tsv", limit=2)
            return {str(row["record_id"]) for row in found.value}
        record_id = str(rows[index % len(rows)]["record_id"])
        return {str(repository.get_record(record_id).value["record_id"])}

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(query, range(8)))
    finally:
        repository.close()

    assert all(result <= expected_ids for result in results)
    assert results.count(expected_ids) == 4


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


def test_summary_literature_join_returns_only_citing_evidence_rows_and_filters_pmids(
    tmp_path: Path,
) -> None:
    """Literature joins retain citing rows and use declared PMID member semantics."""
    from clinpgx_link.data.catalog import SourceInput
    from clinpgx_link.data.repository import DatasetRepository
    from clinpgx_link.ingest.builder import build_snapshot

    archive = tmp_path / "summaryAnnotations.zip"
    _archive(
        archive,
        {
            "summary_annotations.tsv": (b"Summary Annotation ID\tGene\n42\tCYP2C19\n43\tCYP2D6\n"),
            "summary_ann_evidence.tsv": (
                b"Summary Annotation ID\tEvidence ID\tPMID\tSummary\n"
                b"42\tE1\t111;222\tfirst\n"
                b"42\tE2\t\tuncited\n"
                b"42\tE3\t222\tsecond\n"
                b"43\tE4\t222\tother annotation\n"
            ),
        },
    )
    source = SourceInput.from_path(
        dataset_id="data/summaryAnnotations.zip",
        path=archive,
        source_url="https://api.clinpgx.org/v1/download/file/data/summaryAnnotations.zip",
        retrieved_at="2026-09-05T08:00:00Z",
        published_at="2026-08-05T01:12:00-07:00",
        media_type="application/zip",
        license_id="operator-local-only",
        tier="approved_registry",
    )
    built = build_snapshot([source], tmp_path / "out", RELEASE_TAG)
    repository = DatasetRepository(built.database)
    parent = repository.search(
        "data/summaryAnnotations.zip",
        member="summary_annotations.tsv",
        filters={"annotation_id": "42"},
    ).value[0]

    all_citations = repository.related(parent["record_id"], result_type="literature")
    pmid_222 = repository.related(parent["record_id"], result_type="literature", other_id="222")
    mismatch = repository.related(parent["record_id"], result_type="literature", other_id="999")

    assert [row["fields"]["Evidence ID"] for row in all_citations.value] == ["E1", "E3"]
    assert [row["fields"]["Evidence ID"] for row in pmid_222.value] == ["E1", "E3"]
    assert len({row["record_id"] for row in pmid_222.value}) == 2
    assert all(row["join"]["relation_kind"] == "literature" for row in pmid_222.value)
    assert all(
        row["join"]["limitation"] == "citing_evidence_row_not_bibliographic_detail"
        for row in pmid_222.value
    )
    assert mismatch.value == []
    assert mismatch.details["total_count"] == 0
    repository.close()


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
    assert archive.source.source_scope == "dataset"
    assert archive.details["media_type"] == "application/zip"
    assert member.value == (FIXTURES / "genes.tsv").read_bytes()
    assert member.source.sha256 == hashlib.sha256(member.value).hexdigest()
    assert member.source.source_scope == "member"
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
