"""Bounded scalar projections over one retained live-adapter response."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

from fastmcp.tools.base import ToolResult

from clinpgx_link.content.reader import select_value
from clinpgx_link.content.store import ContentStore
from clinpgx_link.exceptions import InvalidInputError, ResponseTooLargeError
from clinpgx_link.identity_contracts import (
    NUMERIC_IDENTITY_CONTRACTS,
    numeric_identity_contract,
)
from clinpgx_link.mcp.envelope import success_result
from clinpgx_link.mcp.selection import finite_json_bytes, resolve_scalars
from clinpgx_link.mcp.shaping import source_pointer
from clinpgx_link.mcp.untrusted_content import fence_text
from clinpgx_link.models import SourceInfo, SourceResponse

ResponseMode = Literal["minimal", "compact", "standard", "full"]
_UNPROFILED = object()


@dataclass(frozen=True, slots=True)
class AdapterProfile:
    """Code-owned general-purpose fields for one verified source family."""

    minimal: tuple[str, ...]
    compact: tuple[str, ...]
    standard: tuple[str, ...]
    required: tuple[str, ...]


_IDENTITY = AdapterProfile(("id",), ("id", "name"), ("id", "name", "objCls"), ("id",))
_PROFILES: dict[str, AdapterProfile] = {
    "gene": AdapterProfile(
        ("id",),
        ("id", "name", "symbol"),
        ("id", "name", "symbol", "description", "objCls", "buildVersion"),
        ("id", "name", "symbol"),
    ),
    "chemical": AdapterProfile(
        ("id",),
        ("id", "name", "types"),
        ("id", "name", "types", "objCls", "altNames", "linkOuts"),
        ("id", "name"),
    ),
    "guideline": AdapterProfile(
        ("id",),
        ("id", "name", "source", "relatedGenes", "relatedChemicals"),
        (
            "id",
            "name",
            "source",
            "relatedGenes",
            "relatedChemicals",
            "summaryMarkdown",
        ),
        ("id", "name"),
    ),
    "guideline_annotation": AdapterProfile(
        ("id",),
        ("id", "name", "source", "relatedGenes", "relatedChemicals"),
        (
            "id",
            "name",
            "source",
            "relatedGenes",
            "relatedChemicals",
            "summaryMarkdown",
        ),
        ("id", "name"),
    ),
    "connected_object": AdapterProfile(
        ("connectedObject",),
        ("connectedObject", "connectionTypes"),
        ("connectedObject", "connectionTypes"),
        ("connectedObject", "connectionTypes"),
    ),
    "pathway_category": _IDENTITY,
    "identity": _IDENTITY,
    **{
        entity: AdapterProfile(
            contract.minimal,
            contract.compact,
            contract.standard,
            tuple(name for name, _kind in contract.required_types),
        )
        for entity, contract in NUMERIC_IDENTITY_CONTRACTS.items()
    },
}
_FAMILY_PROFILES = {
    "gene": "gene",
    "chemical": "chemical",
    "guideline": "guideline",
    "guideline_annotation": "guideline_annotation",
    "haplotype": "identity",
    "allele": "identity",
    "annotation_id": "identity",
    "connection": "identity",
    "data_annotation": "identity",
    "disease": "identity",
    "evidence": "identity",
    "label": "identity",
    "literature": "literature",
    "literature_annotation": "identity",
    "multilink_annotation": "identity",
    "ontology_term": "identity",
    "pathway": "identity",
    "relationship": "identity",
    "summary_annotation": "summary_annotation",
    "variant": "identity",
    "variant_annotation": "variant_annotation",
    "vip": "identity",
    "vip_variant": "identity",
}
_ROUTE_PROFILES = {
    "pathway": "identity",
    "gene": "gene",
    "chemical": "chemical",
    "disease": "identity",
    "variant": "identity",
    "literature": "literature",
    "guidelineAnnotation": "guideline_annotation",
    "label": "identity",
    "summaryAnnotation": "summary_annotation",
    "variantAnnotation": "variant_annotation",
    "vip": "identity",
    "ontologyTerm": "identity",
    "dataAnnotation": "identity",
    "connection": "identity",
}


def adapter_profile(operation: str, *, family: str | None = None) -> str:
    """Map only verified route/family names onto code-owned profiles."""
    if family is not None:
        return _FAMILY_PROFILES.get(family, "unprofiled")
    if operation == "GET /report/stats":
        return "stats"
    if operation == "GET /site/pathwayCategories":
        return "pathway_category"
    match = re.match(r"(?:GET|POST) /(data|site)/([^/{]+)", operation)
    if match is None:
        return "unprofiled"
    namespace, segment = match.groups()
    if namespace == "data":
        return _ROUTE_PROFILES.get(segment, "unprofiled")
    return {
        "gene": "gene",
        "guideline": "guideline",
        "guidelineAnnotation": "guideline_annotation",
        "pathway": "identity",
    }.get(segment, "unprofiled")


def adapter_profile_status(value: Any, profile_name: str | None) -> str | None:
    """Validate one code-owned profile against the source value before projection."""
    if profile_name is None:
        return None
    if profile_name == "stats":
        if not isinstance(value, dict) or not value:
            return "unprofiled"
        stat_name_shape = isinstance(value.get("statName"), str)
        count_shape = all(
            isinstance(item, dict) and type(item.get("count")) in {int, float}
            for item in value.values()
        )
        return "active" if stat_name_shape or count_shape else "unprofiled"
    profile = _PROFILES.get(profile_name)
    if not isinstance(value, dict) or profile is None:
        return "unprofiled"
    numeric_contract = numeric_identity_contract(profile_name)
    if numeric_contract is not None:
        return "active" if numeric_contract.source_value_is_valid(value) else "unprofiled"
    if any(name not in value for name in profile.required):
        return "unprofiled"
    if profile_name == "connected_object":
        if not isinstance(value["connectedObject"], dict) or not isinstance(
            value["connectionTypes"], list
        ):
            return "unprofiled"
    elif any(not isinstance(value[name], str) for name in profile.required):
        return "unprofiled"
    return "active"


def project_adapter_value(value: Any, profile_name: str | None, mode: ResponseMode) -> Any:
    """Apply one source-family profile without deriving authority from source keys."""
    status = adapter_profile_status(value, profile_name)
    if status == "unprofiled":
        return value if mode == "full" else _UNPROFILED
    if mode == "full" or profile_name is None:
        return value
    if profile_name == "stats":
        # This scalar aggregate endpoint has no optional row payload to tier.
        return value
    profile = _PROFILES.get(profile_name or "")
    if profile is None:
        return _UNPROFILED
    names = getattr(profile, mode)
    return {name: value[name] for name in names if name in value}


def adapter_projection_is_unprofiled(value: Any) -> bool:
    return value is _UNPROFILED


def _derived_source(source: SourceInfo, kind: str, raw: bytes) -> SourceInfo:
    return SourceInfo(
        f"ClinPGx Link derived {kind}",
        f"clinpgx://{kind}",
        source.retrieved_at,
        hashlib.sha256(raw).hexdigest(),
        "derived",
        published_at=source.published_at,
        release_tag=source.release_tag,
        coverage="derived_not_original",
        warnings=source.warnings,
        retrieval_time_kind=source.retrieval_time_kind,
        acquired_at=source.acquired_at,
        admitted_at=source.admitted_at,
    )


def _retain_json(store: ContentStore, value: Any, source: SourceInfo, kind: str) -> str:
    raw = finite_json_bytes(value)
    return store.put(raw, _derived_source(source, kind, raw), "application/json")


def _wire_value(value: Any, source: SourceInfo, record_id: str) -> Any:
    if isinstance(value, str):
        return fence_text(value, source=source, record_id=record_id)
    return value


def _locator(response: SourceResponse, pointer: str) -> dict[str, Any]:
    original_ref = str(response.details["content_ref"])
    original_pointer = source_pointer(response.details.get("source_pointer"), pointer)
    if original_pointer is None:
        return {
            "kind": "unavailable",
            "content_ref": original_ref,
            "reason": "original_source_path_unavailable",
        }
    return {
        "kind": "json_pointer",
        "content_ref": original_ref,
        "pointer": fence_text(original_pointer, source=response.source, record_id=original_ref),
    }


def ensure_json_selection(response: SourceResponse) -> None:
    media_type = response.details.get("media_type")
    if not isinstance(media_type, str) or not (
        media_type == "application/json" or media_type.endswith("+json")
    ):
        raise InvalidInputError(
            "Scalar selection requires a verified JSON representation.",
            field="pointers",
            hint="Request JSON or JSON-LD, or retrieve the retained source bytes.",
            subtype="json_selection_required",
        )


def render_adapter_selections(
    response: SourceResponse, pointers: tuple[str, ...], store: ContentStore
) -> dict[str, Any]:
    """Render ordered scalar entries while preserving original and derived identities."""
    ensure_json_selection(response)
    adapter_ref = _retain_json(store, response.value, response.source, "adapter-value")
    resolved = resolve_scalars(response.value, pointers)
    original_ref = str(response.details["content_ref"])
    entries: list[dict[str, Any]] = []
    for item in resolved:
        base: dict[str, Any] = {
            "pointer": fence_text(item.pointer, source=response.source, record_id=original_ref),
            "status": item.status,
            "original_locator": _locator(response, item.pointer),
        }
        if item.status == "value":
            base["value"] = _wire_value(item.value, response.source, original_ref)
        elif item.status == "deferred":
            value = select_value(response.value, item.pointer)
            raw = finite_json_bytes(value)
            scalar_ref = store.put(
                raw, _derived_source(response.source, "adapter-selection", raw), "application/json"
            )
            base.update(
                derived=True,
                byte_length=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                content_ref=scalar_ref,
                fallback_tool="get_source_content",
                fallback_args={
                    "content_ref": scalar_ref,
                    "pointer": "",
                    "representation": "structure",
                },
            )
        entries.append(base)
    result: dict[str, Any] = {
        "content_ref": original_ref,
        "adapter_value_ref": adapter_ref,
        "representation": "adapter_value_json",
        "derived": True,
        "selections": entries,
    }
    license_info = response.details.get("license")
    if isinstance(license_info, dict) and license_info.get("spdx") in {
        "CC-BY-SA-4.0",
        "CC0-1.0",
    }:
        result["source_details"] = {"license": {"spdx": license_info["spdx"]}}
    return result


def adapter_selection_result(
    response: SourceResponse, pointers: tuple[str, ...], store: ContentStore
) -> ToolResult:
    """Apply the shared live-selection row and envelope budgets at the MCP boundary."""
    selected = render_adapter_selections(response, pointers, store)
    if len(finite_json_bytes(selected)) > 70_000:
        raise ResponseTooLargeError("The selected source row exceeds its bounded descriptor size.")
    return success_result(
        selected,
        source=response.source,
        content_ref=response.details["content_ref"],
    )


__all__ = [
    "ResponseMode",
    "adapter_profile",
    "adapter_profile_status",
    "adapter_projection_is_unprofiled",
    "adapter_selection_result",
    "ensure_json_selection",
    "project_adapter_value",
    "render_adapter_selections",
]
