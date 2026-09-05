"""Immutable, developer-owned field profiles for normalized source records."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

ProfileMode = Literal["minimal", "compact", "standard", "full"]
SelectorKind = Literal["tabular", "json_pointer"]


@dataclass(frozen=True)
class FieldDeclaration:
    """One trusted source-column name and its code-owned purpose."""

    name: str
    required: bool
    description: str
    inclusion_reason: str


@dataclass(frozen=True)
class ModeFieldPolicy:
    """Ordered fields added by a response mode."""

    fields: tuple[str, ...]
    include_all_reachable: bool = False


@dataclass(frozen=True)
class ProfileModes:
    minimal: ModeFieldPolicy
    compact: ModeFieldPolicy
    standard: ModeFieldPolicy
    full: ModeFieldPolicy


@dataclass(frozen=True)
class ShapeSelector:
    kind: SelectorKind
    pointer_pattern: str | None = None
    description: str = "Each normalized tabular row in the named member."

    def matches(self, json_pointer: str | None) -> bool:
        if self.kind == "tabular":
            return json_pointer is None
        return (
            isinstance(json_pointer, str)
            and self.pointer_pattern is not None
            and re.fullmatch(self.pointer_pattern, json_pointer) is not None
        )


@dataclass(frozen=True)
class RecordProfile:
    """A source-family policy; it is never inferred from candidate keys."""

    profile_id: str
    dataset_id: str
    member: str
    shape_id: str
    selector: ShapeSelector
    fields: tuple[FieldDeclaration, ...]
    modes: ProfileModes
    description: str

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields if field.required)

    @property
    def optional_fields(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields if not field.required)

    @property
    def trusted_fields(self) -> frozenset[str]:
        return frozenset(field.name for field in self.fields)


def _field(
    name: str, description: str, inclusion_reason: str, *, required: bool = False
) -> FieldDeclaration:
    return FieldDeclaration(name, required, description, inclusion_reason)


def _modes(
    minimal: tuple[str, ...], compact: tuple[str, ...], standard: tuple[str, ...]
) -> ProfileModes:
    return ProfileModes(
        ModeFieldPolicy(minimal),
        ModeFieldPolicy(compact),
        ModeFieldPolicy(standard),
        ModeFieldPolicy(standard, include_all_reachable=True),
    )


_HAPLOTYPE_FIELDS = (
    _field(
        "Accession ID",
        "ClinPGx allele accession.",
        "Stable source identity.",
        required=True,
    ),
    _field("Gene", "Source gene symbol.", "Stable shape subject.", required=True),
    _field("Allele Name", "Source allele name.", "Stable shape subject.", required=True),
    _field("HGVS", "Source HGVS expression.", "Common variant evidence."),
    _field(
        "Structural Variation",
        "Source structural-variation description.",
        "Common variant evidence.",
    ),
    _field("AMP Level", "Source AMP level.", "Evidence qualification context."),
)
_HAPLOTYPE_MODES = _modes(
    ("Accession ID", "Gene", "Allele Name"),
    ("Accession ID", "Gene", "Allele Name", "HGVS", "Structural Variation"),
    ("Accession ID", "Gene", "Allele Name", "HGVS", "Structural Variation", "AMP Level"),
)


RECORD_PROFILES: tuple[RecordProfile, ...] = (
    RecordProfile(
        "clinpgx.genes.v1",
        "data/genes.zip",
        "genes.tsv",
        "tabular_row",
        ShapeSelector("tabular"),
        (
            _field(
                "PharmGKB Accession Id",
                "ClinPGx gene accession.",
                "Stable source identity.",
                required=True,
            ),
            _field("NCBI Gene ID", "NCBI Gene identifier.", "Common cross-database evidence link."),
            _field("HGNC ID", "HGNC gene identifier.", "Common cross-database evidence link."),
            _field("Ensembl Id", "Ensembl gene identifier.", "Additional source identifier."),
            _field("Name", "Source gene name.", "Common human-readable identity."),
            _field(
                "Symbol",
                "Source gene symbol.",
                "Stable shape identity and common lookup.",
                required=True,
            ),
            _field(
                "Alternate Names", "Source alternate gene names.", "Additional discovery evidence."
            ),
            _field(
                "Alternate Symbols",
                "Source alternate gene symbols.",
                "Additional discovery evidence.",
            ),
            _field(
                "Is VIP",
                "Source VIP flag.",
                "Approved source evidence; not independently verified.",
            ),
            _field(
                "Has Variant Annotation",
                "Source variant-annotation flag.",
                "Approved source evidence.",
            ),
        ),
        _modes(
            ("PharmGKB Accession Id", "Symbol"),
            ("PharmGKB Accession Id", "Symbol", "Name", "NCBI Gene ID", "HGNC ID"),
            (
                "PharmGKB Accession Id",
                "Symbol",
                "Name",
                "NCBI Gene ID",
                "HGNC ID",
                "Ensembl Id",
                "Alternate Names",
                "Alternate Symbols",
                "Is VIP",
                "Has Variant Annotation",
            ),
        ),
        "ClinPGx gene catalog rows.",
    ),
    RecordProfile(
        "clinpgx.summary_annotation.v1",
        "data/summaryAnnotations.zip",
        "summary_annotations.tsv",
        "tabular_row",
        ShapeSelector("tabular"),
        (
            _field(
                "Summary Annotation ID",
                "Summary annotation identifier.",
                "Stable source identity.",
                required=True,
            ),
            _field(
                "Variant/Haplotypes",
                "Variants or haplotypes named by the source.",
                "Core evidence subject.",
            ),
            _field("Gene", "Genes named by the source.", "Core evidence subject."),
            _field("Level of Evidence", "Source evidence level.", "Common evidence qualification."),
            _field(
                "Level Override",
                "Source evidence-level override.",
                "Evidence qualification context.",
            ),
            _field(
                "Level Modifiers",
                "Source evidence-level modifiers.",
                "Evidence qualification context.",
            ),
            _field("Score", "Source annotation score.", "Evidence qualification context."),
            _field("Phenotype Category", "Source phenotype category.", "Common evidence summary."),
            _field("PMID Count", "Count of linked PubMed records.", "Common evidence summary."),
            _field(
                "Evidence Count", "Count of linked evidence records.", "Common evidence summary."
            ),
            _field("Drug(s)", "Drugs named by the source.", "Core evidence subject."),
            _field("Phenotype(s)", "Phenotypes named by the source.", "Core evidence subject."),
            _field(
                "Latest History Date (YYYY-MM-DD)",
                "Latest source history date.",
                "Source recency context.",
            ),
            _field("URL", "Source annotation URL.", "Source evidence locator."),
            _field(
                "Specialty Population", "Specialty population text.", "Additional scope context."
            ),
        ),
        _modes(
            ("Summary Annotation ID",),
            (
                "Summary Annotation ID",
                "Variant/Haplotypes",
                "Gene",
                "Drug(s)",
                "Phenotype(s)",
                "Level of Evidence",
            ),
            (
                "Summary Annotation ID",
                "Variant/Haplotypes",
                "Gene",
                "Drug(s)",
                "Phenotype(s)",
                "Level of Evidence",
                "Level Override",
                "Level Modifiers",
                "Score",
                "Phenotype Category",
                "PMID Count",
                "Evidence Count",
                "Latest History Date (YYYY-MM-DD)",
                "URL",
                "Specialty Population",
            ),
        ),
        "ClinPGx summary annotation rows.",
    ),
    RecordProfile(
        "clinpgx.summary_allele.v1",
        "data/summaryAnnotations.zip",
        "summary_ann_alleles.tsv",
        "tabular_row",
        ShapeSelector("tabular"),
        (
            _field(
                "Summary Annotation ID",
                "Owning summary annotation identifier.",
                "Stable join identity.",
                required=True,
            ),
            _field(
                "Genotype/Allele",
                "Source genotype or allele label.",
                "Stable row subject.",
                required=True,
            ),
            _field("Annotation Text", "Source allele annotation text.", "Common evidence detail."),
            _field("Allele Function", "Source allele function label.", "Common evidence detail."),
        ),
        _modes(
            ("Summary Annotation ID", "Genotype/Allele"),
            ("Summary Annotation ID", "Genotype/Allele", "Allele Function"),
            ("Summary Annotation ID", "Genotype/Allele", "Allele Function", "Annotation Text"),
        ),
        "Allele rows linked to ClinPGx summary annotations.",
    ),
    RecordProfile(
        "clinpgx.summary_evidence.v1",
        "data/summaryAnnotations.zip",
        "summary_ann_evidence.tsv",
        "tabular_row",
        ShapeSelector("tabular"),
        (
            _field(
                "Summary Annotation ID",
                "Owning summary annotation identifier.",
                "Stable join identity.",
                required=True,
            ),
            _field(
                "Evidence ID", "Source evidence identifier.", "Stable row identity.", required=True
            ),
            _field("Evidence Type", "Source evidence type.", "Common evidence classification."),
            _field("Evidence URL", "Source evidence URL.", "Source evidence locator."),
            _field("PMID", "Source PubMed identifier.", "Common literature identity."),
            _field("Summary", "Source evidence summary.", "Common evidence detail."),
            _field("Score", "Source evidence score.", "Evidence qualification context."),
        ),
        _modes(
            ("Summary Annotation ID", "Evidence ID"),
            ("Summary Annotation ID", "Evidence ID", "Evidence Type", "PMID", "Score"),
            (
                "Summary Annotation ID",
                "Evidence ID",
                "Evidence Type",
                "PMID",
                "Score",
                "Evidence URL",
                "Summary",
            ),
        ),
        "Evidence rows linked to ClinPGx summary annotations.",
    ),
    RecordProfile(
        "clinpgx.relationship.v1",
        "data/relationships.zip",
        "relationships.tsv",
        "tabular_row",
        ShapeSelector("tabular"),
        (
            _field(
                "Entity1_id",
                "First source entity identifier.",
                "Stable endpoint identity.",
                required=True,
            ),
            _field("Entity1_name", "First source entity name.", "Human-readable endpoint context."),
            _field(
                "Entity1_type",
                "First source entity type.",
                "Required endpoint classification.",
                required=True,
            ),
            _field(
                "Entity2_id",
                "Second source entity identifier.",
                "Stable endpoint identity.",
                required=True,
            ),
            _field(
                "Entity2_name", "Second source entity name.", "Human-readable endpoint context."
            ),
            _field(
                "Entity2_type",
                "Second source entity type.",
                "Required endpoint classification.",
                required=True,
            ),
            _field("Evidence", "Source evidence summary.", "Common relationship evidence."),
            _field("Association", "Source association label.", "Common relationship evidence."),
            _field("PK", "Source pharmacokinetic flag.", "Approved relationship evidence."),
            _field("PD", "Source pharmacodynamic flag.", "Approved relationship evidence."),
            _field("PMIDs", "Source PubMed identifiers.", "Literature evidence links."),
        ),
        _modes(
            ("Entity1_id", "Entity1_type", "Entity2_id", "Entity2_type"),
            (
                "Entity1_id",
                "Entity1_type",
                "Entity1_name",
                "Entity2_id",
                "Entity2_type",
                "Entity2_name",
                "Association",
                "Evidence",
            ),
            (
                "Entity1_id",
                "Entity1_type",
                "Entity1_name",
                "Entity2_id",
                "Entity2_type",
                "Entity2_name",
                "Association",
                "Evidence",
                "PK",
                "PD",
                "PMIDs",
            ),
        ),
        "ClinPGx relationship rows with published endpoint direction.",
    ),
    *(
        RecordProfile(
            profile_id,
            "data/clinpgxHaplotypes.zip",
            member,
            "tabular_row",
            ShapeSelector("tabular"),
            _HAPLOTYPE_FIELDS,
            _HAPLOTYPE_MODES,
            "ClinPGx haplotype and named-allele rows.",
        )
        for profile_id, member in (
            ("clinpgx.haplotype.v1", "clinpgx_haplotypes.tsv"),
            (
                "clinpgx.haplotype.named_alleles.v1",
                "clinpgxHaplotypes_named_alleles.tsv",
            ),
            (
                "clinpgx.haplotype.star_alleles.v1",
                "clinpgxHaplotypes_star_alleles.tsv",
            ),
        )
    ),
    RecordProfile(
        "pharmcat.diplotype.v1",
        "data/pharmcat.zip",
        "phenotypes.json",
        "diplotype",
        ShapeSelector(
            "json_pointer",
            r"/[0-9]+/diplotypes/[0-9]+",
            "Objects at a top-level gene entry's diplotypes array.",
        ),
        (
            _field(
                "diplotype", "Source diplotype label.", "Stable source row subject.", required=True
            ),
            _field(
                "diplotypekey",
                "Source allele-count map.",
                "Exact structured diplotype evidence.",
                required=True,
            ),
            _field(
                "generesult", "Source gene-result label.", "Core result evidence.", required=True
            ),
            _field(
                "lookupkey",
                "Source result lookup key.",
                "Required source shape identity.",
                required=True,
            ),
            _field(
                "phenotype", "Source phenotype label.", "Core phenotype evidence.", required=True
            ),
            _field(
                "activityScore",
                "Optional source activity score.",
                "Additional result evidence when supplied.",
            ),
        ),
        _modes(
            ("diplotype", "lookupkey"),
            ("diplotype", "lookupkey", "generesult", "phenotype"),
            (
                "diplotype",
                "diplotypekey",
                "generesult",
                "lookupkey",
                "phenotype",
                "activityScore",
            ),
        ),
        "PharmCAT diplotype-to-result evidence rows; no phenotype inference is performed.",
    ),
)

_PROFILES_BY_KEY = {
    (profile.dataset_id, profile.member, profile.shape_id): profile for profile in RECORD_PROFILES
}
if len(_PROFILES_BY_KEY) != len(RECORD_PROFILES):
    raise RuntimeError("Record profile keys must be unique")
_PROFILES_BY_MEMBER = {
    key: tuple(
        profile for profile in RECORD_PROFILES if (profile.dataset_id, profile.member) == key
    )
    for key in dict.fromkeys((profile.dataset_id, profile.member) for profile in RECORD_PROFILES)
}


def profile_for_row(dataset_id: str, member: str, json_pointer: str | None) -> RecordProfile | None:
    matches = tuple(
        profile
        for profile in _PROFILES_BY_MEMBER.get((dataset_id, member), ())
        if profile.selector.matches(json_pointer)
    )
    return matches[0] if len(matches) == 1 else None


def profile_for_shape(dataset_id: str, member: str, shape_id: str) -> RecordProfile | None:
    """Look up one declaration by its full stable shape key."""
    return _PROFILES_BY_KEY.get((dataset_id, member, shape_id))


def profiles_for_member(dataset_id: str, member: str) -> tuple[RecordProfile, ...]:
    return _PROFILES_BY_MEMBER.get((dataset_id, member), ())


def _mode_value(policy: ModeFieldPolicy) -> dict[str, object]:
    return {
        "fields": list(policy.fields),
        "include_all_reachable": policy.include_all_reachable,
    }


def profile_declaration(profile: RecordProfile) -> dict[str, object]:
    """Return bounded public metadata containing code-owned prose only."""
    selector: dict[str, object] = {
        "kind": profile.selector.kind,
        "description": profile.selector.description,
    }
    if profile.selector.pointer_pattern is not None:
        selector["pointer_pattern"] = profile.selector.pointer_pattern
    return {
        "profile_id": profile.profile_id,
        "shape_id": profile.shape_id,
        "description": profile.description,
        "selector": selector,
        "required_fields": list(profile.required_fields),
        "optional_fields": list(profile.optional_fields),
        "fields": [
            {
                "name": field.name,
                "description": field.description,
                "inclusion_reason": field.inclusion_reason,
                "required": field.required,
            }
            for field in profile.fields
        ],
        "modes": {
            "minimal": _mode_value(profile.modes.minimal),
            "compact": _mode_value(profile.modes.compact),
            "standard": _mode_value(profile.modes.standard),
            "full": _mode_value(profile.modes.full),
        },
    }


def _definition_projection() -> list[dict[str, object]]:
    return [
        {
            "dataset_id": profile.dataset_id,
            "member": profile.member,
            **profile_declaration(profile),
        }
        for profile in RECORD_PROFILES
    ]


_DEFINITION_JSON = json.dumps(
    _definition_projection(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
)
PROFILE_DEFINITION_DIGEST = "sha256:" + hashlib.sha256(_DEFINITION_JSON.encode()).hexdigest()


__all__ = [
    "PROFILE_DEFINITION_DIGEST",
    "RECORD_PROFILES",
    "FieldDeclaration",
    "ModeFieldPolicy",
    "ProfileModes",
    "RecordProfile",
    "ShapeSelector",
    "profile_declaration",
    "profile_for_row",
    "profile_for_shape",
    "profiles_for_member",
]
