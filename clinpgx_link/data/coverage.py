"""Declared field semantics for lossless source rows and conservative local search."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Membership:
    kind: str
    value: str
    match_mode: str
    source_field: str
    tokenizer: str | None = None


@dataclass(frozen=True)
class FieldProfile:
    semantic_target: str | None = None
    tokenizer: str | None = None


_PROFILES: dict[tuple[str, str], dict[str, FieldProfile]] = {
    ("data/chemicals.zip", "chemicals.tsv"): {
        "PharmGKB Accession Id": FieldProfile("id"),
        "Name": FieldProfile("chemical"),
    },
    ("data/drugs.zip", "drugs.tsv"): {
        "PharmGKB Accession Id": FieldProfile("id"),
        "Name": FieldProfile("chemical"),
    },
    ("data/genes.zip", "genes.tsv"): {
        "PharmGKB Accession Id": FieldProfile("id"),
        "Name": FieldProfile("name"),
        "Symbol": FieldProfile("gene"),
        "Alternate Names": FieldProfile("name", "semicolon"),
        "Alternate Symbols": FieldProfile("gene", "semicolon"),
    },
    ("data/summaryAnnotations.zip", "summary_annotations.tsv"): {
        "Summary Annotation ID": FieldProfile("annotation_id"),
        "Variant/Haplotypes": FieldProfile("variant", "semicolon"),
        "Gene": FieldProfile("gene", "semicolon"),
        "Drug(s)": FieldProfile("chemical", "semicolon"),
        "Phenotype(s)": FieldProfile("name", "semicolon"),
    },
    ("data/summaryAnnotations.zip", "summary_ann_evidence.tsv"): {
        "Summary Annotation ID": FieldProfile("annotation_id"),
        "Evidence ID": FieldProfile("id"),
        "PMID": FieldProfile("literature", "semicolon"),
    },
    ("data/summaryAnnotations.zip", "summary_ann_alleles.tsv"): {
        "Summary Annotation ID": FieldProfile("annotation_id"),
        "Genotype/Allele": FieldProfile("allele"),
    },
    ("data/summaryAnnotations.zip", "summary_ann_history.tsv"): {
        "Summary Annotation ID": FieldProfile("annotation_id"),
    },
    ("data/variants.zip", "variants.tsv"): {
        "Variant ID": FieldProfile("id"),
        "Variant Name": FieldProfile("variant"),
        "Gene Symbols": FieldProfile("gene", "csv"),
        "Synonyms": FieldProfile("variant", "csv"),
    },
    ("data/clinicalVariants.zip", "clinicalVariants.tsv"): {
        "variant": FieldProfile("variant", "csv"),
        "gene": FieldProfile("gene", "csv"),
        "chemicals": FieldProfile("chemical", "csv"),
        "phenotypes": FieldProfile("name", "csv"),
    },
    ("data/drugLabels.zip", "drugLabels.tsv"): {
        "PharmGKB ID": FieldProfile("id"),
        "Name": FieldProfile("name"),
        "Source": FieldProfile("source"),
        "Chemicals": FieldProfile("chemical", "semicolon"),
        "Genes": FieldProfile("gene", "semicolon"),
        "Variants/Haplotypes": FieldProfile("variant", "semicolon"),
    },
    ("data/drugLabels.zip", "drugLabels.byGene.tsv"): {
        "Gene ID": FieldProfile("id"),
        "Gene Symbol": FieldProfile("gene"),
        "Label IDs": FieldProfile("id", "semicolon"),
    },
    ("data/phenotypes.zip", "phenotypes.tsv"): {
        "PharmGKB Accession Id": FieldProfile("id"),
        "Name": FieldProfile("name"),
        "Alternate Names": FieldProfile("name", "semicolon"),
    },
    ("data/occurrences.zip", "occurrences.tsv"): {
        "Source ID": FieldProfile("id"),
        "Source Name": FieldProfile("name"),
        "Object ID": FieldProfile("id"),
        "Object Name": FieldProfile("name"),
    },
    ("data/relationships.zip", "relationships.tsv"): {
        "Entity1_id": FieldProfile(),
        "Entity1_name": FieldProfile("name"),
        "Entity2_id": FieldProfile(),
        "Entity2_name": FieldProfile("name"),
        "PMIDs": FieldProfile("literature", "csv"),
    },
}

_CLINPGX_HAPLOTYPE_PROFILE = {
    "Accession ID": FieldProfile("id"),
    "Gene": FieldProfile("gene"),
    "Allele Name": FieldProfile("allele"),
}
for _haplotype_member in (
    "clinpgx_haplotypes.tsv",
    "clinpgxHaplotypes_named_alleles.tsv",
    "clinpgxHaplotypes_star_alleles.tsv",
):
    _PROFILES[("data/clinpgxHaplotypes.zip", _haplotype_member)] = _CLINPGX_HAPLOTYPE_PROFILE

for _variant_member in ("var_drug_ann.tsv", "var_fa_ann.tsv", "var_pheno_ann.tsv"):
    _PROFILES[("data/variantAnnotations.zip", _variant_member)] = {
        "Variant Annotation ID": FieldProfile("id"),
        "Variant/Haplotypes": FieldProfile("variant", "semicolon"),
        "Gene": FieldProfile("gene", "semicolon"),
        "Drug(s)": FieldProfile("chemical", "semicolon"),
        "PMID": FieldProfile("literature", "semicolon"),
    }

_PROFILES[("data/variantAnnotations.zip", "study_parameters.tsv")] = {
    "Study Parameters ID": FieldProfile("id"),
    "Variant Annotation ID": FieldProfile("annotation_id"),
}


def _tokens(value: str, tokenizer: str) -> Iterable[str]:
    delimiter = ";" if tokenizer == "semicolon" else ","
    for token in value.split(delimiter):
        stripped = token.strip()
        if stripped:
            yield stripped


def _profile(dataset_id: str, member: str) -> dict[str, FieldProfile]:
    profile = dict(_PROFILES.get((dataset_id, member), {}))
    if dataset_id == "data/pharmgkb_haplotype_frequencies_AllOfUs.zip":
        profile.update(
            {
                "allele": FieldProfile("allele"),
                "phenotype": FieldProfile("name"),
            }
        )
    elif dataset_id == "data/pharmgkb_haplotype_frequencies_UKBB.zip":
        profile.update({"Allele": FieldProfile("allele"), "Source": FieldProfile("source")})
    return profile


def field_metadata(dataset_id: str, member: str, headers: tuple[str, ...]) -> list[dict[str, Any]]:
    profile = _profile(dataset_id, member)
    result: list[dict[str, Any]] = []
    for header in headers:
        declared = profile.get(header, FieldProfile())
        match_modes = ["exact"]
        if declared.tokenizer is not None:
            match_modes.append("member")
        result.append(
            {
                "name": header,
                "match_modes": match_modes,
                "tokenizer": declared.tokenizer,
                "semantic_target": declared.semantic_target,
            }
        )
    return result


def tabular_memberships(
    dataset_id: str, member: str, fields: dict[str, str]
) -> tuple[Membership, ...]:
    profile = _profile(dataset_id, member)
    gene_from_member: str | None = None
    if dataset_id == "data/pharmgkb_haplotype_frequencies_AllOfUs.zip":
        name = member.rsplit("/", maxsplit=1)[-1]
        for suffix in ("_activity_score.tsv", "_allele.tsv", "_phenotype.tsv"):
            if name.endswith(suffix):
                gene_from_member = name.removesuffix(suffix)
                break
    elif dataset_id == "data/pharmgkb_haplotype_frequencies_UKBB.zip":
        name = member.rsplit("/", maxsplit=1)[-1]
        if name.endswith("_UKBB_frequencies.tsv"):
            gene_from_member = name.removesuffix("_UKBB_frequencies.tsv")
    memberships: list[Membership] = []
    for source_field, declared in profile.items():
        value = fields.get(source_field, "")
        if not value or declared.semantic_target is None:
            continue
        kinds = [declared.semantic_target]
        if source_field == "Summary Annotation ID":
            kinds.append("id")
        if source_field in {"Name", "Symbol", "Gene Symbol", "Variant Name"}:
            kinds.append("name")
        for kind in kinds:
            memberships.append(Membership(kind, value, "exact", source_field))
            if kind == "allele":
                memberships.append(Membership("name", value, "exact", source_field))
            if declared.tokenizer is not None:
                memberships.extend(
                    Membership(kind, token, "member", source_field, declared.tokenizer)
                    for token in _tokens(value, declared.tokenizer)
                )
    if member == "relationships.tsv":
        for prefix in ("Entity1", "Entity2"):
            identifier = fields.get(f"{prefix}_id", "")
            entity_type = fields.get(f"{prefix}_type", "").lower()
            if identifier and entity_type in {"gene", "chemical", "variant", "disease"}:
                memberships.append(Membership(entity_type, identifier, "exact", f"{prefix}_id"))
    if member == "occurrences.tsv":
        object_type = fields.get("Object Type", "").lower()
        object_id = fields.get("Object ID", "")
        if object_type in {"gene", "chemical", "variant", "disease"} and object_id:
            memberships.append(Membership(object_type, object_id, "exact", "Object ID"))
    if gene_from_member:
        memberships.append(Membership("gene", gene_from_member, "exact", "$member"))
    return tuple(dict.fromkeys(memberships))


def spreadsheet_memberships(
    dataset_id: str, member: str, fields: dict[str, Any]
) -> tuple[Membership, ...]:
    """Project only validated CPIC mapping cells into canonical search semantics."""
    if dataset_id != "data/cpic.drug.mapping.zip" or not member.startswith("drug.mapping."):
        return ()

    def value(name: str) -> str | None:
        cell = fields.get(name)
        if not isinstance(cell, dict):
            return None
        raw = cell.get("value")
        return str(raw) if raw is not None and str(raw) else None

    memberships: list[Membership] = []
    drug = value("Drug or Ingredient")
    source = value("Source")
    code = value("Code")
    if drug:
        memberships.extend(
            (
                Membership("chemical", drug, "exact", "Drug or Ingredient"),
                Membership("name", drug, "exact", "Drug or Ingredient"),
            )
        )
    if source:
        memberships.append(Membership("source", source, "exact", "Source"))
    if code:
        memberships.append(Membership("id", code, "exact", "Code"))
        memberships.extend(
            Membership("id", item.strip(), "member", "Code", "csv")
            for item in code.split(",")
            if item.strip()
        )
    return tuple(dict.fromkeys(memberships))


def contextual_json_memberships(
    dataset_id: str,
    member: str,
    pointer: str,
    value: Any,
    context_gene: str | None,
) -> tuple[Membership, ...]:
    """Add profiled parent context without copying it into the retained source row."""
    if dataset_id != "data/pharmcat.zip":
        return ()
    memberships: list[Membership] = []
    if context_gene:
        memberships.append(Membership("gene", context_gene, "exact", "$parent/gene"))
    if "/namedAlleles/" in pointer and isinstance(value, dict):
        allele = value.get("name")
        if isinstance(allele, str) and allele:
            memberships.append(Membership("allele", allele, "exact", "/name"))
            memberships.append(Membership("name", allele, "exact", "/name"))
    return tuple(memberships)


def _member(memberships: list[Membership], value: Any, kind: str, source_field: str) -> None:
    if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
        memberships.append(Membership(kind, str(value), "exact", source_field))


def _guideline_memberships(pointer: str, value: Any) -> tuple[Membership, ...]:
    memberships: list[Membership] = []
    if pointer == "" and isinstance(value, dict):
        guideline = value.get("guideline")
        if isinstance(guideline, dict):
            _member(memberships, guideline.get("id"), "id", "/guideline/id")
            _member(memberships, guideline.get("name"), "name", "/guideline/name")
            _member(memberships, guideline.get("source"), "source", "/guideline/source")
            for field, kind in (("relatedGenes", "gene"), ("relatedChemicals", "chemical")):
                related = guideline.get(field)
                if isinstance(related, list):
                    for index, item in enumerate(related):
                        if isinstance(item, dict):
                            _member(
                                memberships,
                                item.get("id"),
                                kind,
                                f"/guideline/{field}/{index}/id",
                            )
                            _member(
                                memberships,
                                item.get("name"),
                                "name",
                                f"/guideline/{field}/{index}/name",
                            )
    elif pointer.startswith("/citations/") and isinstance(value, dict):
        _member(memberships, value.get("id"), "literature", f"{pointer}/id")
        _member(memberships, value.get("title"), "name", f"{pointer}/title")
    return tuple(dict.fromkeys(memberships))


def _pathway_memberships(value: Any) -> tuple[Membership, ...]:
    if not isinstance(value, dict):
        return ()
    memberships: list[Membership] = []
    _member(memberships, value.get("id"), "id", "/id")
    _member(memberships, value.get("name"), "name", "/name")
    for field, kind in (("genes", "gene"), ("chemicals", "chemical")):
        related = value.get(field)
        if isinstance(related, list):
            for index, item in enumerate(related):
                if isinstance(item, dict):
                    _member(memberships, item.get("id"), kind, f"/{field}/{index}/id")
                    _member(memberships, item.get("name"), "name", f"/{field}/{index}/name")
    return tuple(dict.fromkeys(memberships))


def json_memberships(
    dataset_id: str, member: str, pointer: str, value: Any
) -> tuple[Membership, ...]:
    """Project only dataset/pointer combinations backed by an explicit source profile."""
    if dataset_id in {
        "data/guidelineAnnotations.json.zip",
        "data/guidelineAnnotations.extended.json.zip",
    } and member.endswith(".json"):
        return _guideline_memberships(pointer, value)
    if dataset_id == "data/pathways.json.zip" and member == "pathways.json":
        return _pathway_memberships(value)
    if dataset_id == "data/pharmcat.zip" and member == "phenotypes.json":
        memberships: list[Membership] = []
        if isinstance(value, dict):
            _member(memberships, value.get("gene"), "gene", f"{pointer}/gene")
        return tuple(memberships)
    return ()


def primary_identifier(memberships: Iterable[Membership]) -> str | None:
    for membership in memberships:
        if membership.kind in {"id", "annotation_id"} and membership.match_mode == "exact":
            return membership.value
    return None


def known_filters(dataset_id: str, member: str | None = None) -> frozenset[str]:
    keys: set[str] = set()
    for (profile_dataset, profile_member), profile in _PROFILES.items():
        if profile_dataset != dataset_id or (member is not None and profile_member != member):
            continue
        keys.update(
            item.semantic_target for item in profile.values() if item.semantic_target is not None
        )
        if any(item.semantic_target == "allele" for item in profile.values()):
            keys.add("name")
        if any(field == "Summary Annotation ID" for field in profile):
            keys.add("id")
    json_filters = {
        "data/guidelineAnnotations.json.zip": {
            "id",
            "name",
            "gene",
            "chemical",
            "source",
        },
        "data/guidelineAnnotations.extended.json.zip": {
            "id",
            "name",
            "gene",
            "chemical",
            "source",
        },
        "data/pathways.json.zip": {"id", "name", "gene", "chemical"},
        "data/pharmcat.zip": {"name", "gene"},
    }
    keys.update(json_filters.get(dataset_id, set()))
    if dataset_id in {"data/relationships.zip", "data/occurrences.zip"}:
        keys.update({"id", "name", "gene", "chemical", "variant"})
    if dataset_id == "data/cpic.drug.mapping.zip":
        keys.update({"id", "name", "chemical", "source"})
    if dataset_id.startswith("data/pharmgkb_haplotype_frequencies_"):
        keys.update({"name", "gene"})
    return frozenset(keys)


__all__ = [
    "FieldProfile",
    "Membership",
    "contextual_json_memberships",
    "field_metadata",
    "json_memberships",
    "known_filters",
    "primary_identifier",
    "spreadsheet_memberships",
    "tabular_memberships",
]
