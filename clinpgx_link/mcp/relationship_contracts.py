"""Closed executable contract for ClinPGx relationship report routes."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal, NamedTuple

from clinpgx_link.exceptions import InvalidInputError

RelationshipMode = Literal["connected_object", "pair"]

CONNECTED_OBJECT_OPERATION = "GET /report/connectedObjects/{id}/{type}"
PAIR_OPERATION = "GET /report/pair/{firstObjId}/{secondObjId}/{resultType}"
PAIR_RESULT_TYPE_MAPPING: Mapping[str, str] = MappingProxyType(
    {
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
)
RELATIONSHIP_OBJECT_TYPES = frozenset({"Gene", "Chemical", "Disease", "Variant"})


class RelationshipRoute(NamedTuple):
    """One registry-valid live relationship dispatch."""

    mode: RelationshipMode
    operation: str
    path_parameters: dict[str, str]
    query_parameters: dict[str, str] | None


def _unsupported(field: str) -> InvalidInputError:
    return InvalidInputError(
        "The relationship arguments do not match a supported mode.",
        field=field,
        subtype="unsupported_related_mode",
    )


def resolve_api_relationship_route(
    record_id: str,
    result_type: str,
    other_id: str | None,
    other_type: str,
    view: str,
) -> RelationshipRoute:
    """Resolve the closed mode grammar without inventing pair object-type filters."""
    if other_id is None:
        if result_type != "relationship":
            raise _unsupported("other_id")
        return RelationshipRoute(
            "connected_object",
            CONNECTED_OBJECT_OPERATION,
            {"id": record_id, "type": other_type},
            None,
        )

    upstream_result_type = PAIR_RESULT_TYPE_MAPPING.get(result_type)
    if upstream_result_type is None:
        raise _unsupported("result_type")
    return RelationshipRoute(
        "pair",
        PAIR_OPERATION,
        {
            "firstObjId": record_id,
            "secondObjId": other_id,
            "resultType": upstream_result_type,
        },
        {"view": view},
    )


def connected_object_arguments(
    record_id: str,
    other_type: str,
    *,
    source: str = "api",
    view: str = "base",
) -> dict[str, Any]:
    """Build executable public arguments for connected-object mode."""
    return {
        "record_id": record_id,
        "result_type": "relationship",
        "other_type": other_type,
        "source": source,
        "view": view,
    }


def pair_arguments(
    record_id: str,
    other_id: str,
    result_type: str,
    *,
    entity_type: str = "Gene",
    other_type: str = "Chemical",
    source: str = "api",
    view: str = "base",
) -> dict[str, Any]:
    """Build executable public arguments for one captured pair result type."""
    if result_type not in PAIR_RESULT_TYPE_MAPPING:
        raise ValueError("invalid pair result type")
    return {
        "record_id": record_id,
        "other_id": other_id,
        "entity_type": entity_type,
        "other_type": other_type,
        "result_type": result_type,
        "source": source,
        "view": view,
    }


def relationship_recovery_choices() -> dict[str, list[str]]:
    """Return defensive closed choices for invalid mode recovery."""
    return {
        "connected_object_result_type": ["relationship"],
        "mode": ["connected_object", "pair"],
        "pair_result_type": list(PAIR_RESULT_TYPE_MAPPING),
    }


def guideline_website_recommendation(
    entity_type: str, record_id: str, source: str
) -> list[dict[str, Any]] | None:
    """Recommend website representation for guideline annotations to discover publisher URLs."""
    if entity_type in {"guideline_annotation", "guidelineAnnotation"}:
        return [
            {
                "tool": "get_record",
                "arguments": {
                    "entity_type": "guideline_annotation",
                    "record_id": record_id,
                    "source": "website",
                    "pointer": "/data/cpicGuideline/link/resourceId",
                },
                "description": "Fetch CPIC guideline publisher URL directly via website representation",
            }
        ]
    return None


def relationship_next_commands(value: Any, result_type: str) -> list[dict[str, Any]] | None:
    """Recommend direct website publisher URL commands for guideline annotation relationships."""
    if result_type in {"guideline_annotation", "guidelineAnnotation"} and isinstance(value, list):
        cmds: list[dict[str, Any]] = []
        for item in value[:2]:
            if isinstance(item, dict) and "id" in item:
                name = str(item.get("name", ""))
                org = ""
                for candidate in ("CPIC", "DPWG", "RNPGx", "AHA"):
                    if candidate in name:
                        org = candidate
                        break
                desc = (
                    f"Fetch {org} publisher URL via website representation"
                    if org
                    else "Fetch publisher URL via website representation"
                )
                cmds.append(
                    {
                        "tool": "get_record",
                        "arguments": {
                            "entity_type": "guideline_annotation",
                            "record_id": item["id"],
                            "source": "website",
                            "pointer": (
                                "/data/cpicGuideline/link/resourceId"
                                if org == "CPIC"
                                else "/data/guideline"
                            ),
                        },
                        "description": desc,
                    }
                )
        return cmds or None
    return None


def relationship_capabilities_payload() -> dict[str, Any]:
    """Publish bounded relationship grammar and syntax-only examples."""
    return {
        "identifier_workflow": (
            "Resolve gene and chemical names with search_records, then reuse returned IDs."
        ),
        "guideline_url_workflow": (
            "Guideline annotations from pair reports have publisher URLs and web representations "
            "on the website route via get_record(entity_type='guideline_annotation', record_id=id, "
            "source='website') at /data/cpicGuideline/link/resourceId."
        ),
        "connected_object": {
            "operation": CONNECTED_OBJECT_OPERATION,
            "other_id": "forbidden",
            "result_type": "relationship",
            "other_type_selector": "Sent upstream as the connected-object target type.",
            "example": {
                "tool": "get_related_records",
                "arguments": connected_object_arguments("PA124", "Chemical"),
            },
            "example_purpose": "Request syntax and route binding; not evidence of a match.",
        },
        "pair": {
            "operation": PAIR_OPERATION,
            "other_id": "required",
            "result_types": list(PAIR_RESULT_TYPE_MAPPING),
            "result_type_mapping": dict(PAIR_RESULT_TYPE_MAPPING),
            "object_type_selectors": (
                "Documentation-only; not sent as upstream pair filters or restrictions."
            ),
            "gene_chemical_guideline_example": {
                "tool": "get_related_records",
                "arguments": pair_arguments("PA124", "PA449053", "guideline_annotation"),
            },
            "example_purpose": (
                "Gene/chemical to guideline_annotation syntax; not evidence of a current match."
            ),
        },
    }


__all__ = [
    "PAIR_RESULT_TYPE_MAPPING",
    "RELATIONSHIP_OBJECT_TYPES",
    "RelationshipRoute",
    "connected_object_arguments",
    "guideline_website_recommendation",
    "pair_arguments",
    "relationship_capabilities_payload",
    "relationship_next_commands",
    "relationship_recovery_choices",
    "resolve_api_relationship_route",
]
