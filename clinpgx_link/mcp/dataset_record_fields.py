"""Developer-owned field profiles for safely shaping indexed dataset rows."""

from __future__ import annotations

import re
from math import isfinite
from typing import Any

_PHARMCAT_DIPLOTYPE_FIELDS = frozenset(
    {"activityScore", "diplotype", "diplotypekey", "generesult", "lookupkey", "phenotype"}
)
_PHARMCAT_REQUIRED_DIPLOTYPE_FIELDS = frozenset(
    {"diplotype", "diplotypekey", "generesult", "lookupkey", "phenotype"}
)
_PHARMCAT_DIPLOTYPE_POINTER = re.compile(r"/[0-9]+/diplotypes/[0-9]+")
_STAR_ALLELE = r"\*[0-9]+[A-Za-z]?(?:x(?:[0-9]+|≥[0-9]+))?"
_NUCLEOTIDE_CHANGE = (
    r"[0-9]+(?:[-+][0-9]+)?(?:_[0-9]+)?"
    r"(?:[ACGT]>(?:[ACGT]|del(?:\+C[nN])?)|(?:delins|del|dup|ins)[ACGT]*)"
)
_CODING_ALLELE = (
    rf"c\.{_NUCLEOTIDE_CHANGE}(?:, c\.{_NUCLEOTIDE_CHANGE})?"
    rf"(?: \((?:{_STAR_ALLELE}|HapB[0-9]+)\))?"
)
_PHARMCAT_ALLELE_KEY = re.compile(
    rf"(?:Reference|{_STAR_ALLELE}(?: ?\+ ?{_STAR_ALLELE})*|{_CODING_ALLELE})"
)

_CODE_OWNED_FIELDS_BY_MEMBER: dict[tuple[str, str], frozenset[str]] = {
    ("data/genes.zip", "genes.tsv"): frozenset(
        {
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
        }
    ),
    ("data/summaryAnnotations.zip", "summary_annotations.tsv"): frozenset(
        {
            "Summary Annotation ID",
            "Variant/Haplotypes",
            "Gene",
            "Level of Evidence",
            "Level Override",
            "Level Modifiers",
            "Score",
            "Phenotype Category",
            "PMID Count",
            "Evidence Count",
            "Drug(s)",
            "Phenotype(s)",
            "Latest History Date (YYYY-MM-DD)",
            "URL",
            "Specialty Population",
        }
    ),
    ("data/summaryAnnotations.zip", "summary_ann_alleles.tsv"): frozenset(
        {"Summary Annotation ID", "Genotype/Allele", "Annotation Text", "Allele Function"}
    ),
    ("data/summaryAnnotations.zip", "summary_ann_evidence.tsv"): frozenset(
        {
            "Summary Annotation ID",
            "Evidence ID",
            "Evidence Type",
            "Evidence URL",
            "PMID",
            "Summary",
            "Score",
        }
    ),
    ("data/relationships.zip", "relationships.tsv"): frozenset(
        {
            "Entity1_id",
            "Entity1_name",
            "Entity1_type",
            "Entity2_id",
            "Entity2_name",
            "Entity2_type",
            "Evidence",
            "Association",
            "PK",
            "PD",
            "PMIDs",
        }
    ),
    ("data/clinpgxHaplotypes.zip", "clinpgx_haplotypes.tsv"): frozenset(
        {"Accession ID", "Gene", "Allele Name", "HGVS", "Structural Variation", "AMP Level"}
    ),
}


def _is_pharmcat_diplotype_row(row: dict[str, Any]) -> bool:
    return (
        row.get("dataset_id") == "data/pharmcat.zip"
        and row.get("member") == "phenotypes.json"
        and isinstance(row.get("json_pointer"), str)
        and _PHARMCAT_DIPLOTYPE_POINTER.fullmatch(str(row["json_pointer"])) is not None
    )


def trusted_fields_for_row(row: dict[str, Any]) -> frozenset[str]:
    """Return only a profile shipped by this program, never source metadata."""
    if _is_pharmcat_diplotype_row(row):
        return _PHARMCAT_DIPLOTYPE_FIELDS
    return _CODE_OWNED_FIELDS_BY_MEMBER.get(
        (str(row.get("dataset_id", "")), str(row.get("member", ""))), frozenset()
    )


def profiled_nested_fields_are_safe(row: dict[str, Any]) -> bool:
    """Validate the sole profiled dynamic-key map before allowing inline output."""
    if not _is_pharmcat_diplotype_row(row):
        return True
    fields = row.get("fields")
    if (
        not isinstance(fields, dict)
        or not _PHARMCAT_REQUIRED_DIPLOTYPE_FIELDS <= set(fields) <= _PHARMCAT_DIPLOTYPE_FIELDS
        or any(
            not isinstance(fields[name], str) or len(fields[name]) > 512
            for name in _PHARMCAT_REQUIRED_DIPLOTYPE_FIELDS - {"diplotypekey"}
        )
    ):
        return False
    key = fields.get("diplotypekey")
    activity_score = fields.get("activityScore")
    return (
        isinstance(key, dict)
        and 1 <= len(key) <= 2
        and (
            activity_score is None
            or (
                isinstance(activity_score, (str, int, float))
                and not isinstance(activity_score, bool)
                and (not isinstance(activity_score, str) or len(activity_score) <= 128)
                and (not isinstance(activity_score, float) or isfinite(activity_score))
            )
        )
        and all(
            isinstance(name, str)
            and _PHARMCAT_ALLELE_KEY.fullmatch(name) is not None
            and type(count) is int
            and count in {1, 2}
            for name, count in key.items()
        )
    )


__all__ = ["profiled_nested_fields_are_safe", "trusted_fields_for_row"]
