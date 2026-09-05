"""Closed executable contracts for entity discovery across source types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, NamedTuple

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
    semantics: str = "All supplied canonical exact filters are combined with AND semantics."

    @property
    def filters(self) -> tuple[str, ...]:
        return tuple(sorted(canonical for canonical, _ in self.filter_mapping))

    def translation(self) -> dict[str, str]:
        return dict(self.filter_mapping)

    def value_choices(self) -> dict[str, tuple[str, ...]]:
        return dict(self.filter_values)

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
    ),
    SearchContract(
        "chemical",
        "api",
        "GET /data/chemical",
        (("id", "accessionId"), ("chemical", "name")),
        (("chemical", "clopidogrel"),),
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
        (("id", "id"),),
        (("id", "15178564"),),
    ),
    SearchContract(
        "guideline_annotation",
        "api",
        "GET /data/guidelineAnnotation",
        (("source", "source"),),
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
        ),
        (("gene", "CYP2C19"), ("chemical", "clopidogrel")),
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
    "ANDed canonical filters (" + ", ".join(CANONICAL_FILTERS) + "); API choices vary by entity."
)


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
            normalized = value.lower()
            if normalized not in allowed_values:
                raise InvalidInputError(
                    "The filter value is outside the entity API contract.",
                    field="filters",
                    subtype="unsupported_api_filter_value",
                )
            value = normalized
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
    """Return a defensive, bounded projection for server capabilities."""
    return {
        "api": {
            entity: {
                "source": contract.source,
                "operation": contract.operation,
                "filters": list(contract.filters),
                "filter_mapping": contract.translation(),
                "filter_values": api_filter_value_choices(entity),
                "semantics": contract.semantics,
                "example": contract.example(),
                "example_purpose": "Request syntax and route binding; not evidence of a match.",
            }
            for entity, contract in API_SEARCH_CONTRACTS.items()
        },
        "download": {
            "entities": sorted(LOCAL_SEARCH_ENTITIES),
            "filters": list(CANONICAL_FILTERS),
            "semantics": DOWNLOAD_SEMANTICS,
        },
    }


__all__ = [
    "API_SEARCH_CONTRACTS",
    "CANONICAL_FILTERS",
    "DOWNLOAD_SEMANTICS",
    "FILTER_DESCRIPTION",
    "LOCAL_SEARCH_ENTITIES",
    "SearchContract",
    "SearchRoute",
    "api_contract",
    "api_filter_choices",
    "api_filter_value_choices",
    "api_filters",
    "capabilities_payload",
    "resolve_search_route",
    "supports_accession_shortcut",
    "supports_api_search",
]
