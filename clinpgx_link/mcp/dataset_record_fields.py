"""Developer-owned field profiles for safely shaping indexed dataset rows."""

from __future__ import annotations

from typing import Any

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


def trusted_fields_for_row(row: dict[str, Any]) -> frozenset[str]:
    """Return only a profile shipped by this program, never source metadata."""
    return _CODE_OWNED_FIELDS_BY_MEMBER.get(
        (str(row.get("dataset_id", "")), str(row.get("member", ""))), frozenset()
    )


__all__ = ["trusted_fields_for_row"]
