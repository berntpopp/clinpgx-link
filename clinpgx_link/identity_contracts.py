"""Captured numeric detail identifiers and family-specific source profiles."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

FieldKind = Literal["integer", "string", "object", "array", "boolean", "cross_references"]


@dataclass(frozen=True, slots=True)
class NumericIdentityContract:
    """One captured integer-id schema plus its supported discovery contract."""

    entity_type: str
    operation: str
    minimal: tuple[str, ...]
    compact: tuple[str, ...]
    standard: tuple[str, ...]
    required_types: tuple[tuple[str, FieldKind], ...]
    optional_types: tuple[tuple[str, FieldKind], ...]
    external_reference_note: str | None = None

    def source_value_is_valid(self, value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        for name, kind in self.required_types:
            if name not in value or not _field_matches(value[name], kind):
                return False
        valid_optional = all(
            name not in value or _field_matches(value[name], kind)
            for name, kind in self.optional_types
        )
        if self.entity_type == "literature":
            return valid_optional and (
                "resourceId" in value
                or bool(isinstance(value.get("crossReferences"), list) and value["crossReferences"])
            )
        return valid_optional

    def capability(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operation": self.operation,
            "source_field": "id",
            "role": "internal_numeric_detail_id",
            "value_type": "integer",
            "tool_argument": "record_id",
            "argument_encoding": "decimal_string",
        }
        if self.entity_type == "variant_annotation":
            payload["optional_accession"] = {
                "source_field": "accessionId",
                "required": False,
                "role": "external_cross_reference",
                "detail_identifier": False,
            }
        if self.entity_type == "literature":
            payload["external_cross_references"] = [
                {
                    "source_field": field,
                    "role": "external_cross_reference",
                    "detail_identifier": False,
                }
                for field in ("resourceId", "crossReferences[].resourceId")
            ]
        if self.external_reference_note is not None:
            payload["external_reference_note"] = self.external_reference_note
        return payload


def _field_matches(value: Any, kind: FieldKind) -> bool:
    if kind == "integer":
        return type(value) is int and 0 <= value <= 2_147_483_647
    if kind == "cross_references":
        return isinstance(value, list) and all(_cross_reference_is_valid(item) for item in value)
    expected = {"string": str, "object": dict, "array": list, "boolean": bool}[kind]
    return isinstance(value, expected)


def _cross_reference_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or not set(value) <= {"id", "resource", "resourceId", "_url"}:
        return False
    if set(value) < {"id", "resource", "resourceId"}:
        return False
    return (
        _field_matches(value["id"], "integer")
        and _field_matches(value["resource"], "string")
        and _field_matches(value["resourceId"], "string")
        and ("_url" not in value or _field_matches(value["_url"], "string"))
    )


_ROWS = (
    NumericIdentityContract(
        "literature",
        "GET /data/literature/{id}",
        ("id",),
        ("id", "resourceId", "title", "type", "crossReferences"),
        (
            "id",
            "resourceId",
            "title",
            "type",
            "crossReferences",
            "authors",
            "journal",
            "pubDate",
            "year",
        ),
        (("id", "integer"), ("title", "string"), ("type", "string")),
        (
            ("resourceId", "string"),
            ("crossReferences", "cross_references"),
            ("authors", "array"),
            ("journal", "string"),
            ("pubDate", "string"),
            ("year", "integer"),
        ),
        "PubMed resourceId is not the internal ClinPGx literature id.",
    ),
    NumericIdentityContract(
        "summary_annotation",
        "GET /data/summaryAnnotation/{id}",
        ("id",),
        ("id", "levelOfEvidence", "relatedChemicals"),
        ("id", "levelOfEvidence", "location", "relatedChemicals", "allelePhenotypes"),
        (("id", "integer"),),
        (
            ("levelOfEvidence", "object"),
            ("location", "object"),
            ("relatedChemicals", "array"),
            ("allelePhenotypes", "object"),
        ),
    ),
    NumericIdentityContract(
        "variant_annotation",
        "GET /data/variantAnnotation/{id}",
        ("id",),
        ("id", "accessionId", "objCls", "sentence", "literature"),
        (
            "id",
            "accessionId",
            "objCls",
            "sentence",
            "literature",
            "location",
            "relatedChemicals",
            "alleleGenotype",
            "comparison",
            "isAssociated",
        ),
        (("id", "integer"),),
        (
            ("accessionId", "string"),
            ("objCls", "string"),
            ("sentence", "string"),
            ("literature", "object"),
            ("location", "object"),
            ("relatedChemicals", "array"),
            ("alleleGenotype", "string"),
            ("comparison", "string"),
            ("isAssociated", "boolean"),
        ),
    ),
)

NUMERIC_IDENTITY_CONTRACTS: Mapping[str, NumericIdentityContract] = MappingProxyType(
    {contract.entity_type: contract for contract in _ROWS}
)


def numeric_identity_contract(entity_type: str | None) -> NumericIdentityContract | None:
    return NUMERIC_IDENTITY_CONTRACTS.get(entity_type or "")


def numeric_detail_argument_is_valid(entity_type: str, value: str) -> bool:
    """Match the registry's request-side ASCII decimal path contract."""
    return (
        entity_type in NUMERIC_IDENTITY_CONTRACTS
        and isinstance(value, str)
        and value.isascii()
        and value.isdecimal()
        and len(value) <= 32
    )


def detail_identifier_capabilities(
    search_filters: Callable[[str], list[str]],
) -> dict[str, Any]:
    return {
        entity: {**contract.capability(), "search_filters": search_filters(entity)}
        for entity, contract in NUMERIC_IDENTITY_CONTRACTS.items()
    }


def detail_identity_metadata(
    value: Any, profile_name: str | None, selectors: dict[str, Any]
) -> dict[str, Any]:
    """Return code-owned metadata only for a schema-valid numeric source row."""
    contract = numeric_identity_contract(profile_name)
    if contract is None or not contract.source_value_is_valid(value):
        return {}
    identifier = value["id"]
    metadata: dict[str, Any] = {
        "id": identifier,
        "detail_identifier": {
            "source_field": "id",
            "role": "internal_numeric_detail_id",
            "value": identifier,
        },
    }
    if (
        selectors.get("tool") == "search_records"
        and selectors.get("entity_type") == contract.entity_type
        and selectors.get("selected_source") == "api"
        and selectors.get("view") in {"min", "base", "max"}
    ):
        metadata["next_commands"] = [
            {
                "tool": "get_record",
                "arguments": {
                    "entity_type": contract.entity_type,
                    "record_id": str(identifier),
                    "source": "api",
                    "view": selectors["view"],
                },
            }
        ]
    return metadata


__all__ = [
    "NUMERIC_IDENTITY_CONTRACTS",
    "NumericIdentityContract",
    "detail_identifier_capabilities",
    "detail_identity_metadata",
    "numeric_detail_argument_is_valid",
    "numeric_identity_contract",
]
