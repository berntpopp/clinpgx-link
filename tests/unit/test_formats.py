"""Loss-preserving parsers for heterogeneous ClinPGx export members."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parents[1] / "fixtures" / "exports"


def test_tabular_reader_preserves_original_headers_empty_cells_and_ordinals() -> None:
    """Catch header normalization, empty-value dropping, or unstable source row identity."""
    from clinpgx_link.ingest.tabular import TabularReader

    raw = (FIXTURES / "sourced" / "genes.tsv").read_bytes()
    reader = TabularReader(io.BytesIO(raw), delimiter="\t")
    rows = list(reader)

    assert reader.headers == (
        "PharmGKB Accession Id",
        "NCBI Gene ID",
        "HGNC ID",
        "Ensembl Id",
        "Name",
        "Symbol",
        "Alternate Names",
        "Alternate Symbols",
        "Is VIP",
        "Has Variant Annotation",
    )
    assert [row.ordinal for row in rows] == [1, 2]
    assert rows[0].fields["Symbol"] == "CYP2C19"
    assert rows[1].fields["Alternate Names"] == ""
    assert tuple(rows[0].fields) == reader.headers


@pytest.mark.parametrize("fixture", ["duplicate_headers.tsv", "malformed_width.tsv"])
def test_tabular_reader_rejects_ambiguous_or_incomplete_rows(fixture: str) -> None:
    """Catch silent column overwrite or padding/truncation of malformed source rows."""
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.ingest.tabular import TabularReader

    raw = (FIXTURES / "adversarial" / fixture).read_bytes()
    with pytest.raises(DataValidationError):
        list(TabularReader(io.BytesIO(raw), delimiter="\t"))


def test_tabular_reader_accepts_the_measured_148743_character_field() -> None:
    """Catch use of Python's smaller default CSV field limit on a measured source value."""
    from clinpgx_link.ingest.tabular import TabularReader

    large = "x" * 148_743
    raw = f"ID\tText\nPA1\t{large}\n".encode()
    rows = list(TabularReader(io.BytesIO(raw), delimiter="\t", max_field_chars=200_000))
    assert rows[0].fields == {"ID": "PA1", "Text": large}


class _NoUnboundedRead(io.BytesIO):
    """A real byte stream that rejects whole-input reads."""

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise AssertionError("parser attempted an unbounded read")
        return super().read(size)


def test_json_records_stream_array_items_and_nested_parent_pointers() -> None:
    """Catch whole-file loading or loss of nested PharmCAT record provenance."""
    from clinpgx_link.ingest.json_records import iter_json_records

    raw = (FIXTURES / "sourced" / "pharmcat_phenotypes.json").read_bytes()
    records = list(iter_json_records(_NoUnboundedRead(raw)))
    by_pointer = {record.pointer: record for record in records}

    assert by_pointer["/0"].value["gene"] == "TPMT"
    assert by_pointer["/0"].parent_pointer == ""
    assert by_pointer["/0/diplotypes/0"].value["diplotype"] == "*1/*1"
    assert by_pointer["/0/diplotypes/0"].parent_pointer == "/0"
    assert by_pointer["/0/diplotypeFunctions/0"].value["phenotype"] == "Indeterminate"
    assert len([record for record in records if "/diplotypes/" in record.pointer]) == 2


def test_json_records_keep_object_root_and_per_file_guideline_children() -> None:
    """Catch treating a per-guideline object wrapper as an array or discarding its root."""
    from clinpgx_link.ingest.json_records import iter_json_records

    raw = (FIXTURES / "sourced" / "PA166363221.json").read_bytes()
    records = list(iter_json_records(io.BytesIO(raw)))
    by_pointer = {record.pointer: record for record in records}

    assert by_pointer[""].value["guideline"]["id"] == "PA166363221"
    assert by_pointer["/citations/0"].parent_pointer == ""
    assert by_pointer["/guideline/relatedGenes/0"].value["symbol"] == "SLCO1B1"
    assert by_pointer["/guideline/relatedGenes/0"].parent_pointer == ""


