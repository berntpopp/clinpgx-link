"""Closed executable contracts for entity discovery across source types."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, NamedTuple

from clinpgx_link.data.search_diagnostics import CANONICAL_FILTER_PRIORITY
from clinpgx_link.exceptions import InvalidInputError

SearchSource = Literal["api"]


class SearchRoute(NamedTuple):
    selected_source: str
    api_filters: dict[str, str] | None


@dataclass(frozen=True)
class SearchContract:
    """One developer-authored API search contract."""

    entity_type: str
    source: SearchSource
    operation: str
    filter_mapping: tuple[tuple[str, str], ...]
    example_filters: tuple[tuple[str, str], ...]
    filter_values: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # Upstream field a ClinPGx PA accession is routed to instead of the name/symbol field.
    accession_mapping: tuple[tuple[str, str], ...] = ()
    semantics: str = "All supplied canonical exact filters are combined with AND semantics."

    @property
    def filters(self) -> tuple[str, ...]:
        return tuple(sorted(canonical for canonical, _ in self.filter_mapping))

    def translation(self) -> dict[str, str]:
        return dict(self.filter_mapping)

    def value_choices(self) -> dict[str, tuple[str, ...]]:
        return dict(self.filter_values)

    def accession_fields(self) -> dict[str, str]:
        return dict(self.accession_mapping)

    def example(self) -> dict[str, Any]:
        return {
            "tool": "search_records",
            "arguments": {
                "entity_type": self.entity_type,
                "filters": dict(self.example_filters),
                "source": self.source,
            },
        }


_CONTRACT_ROWS = (
    SearchContract(
        "pathway",
        "api",
        "GET /data/pathway",
        (("id", "accessionId"),),
        (("id", "PA154424674"),),
    ),
    SearchContract(
        "gene",
        "api",
        "GET /data/gene",
        (("id", "accessionId"), ("gene", "symbol")),
        (("gene", "CYP2C19"),),
        accession_mapping=(("gene", "accessionId"),),
    ),
    SearchContract(
        "chemical",
        "api",
        "GET /data/chemical",
        (("id", "accessionId"), ("chemical", "name"), ("name", "name")),
        (("chemical", "clopidogrel"),),
        accession_mapping=(("chemical", "accessionId"), ("name", "accessionId")),
    ),
    SearchContract(
        "disease",
        "api",
        "GET /data/disease",
        (("id", "accessionId"),),
        (("id", "PA999999"),),
    ),
    SearchContract(
        "variant",
        "api",
        "GET /data/variant/",
        (("variant", "symbol"),),
        (("variant", "rs4244285"),),
    ),
    SearchContract(
        "literature",
        "api",
        "GET /data/literature",
        (("id", "id"), ("resource_id", "resourceId")),
        (("id", "15178564"),),
    ),
    SearchContract(
        "guideline_annotation",
        "api",
        "GET /data/guidelineAnnotation",
        (
            ("source", "source"),
            ("gene", "relatedGenes.accessionId"),
            ("chemical", "relatedChemicals.accessionId"),
        ),
        (("source", "CPIC"),),
        (("source", ("cpic", "dpwg", "pro")),),
    ),
    SearchContract(
        "label",
        "api",
        "GET /data/label",
        (
            ("source", "source"),
            ("gene", "relatedGenes.symbol"),
            ("chemical", "relatedChemicals.name"),
        ),
        (("gene", "DPYD"), ("chemical", "fluorouracil")),
        (("source", ("ema", "fda", "hcsc", "pmda")),),
        accession_mapping=(
            ("gene", "relatedGenes.accessionId"),
            ("chemical", "relatedChemicals.accessionId"),
        ),
    ),
    SearchContract(
        "summary_annotation",
        "api",
        "GET /data/summaryAnnotation",
        (
            ("id", "id"),
            ("annotation_id", "id"),
            ("gene", "location.genes.symbol"),
            ("chemical", "relatedChemicals.name"),
            ("variant", "location.fingerprint"),
            ("level_of_evidence", "levelOfEvidence.term"),
        ),
        (("gene", "CYP2C19"), ("chemical", "clopidogrel")),
        (("level_of_evidence", ("1A", "1B", "2A", "2B", "3", "4")),),
        accession_mapping=(("chemical", "relatedChemicals.accessionId"),),
    ),
    SearchContract(
        "variant_annotation",
        "api",
        "GET /data/variantAnnotation",
        (("gene", "location.genes.symbol"), ("variant", "location.fingerprint")),
        (("variant", "rs4149056"),),
    ),
)

API_SEARCH_CONTRACTS: Mapping[str, SearchContract] = MappingProxyType(
    {contract.entity_type: contract for contract in _CONTRACT_ROWS}
)
LOCAL_SEARCH_ENTITIES = frozenset(
    {"allele", "annotation_id", "chemical", "disease", "gene", "literature", "variant"}
)
QUERY_UNSUPPORTED_ENTITIES = frozenset(API_SEARCH_CONTRACTS) - LOCAL_SEARCH_ENTITIES
QUERY_DESCRIPTION = (
    "Literal AND-token local text query; unavailable for API searches. Rejected "
    f"(invalid_input) for {', '.join(sorted(QUERY_UNSUPPORTED_ENTITIES))}: use filters."
)
DOWNLOAD_FILTERS = tuple(sorted(CANONICAL_FILTER_PRIORITY))
CANONICAL_FILTERS = tuple(
    sorted(
        {"name"}
        | {
            canonical
            for contract in _CONTRACT_ROWS
            for canonical, _upstream in contract.filter_mapping
        }
    )
)
DOWNLOAD_SEMANTICS = (
    "Local search returns installed dataset memberships; entity_type is a discovery lens, "
    "not a claim of row ownership or identity. Query tokens and filters are combined with AND."
)
FILTER_DESCRIPTION = (
    "ANDed exact filters; keys depend on entity_type. API keys: "
    + "; ".join(f"{c.entity_type}: {'|'.join(c.filters)}" for c in _CONTRACT_ROWS)
    + ". A PA accession is accepted by gene/chemical/name on gene, chemical and label and by "
    "chemical on summary_annotation; guideline_annotation gene/chemical take PA accessions "
    "only; other name/symbol keys reject PA values. level_of_evidence: 1A|1B|2A|2B|3|4. "
    "Download keys: " + "|".join(DOWNLOAD_FILTERS) + "."
)
_API_CONTRACT_DEFAULT_FIELDS = ("source", "semantics", "example_purpose")
_API_CONTRACT_DEFAULTS_SCOPE = "Every API row unless that row overrides the field."


_ACCESSION = re.compile(r"PA[0-9]+")
_NAME_FIELDS = frozenset({"name", "symbol", "fingerprint"})


def _route_value_form(canonical: str, upstream: str, value: str, contract: SearchContract) -> str:
    """Pick the upstream field that can match this value form, never a silently empty one."""
    is_accession = _ACCESSION.fullmatch(value) is not None
    if is_accession and canonical in contract.accession_fields():
        return contract.accession_fields()[canonical]
    leaf = upstream.rsplit(".", 1)[-1]
    if is_accession and leaf in _NAME_FIELDS:
        raise InvalidInputError(
            "This filter matches names or symbols, not ClinPGx PA accessions.",
            field="filters",
            subtype="accession_in_name_filter",
        )
    if not is_accession and leaf == "accessionId":
        raise InvalidInputError(
            "This filter requires a ClinPGx PA accession.",
            field="filters",
            subtype="accession_required",
        )
    return upstream


def api_contract(entity_type: str) -> SearchContract | None:
    """Return the closed API contract, if this entity has one."""
    return API_SEARCH_CONTRACTS.get(entity_type)


def api_filters(entity_type: str, filters: dict[str, str]) -> dict[str, str] | None:
    """Translate a complete canonical predicate without dropping aliases."""
    contract = api_contract(entity_type)
    if contract is None or not filters or not set(filters) <= set(contract.filters):
        return None
    mapping = contract.translation()
    value_choices = contract.value_choices()
    translated: dict[str, str] = {}
    for canonical, value in filters.items():
        upstream = mapping[canonical]
        allowed_values = value_choices.get(canonical)
        if allowed_values is not None:
            # Send the declared spelling: upstream enum matching is case-sensitive.
            normalized = {item.lower(): item for item in allowed_values}.get(value.lower())
            if normalized is None:
                raise InvalidInputError(
                    "The filter value is outside the entity API contract.",
                    field="filters",
                    subtype="unsupported_api_filter_value",
                )
            value = normalized
        else:
            upstream = _route_value_form(canonical, upstream, value, contract)
        previous = translated.get(upstream)
        if previous is not None and previous != value:
            raise InvalidInputError(
                "Aliases for one API predicate must carry the same value.",
                field="filters",
                subtype="conflicting_api_filters",
            )
        translated[upstream] = value
    return translated


def api_filter_choices(entity_type: str) -> list[str]:
    """List canonical API selectors for fixed recovery guidance."""
    contract = api_contract(entity_type)
    return list(contract.filters) if contract is not None else []


def api_filter_value_choices(entity_type: str) -> dict[str, list[str]]:
    """Return route-scoped accepted enum spellings for recovery."""
    contract = api_contract(entity_type)
    if contract is None:
        return {}
    return {key: list(values) for key, values in contract.value_choices().items()}


def supports_api_search(entity_type: str) -> bool:
    return entity_type in API_SEARCH_CONTRACTS


def supports_accession_shortcut(entity_type: str) -> bool:
    """Only accessionId-backed searches may reuse a strict PA detail identifier."""
    contract = api_contract(entity_type)
    return contract is not None and contract.translation().get("id") == "accessionId"


def resolve_search_route(
    entity_type: str,
    source: str,
    query: str | None,
    filters: dict[str, str],
) -> SearchRoute:
    """Choose a source only when the complete predicate has declared semantics."""
    if query is not None and entity_type in QUERY_UNSUPPORTED_ENTITIES:
        # Indexed nowhere for text: the API takes exact filters only and the snapshot lacks it.
        raise InvalidInputError(
            "This entity type does not support free-text query.",
            field="query",
            subtype="query_unsupported",
        )
    translated = None if query is not None else api_filters(entity_type, filters)
    contract = api_contract(entity_type)
    if source == "auto":
        if translated is not None:
            return SearchRoute("api", translated)
        if query is None and contract is not None and entity_type not in LOCAL_SEARCH_ENTITIES:
            raise InvalidInputError(
                "The entity API route does not support these canonical filters.",
                field="filters",
                subtype="unsupported_api_filters",
            )
        return SearchRoute("download", None)
    if source == "api":
        if query is not None or contract is None:
            raise InvalidInputError(
                "This request has no supported API search contract.",
                field="query" if query is not None else "entity_type",
                subtype="unsupported_search_source",
            )
        if translated is None:
            raise InvalidInputError(
                "The entity API route does not support these canonical filters.",
                field="filters",
                subtype="unsupported_api_filters",
            )
    return SearchRoute(source, translated)


def capabilities_payload() -> dict[str, Any]:
    """Return the complete canonical search-capabilities contract."""
    return {
        "api": {
            entity: {
                "source": contract.source,
                "operation": contract.operation,
                "filters": list(contract.filters),
                "filter_mapping": contract.translation(),
                "filter_values": api_filter_value_choices(entity),
                "accession_filter_mapping": contract.accession_fields(),
                "semantics": contract.semantics,
                "example": contract.example(),
                "example_purpose": "Request syntax and route binding; not evidence of a match.",
            }
            for entity, contract in API_SEARCH_CONTRACTS.items()
        },
        "download": {
            "entities": sorted(LOCAL_SEARCH_ENTITIES),
            "filters": list(DOWNLOAD_FILTERS),
            "semantics": DOWNLOAD_SEMANTICS,
            "installed_dataset_examples": {
                "gene": {
                    "dataset_id": "data/genes.zip",
                    "member": "genes.tsv",
                    "canonical_filter": "gene",
                    "source_column": "Symbol",
                    "example_call": {
                        "tool": "search_dataset",
                        "arguments": {
                            "dataset_id": "data/genes.zip",
                            "filters": {"gene": "DPYD"},
                        },
                    },
                },
                "chemical": {
                    "dataset_id": "data/chemicals.zip",
                    "member": "chemicals.tsv",
                    "canonical_filter": "chemical",
                    "source_column": "Name",
                    "example_call": {
                        "tool": "search_dataset",
                        "arguments": {
                            "dataset_id": "data/chemicals.zip",
                            "filters": {"chemical": "warfarin"},
                        },
                    },
                },
            },
        },
    }


def compact_capabilities_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Factor only uniform API-row defaults into a defensive payload copy."""
    projected = deepcopy(dict(payload))
    api = projected.get("api")
    if not isinstance(api, dict) or not api:
        return projected

    rows = list(api.values())
    defaults: dict[str, Any] = {}
    for field in _API_CONTRACT_DEFAULT_FIELDS:
        if not all(isinstance(row, dict) and field in row for row in rows):
            continue
        first = rows[0][field]
        if all(row[field] == first for row in rows[1:]):
            defaults[field] = deepcopy(first)

    if defaults:
        projected["api_contract_defaults"] = {
            "applies_to": _API_CONTRACT_DEFAULTS_SCOPE,
            **defaults,
        }
        for row in rows:
            for field in defaults:
                del row[field]
    return projected


__all__ = [
    "API_SEARCH_CONTRACTS",
    "CANONICAL_FILTERS",
    "DOWNLOAD_FILTERS",
    "DOWNLOAD_SEMANTICS",
    "FILTER_DESCRIPTION",
    "LOCAL_SEARCH_ENTITIES",
    "QUERY_DESCRIPTION",
    "QUERY_UNSUPPORTED_ENTITIES",
    "SearchContract",
    "SearchRoute",
    "api_contract",
    "api_filter_choices",
    "api_filter_value_choices",
    "api_filters",
    "capabilities_payload",
    "compact_capabilities_payload",
    "resolve_search_route",
    "supports_accession_shortcut",
    "supports_api_search",
]
