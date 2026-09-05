"""Closed, developer-authored recovery plans for entity-tool failures."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from clinpgx_link.exceptions import InvalidInputError

RecoveryKind = Literal[
    "invalid_search_filters",
    "record_not_found",
    "unsupported_detail_source",
    "unsupported_related_mode",
    "unsupported_search_source",
    "variant_symbol_requires_search",
]
_KINDS = frozenset(
    {
        "invalid_search_filters",
        "record_not_found",
        "unsupported_detail_source",
        "unsupported_related_mode",
        "unsupported_search_source",
        "variant_symbol_requires_search",
    }
)

_SAFE_ID = re.compile(r"(?:PA|rs)[0-9]+|record:[0-9a-f]{64}")
_ENTITIES = frozenset(
    {
        "allele",
        "annotation_id",
        "chemical",
        "connection",
        "data_annotation",
        "disease",
        "gene",
        "guideline",
        "guideline_annotation",
        "haplotype",
        "label",
        "literature",
        "ontology_term",
        "pathway",
        "summary_annotation",
        "variant",
        "variant_annotation",
        "vip",
    }
)
_SOURCES = frozenset({"api", "auto", "download", "website"})
_VIEWS = frozenset({"min", "base", "max"})
_RESULT_TYPES = frozenset(
    {
        "allele",
        "evidence",
        "guideline_annotation",
        "label",
        "literature",
        "literature_annotation",
        "multilink_annotation",
        "pathway",
        "relationship",
        "summary_annotation",
        "variant_annotation",
        "vip",
        "vip_variant",
    }
)
_OBJECT_TYPES = frozenset({"Gene", "Chemical", "Disease", "Variant"})
_FILTERS = ["annotation_id", "chemical", "gene", "id", "name", "source", "variant"]
LOCAL_ENTITIES = frozenset(
    {"allele", "annotation_id", "chemical", "disease", "gene", "literature", "variant"}
)
WEBSITE_GET = {
    "allele": "GET /site/allele/{id}",
    "gene": "GET /site/gene/{id}",
    "guideline": "GET /site/guideline/{id}",
    "guideline_annotation": "GET /site/guidelineAnnotation/{id}",
    "haplotype": "GET /site/haplotype/{id}",
    "label": "GET /site/labelAnnotation/{id}",
    "pathway": "GET /site/pathway/{id}",
    "vip": "GET /site/vip/{id}",
}
API_RESULT = {
    "guideline_annotation": "guidelineAnnotation",
    "label": "label",
    "literature_annotation": "literatureAnnotation",
    "multilink_annotation": "multilinkAnnotation",
    "pathway": "pathway",
    "summary_annotation": "summaryAnnotation",
    "variant_annotation": "variantAnnotation",
    "vip": "vip",
    "vip_variant": "vipVariant",
}
_API_FILTER_MAPPING: dict[str, dict[str, str]] = {
    "pathway": {"id": "accessionId"},
    "gene": {"id": "accessionId", "gene": "symbol"},
    "chemical": {"id": "accessionId", "chemical": "name"},
    "disease": {"id": "accessionId"},
    "variant": {"variant": "symbol"},
    "literature": {"id": "id"},
    "guideline_annotation": {"source": "source"},
    "label": {
        "source": "source",
        "gene": "relatedGenes.symbol",
        "chemical": "relatedChemicals.name",
    },
    "summary_annotation": {
        "id": "id",
        "annotation_id": "id",
        "gene": "location.genes.symbol",
        "chemical": "relatedChemicals.name",
        "variant": "location.fingerprint",
    },
    "variant_annotation": {
        "gene": "location.genes.symbol",
        "variant": "location.fingerprint",
    },
}
_API_DETAIL = frozenset(
    {
        "chemical",
        "disease",
        "gene",
        "guideline_annotation",
        "label",
        "literature",
        "pathway",
        "summary_annotation",
        "variant",
        "variant_annotation",
    }
)
_WEBSITE_DETAIL = frozenset(
    {"allele", "gene", "guideline", "guideline_annotation", "haplotype", "label", "pathway", "vip"}
)
_DOWNLOAD_DETAIL = frozenset(
    {"allele", "annotation_id", "chemical", "disease", "gene", "literature", "variant"}
)
_API_ID_SEARCH = frozenset(
    {"chemical", "disease", "gene", "literature", "pathway", "summary_annotation"}
)
_API_SEARCH = frozenset(_API_FILTER_MAPPING)


def api_filters(entity_type: str, filters: dict[str, str]) -> dict[str, str] | None:
    """Translate canonical filters only when the API semantics are equivalent."""
    declared = _API_FILTER_MAPPING.get(entity_type)
    if declared is None or not filters or not set(filters) <= set(declared):
        return None
    return {declared[key]: value for key, value in filters.items()}


def validate_filters(filters: dict[str, str]) -> None:
    for key, value in filters.items():
        if key not in _FILTERS:
            raise InvalidInputError("Unknown canonical entity filter.", field="filters")
        if not isinstance(value, str) or not value:
            raise InvalidInputError("Entity filters must be nonempty strings.", field=key)


@dataclass(frozen=True)
class RecoveryPlan:
    """A closed plan token whose public payload is rendered from fixed text."""

    kind: RecoveryKind
    entity_type: str | None = None
    record_id: str | None = None
    source: str | None = None
    view: str = "base"
    result_type: str | None = None
    other_id: str | None = None
    other_type: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError("invalid recovery kind")
        if self.entity_type is not None and self.entity_type not in _ENTITIES:
            raise ValueError("invalid recovery entity")
        if self.record_id is not None and not safe_identifier(self.record_id):
            raise ValueError("unsafe recovery identifier")
        if self.other_id is not None and not safe_identifier(self.other_id):
            raise ValueError("unsafe recovery identifier")
        if self.source is not None and self.source not in _SOURCES:
            raise ValueError("invalid recovery source")
        if self.view not in _VIEWS:
            raise ValueError("invalid recovery view")
        if self.result_type is not None and self.result_type not in _RESULT_TYPES:
            raise ValueError("invalid recovery result type")
        if self.other_type is not None and self.other_type not in _OBJECT_TYPES:
            raise ValueError("invalid recovery object type")
        required = {
            "invalid_search_filters": (self.entity_type, self.source),
            "record_not_found": (self.entity_type, self.record_id, self.source),
            "unsupported_detail_source": (self.entity_type, self.record_id, self.source),
            "unsupported_related_mode": (
                self.record_id,
                self.result_type,
                self.other_type,
                self.source,
            ),
            "unsupported_search_source": (self.entity_type, self.source),
            "variant_symbol_requires_search": (self.entity_type, self.record_id, self.source),
        }
        if any(value is None for value in required[self.kind]):
            raise ValueError("recovery context is incomplete")
        if self.kind == "variant_symbol_requires_search" and (
            self.entity_type != "variant"
            or self.source != "api"
            or not str(self.record_id).startswith("rs")
        ):
            raise ValueError("variant recovery context is invalid")


def safe_identifier(value: str) -> bool:
    return isinstance(value, str) and len(value) <= 512 and _SAFE_ID.fullmatch(value) is not None


def variant_symbol_plan(record_id: str, view: str) -> RecoveryPlan:
    return RecoveryPlan(
        "variant_symbol_requires_search",
        entity_type="variant",
        record_id=record_id,
        source="api",
        view=view,
    )


def not_found_plan(entity_type: str, record_id: str, source: str, view: str) -> RecoveryPlan | None:
    if not safe_identifier(record_id):
        return None
    return RecoveryPlan(
        "record_not_found",
        entity_type=entity_type,
        record_id=record_id,
        source=source,
        view=view,
    )


def unsupported_detail_plan(
    entity_type: str, record_id: str, source: str, view: str
) -> RecoveryPlan | None:
    if not safe_identifier(record_id):
        return None
    return RecoveryPlan(
        "unsupported_detail_source",
        entity_type=entity_type,
        record_id=record_id,
        source=source,
        view=view,
    )


def invalid_filters_plan(entity_type: str, source: str, view: str) -> RecoveryPlan:
    return RecoveryPlan("invalid_search_filters", entity_type=entity_type, source=source, view=view)


def unsupported_search_plan(entity_type: str, source: str, view: str) -> RecoveryPlan:
    return RecoveryPlan(
        "unsupported_search_source", entity_type=entity_type, source=source, view=view
    )


def related_mode_plan(
    record_id: str,
    result_type: str,
    other_id: str | None,
    other_type: str,
    source: str,
    view: str,
) -> RecoveryPlan | None:
    if not safe_identifier(record_id) or (other_id is not None and not safe_identifier(other_id)):
        return None
    return RecoveryPlan(
        "unsupported_related_mode",
        record_id=record_id,
        result_type=result_type,
        other_id=other_id,
        other_type=other_type,
        source=source,
        view=view,
    )


def _detail_sources(entity_type: str) -> list[str]:
    return [
        source
        for source, entities in (
            ("api", _API_DETAIL),
            ("website", _WEBSITE_DETAIL),
            ("download", _DOWNLOAD_DETAIL),
        )
        if entity_type in entities
    ]


def detail_source_supported(entity_type: str, source: str) -> bool:
    return source in _detail_sources(entity_type)


def _command(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool not in {
        "get_record",
        "get_related_records",
        "get_server_capabilities",
        "search_records",
    }:
        raise ValueError("invalid recovery tool")
    return {"tool": tool, "arguments": arguments}


def recovery_payload(plan: RecoveryPlan) -> dict[str, Any]:
    """Render fixed prose and executable commands from a validated plan token."""
    if not isinstance(plan, RecoveryPlan):
        raise TypeError("recovery must be a validated plan")
    entity = plan.entity_type
    record_id = plan.record_id
    source = plan.source
    context = {
        key: value
        for key, value in (
            ("entity_type", entity),
            ("record_id", record_id),
            ("source", source),
        )
        if value is not None
    }
    choices: dict[str, list[str]] = {}
    commands: list[dict[str, Any]] = []

    if plan.kind == "variant_symbol_requires_search":
        limitation = (
            "An rs identifier is a variant symbol, not a detail-route accession; "
            "this validation failure is not an absence claim about the source."
        )
        commands.append(
            _command(
                "search_records",
                {
                    "entity_type": "variant",
                    "filters": {"variant": record_id},
                    "source": "api",
                    "view": plan.view,
                },
            )
        )
    elif plan.kind == "record_not_found":
        limitation = (
            "The requested source returned no exact detail record; discovery is a separate "
            "operation and does not prove absence from other sources."
        )
        if source == "api" and entity in _API_ID_SEARCH:
            commands.append(
                _command(
                    "search_records",
                    {
                        "entity_type": entity,
                        "filters": {"id": record_id},
                        "source": "api",
                        "view": plan.view,
                    },
                )
            )
        elif source == "download" and entity in _DOWNLOAD_DETAIL:
            commands.append(
                _command(
                    "search_records",
                    {"entity_type": entity, "filters": {"id": record_id}, "source": "download"},
                )
            )
        else:
            commands.append(_command("get_server_capabilities", {}))
    elif plan.kind == "unsupported_detail_source":
        limitation = "This entity has no verified detail route in the requested source."
        choices["source"] = _detail_sources(str(entity))
        if choices["source"]:
            alternative = choices["source"][0]
            commands.append(
                _command(
                    "get_record",
                    {
                        "entity_type": entity,
                        "record_id": record_id,
                        "source": alternative,
                        "view": plan.view,
                    },
                )
            )
    elif plan.kind == "invalid_search_filters":
        limitation = "One or more filter names are outside the canonical entity-search contract."
        choices["filters"] = _FILTERS
        if source in {"auto", "download"} and entity in _DOWNLOAD_DETAIL:
            commands.append(
                _command(
                    "search_records", {"entity_type": entity, "source": "download", "limit": 20}
                )
            )
        else:
            commands.append(_command("get_server_capabilities", {}))
    elif plan.kind == "unsupported_search_source":
        limitation = "This entity and source do not have a compatible search contract."
        choices["source"] = [
            item
            for item in ("api", "download")
            if (item == "api" and entity in _API_SEARCH)
            or (item == "download" and entity in _DOWNLOAD_DETAIL)
        ]
        if entity in _DOWNLOAD_DETAIL:
            commands.append(
                _command(
                    "search_records", {"entity_type": entity, "source": "download", "limit": 20}
                )
            )
        else:
            commands.append(_command("get_server_capabilities", {}))
    else:
        limitation = (
            "Connected-object mode omits other_id and uses relationship; pair mode requires "
            "other_id and a documented pair result type."
        )
        choices["mode"] = ["connected_object", "pair"]
        commands.append(
            _command(
                "get_related_records",
                {
                    "record_id": record_id,
                    "result_type": "relationship",
                    "other_type": plan.other_type,
                    "source": source,
                    "view": plan.view,
                },
            )
        )
    if not commands:
        commands.append(_command("get_server_capabilities", {}))
    return {
        "action": plan.kind,
        "limitation": limitation,
        "context": context,
        "valid_choices": choices,
        "next_commands": commands,
    }


__all__ = [
    "API_RESULT",
    "LOCAL_ENTITIES",
    "WEBSITE_GET",
    "RecoveryPlan",
    "api_filters",
    "detail_source_supported",
    "invalid_filters_plan",
    "not_found_plan",
    "recovery_payload",
    "related_mode_plan",
    "safe_identifier",
    "unsupported_detail_plan",
    "unsupported_search_plan",
    "validate_filters",
    "variant_symbol_plan",
]
