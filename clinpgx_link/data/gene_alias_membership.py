"""Candidate-bound compatibility receipt for source-defined gene aliases."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, cast

GENE_ALIAS_DATASET = "data/genes.zip"
GENE_ALIAS_MEMBER = "genes.tsv"
GENE_ALIAS_DECLARATIONS = (
    ("Alternate Names", "name", "csv"),
    ("Alternate Symbols", "gene", "csv"),
)
GENE_ALIAS_FIELDS = frozenset(item[0] for item in GENE_ALIAS_DECLARATIONS)
GENE_ALIAS_RECEIPT_SCHEMA_VERSION = 1
GENE_ALIAS_TOKENIZATION_CONTRACT_VERSION = "literal-comma-split-strip-nonempty-v1"
GENE_ALIAS_MEMBERSHIP_LIMITATION = (
    "Gene alias member search is unavailable for this installed snapshot. Rebuild and install "
    "a compatible immutable snapshot; raw records and exact source-field search remain available."
)
_MAX_RECEIPT_BYTES = 16_384
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "snapshot_id",
        "profile_definition_digest",
        "tokenization_contract_version",
    }
)
_PROFILE_DEFINITION = {
    "fields": [
        {
            "dataset_id": GENE_ALIAS_DATASET,
            "member": GENE_ALIAS_MEMBER,
            "semantic_target": semantic_target,
            "source_field": source_field,
            "tokenizer": tokenizer,
        }
        for source_field, semantic_target, tokenizer in GENE_ALIAS_DECLARATIONS
    ],
    "tokenization_contract_version": GENE_ALIAS_TOKENIZATION_CONTRACT_VERSION,
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


GENE_ALIAS_PROFILE_DEFINITION_DIGEST = (
    "sha256:"
    + hashlib.sha256((_canonical_json(_PROFILE_DEFINITION) + "\n").encode("utf-8")).hexdigest()
)


@dataclass(frozen=True)
class LoadedGeneAliasMembership:
    status: Literal["compatible", "unknown", "mismatched"]

    @property
    def compatible(self) -> bool:
        return self.status == "compatible"


def gene_alias_membership_receipt(snapshot_id: str) -> dict[str, object]:
    """Return the closed build receipt for the declared alias membership semantics."""
    return {
        "schema_version": GENE_ALIAS_RECEIPT_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "profile_definition_digest": GENE_ALIAS_PROFILE_DEFINITION_DIGEST,
        "tokenization_contract_version": GENE_ALIAS_TOKENIZATION_CONTRACT_VERSION,
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def load_gene_alias_membership(
    raw: str | None, expected_snapshot: str
) -> LoadedGeneAliasMembership:
    """Strictly load a bounded receipt without reflecting malformed candidate input."""
    if raw is None:
        return LoadedGeneAliasMembership("unknown")
    if type(raw) is not str or len(raw.encode("utf-8")) > _MAX_RECEIPT_BYTES:
        return LoadedGeneAliasMembership("mismatched")
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, TypeError, UnicodeError, ValueError):
        return LoadedGeneAliasMembership("mismatched")
    if not isinstance(value, dict) or frozenset(value) != _RECEIPT_KEYS:
        return LoadedGeneAliasMembership("mismatched")
    receipt = cast(dict[str, object], value)
    compatible = (
        type(receipt["schema_version"]) is int
        and receipt["schema_version"] == GENE_ALIAS_RECEIPT_SCHEMA_VERSION
        and type(receipt["snapshot_id"]) is str
        and receipt["snapshot_id"] == expected_snapshot
        and type(receipt["profile_definition_digest"]) is str
        and receipt["profile_definition_digest"] == GENE_ALIAS_PROFILE_DEFINITION_DIGEST
        and type(receipt["tokenization_contract_version"]) is str
        and receipt["tokenization_contract_version"] == GENE_ALIAS_TOKENIZATION_CONTRACT_VERSION
    )
    return LoadedGeneAliasMembership("compatible" if compatible else "mismatched")


__all__ = [
    "GENE_ALIAS_DATASET",
    "GENE_ALIAS_DECLARATIONS",
    "GENE_ALIAS_FIELDS",
    "GENE_ALIAS_MEMBER",
    "GENE_ALIAS_MEMBERSHIP_LIMITATION",
    "GENE_ALIAS_PROFILE_DEFINITION_DIGEST",
    "GENE_ALIAS_RECEIPT_SCHEMA_VERSION",
    "GENE_ALIAS_TOKENIZATION_CONTRACT_VERSION",
    "LoadedGeneAliasMembership",
    "gene_alias_membership_receipt",
    "load_gene_alias_membership",
]