def test_json_records_index_array_root_pathways_without_fabricating_a_wrapper() -> None:
    """Catch loss of top-level array ordinals or an invented pathway object wrapper."""
    from clinpgx_link.ingest.json_records import iter_json_records

    raw = (FIXTURES / "sourced" / "pathways.json").read_bytes()
    top_level = [
        record for record in iter_json_records(io.BytesIO(raw)) if record.parent_pointer == ""
    ]
    assert [(record.pointer, record.value["id"]) for record in top_level] == [
        ("/0", "PA166163705"),
        ("/1", "PA166160830"),
    ]


def test_json_records_do_not_round_source_numbers() -> None:
    """Catch conversion of source decimals through an IEEE-754 binary float."""
    from decimal import Decimal

    from clinpgx_link.ingest.json_records import iter_json_records

    records = list(
        iter_json_records(io.BytesIO(b'[{"frequency":0.12345678901234567890123456789}]'))
    )
    assert records[0].value["frequency"] == Decimal("0.12345678901234567890123456789")


def _profiled_workbook() -> bytes:
    from openpyxl import Workbook

    output = io.BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["Drug or Ingredient", "Source", "Code Type", "Code"])
    sheet.append(["clopidogrel", "RxNorm", "RxNorm", "32968"])
    sheet.append(["clopidogrel", "Synthetic profile", "Derived", "=1+1"])
    sheet.append(["missing-code", "Synthetic profile", "Derived"])
    sheet.append(["merged note"])
    sheet.merge_cells("A5:B5")
    workbook.save(output)
    return output.getvalue()


def test_spreadsheet_parser_preserves_profiled_rows_formulas_merges_and_missing_cells() -> None:
    """Catch flattened XLSX values that hide formulas, merge semantics, or absent cells."""
    from clinpgx_link.ingest.spreadsheets import parse_spreadsheet

    document = parse_spreadsheet(io.BytesIO(_profiled_workbook()))
    sheet = document.sheets[0]

    assert sheet.name == "Sheet1"
    assert sheet.headers == ("Drug or Ingredient", "Source", "Code Type", "Code")
    assert sheet.rows[0].fields["Code"].value == "32968"
    formula = sheet.rows[1].fields["Code"]
    assert formula.formula == "=1+1"
    assert formula.cached_value is None
    assert formula.present is True
    missing = sheet.rows[2].fields["Code"]
    assert missing.value is None
    assert missing.present is False
    merged = sheet.rows[3].fields["Source"]
    assert merged.merged_range == "A5:B5"
    assert merged.merge_anchor == "A5"
    assert sheet.hidden is False


def test_spreadsheet_parser_rejects_extension_without_ooxml_signature() -> None:
    """Catch selecting a parser solely from an attacker-controlled filename extension."""
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.ingest.spreadsheets import parse_spreadsheet

    raw = (FIXTURES / "adversarial" / "not_really_xlsx.xlsx").read_bytes()
    with pytest.raises(DataValidationError):
        parse_spreadsheet(io.BytesIO(raw))


def test_frequency_field_metadata_publishes_only_profiled_match_semantics() -> None:
    """Catch usable allele frequency filters being absent from dataset discovery."""
    from clinpgx_link.data.coverage import field_metadata

    fields = field_metadata(
        "data/pharmgkb_haplotype_frequencies_AllOfUs.zip",
        "AllOfUs_Frequencies_v7/allele/CYP2C19_allele.tsv",
        (
            "biogeographic_group",
            "allele",
            "n_haplotype",
            "frequencies",
            "in_cpic",
            "notes",
            "n_subjects_genotyped",
        ),
    )

    assert fields[1] == {
        "name": "allele",
        "match_modes": ["exact"],
        "tokenizer": None,
        "semantic_target": "allele",
    }
    assert fields[0]["semantic_target"] is None
