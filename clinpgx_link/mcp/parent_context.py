"""Validation and wire presentation for explicit original-source parent context."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from clinpgx_link.content.assets import AssetReference
from clinpgx_link.data.parent_context import PARENT_CONTEXT_FIELDS
from clinpgx_link.exceptions import InvalidInputError, UpstreamUnavailableError
from clinpgx_link.mcp.row_provenance import row_provenance
from clinpgx_link.mcp.untrusted_content import fence_text
from clinpgx_link.models import SourceResponse


def _invalid_parent_fields(*, subtype: str | None = None) -> InvalidInputError:
    return InvalidInputError(
        "Invalid parent field selection.",
        field="parent_fields",
        hint="Use one or two unique names from get_dataset parent-context metadata.",
        subtype=subtype,
    )


def validate_parent_fields(value: Sequence[str] | None) -> tuple[str, ...] | None:
    """Validate a closed, ordered selector before any repository acquisition."""
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _invalid_parent_fields()
    if not 1 <= len(value) <= 2:
        raise _invalid_parent_fields()
    selected: list[str] = []
    for name in value:
        if not isinstance(name, str) or name not in PARENT_CONTEXT_FIELDS or name in selected:
            raise _invalid_parent_fields()
        selected.append(name)
    return tuple(selected)


def validate_parent_pointer_compatibility(
    parent_fields: tuple[str, ...] | None, *, pointer: str, pointers: Sequence[str] | None
) -> None:
    if parent_fields is not None and (bool(pointer) or bool(pointers)):
        raise _invalid_parent_fields(subtype="parent_selection_with_pointer")


def _member_ref(response: SourceResponse, snapshot_id: str) -> str:
    asset = response.details.get("asset")
    if not isinstance(asset, dict):
        raise UpstreamUnavailableError(
            "The indexed row has no retained source member.", subtype="asset_missing"
        )
    try:
        return AssetReference(
            snapshot_id,
            str(asset["dataset_id"]),
            str(asset["member"]),
            str(asset["sha256"]),
        ).encode()
    except (KeyError, TypeError, InvalidInputError) as exc:
        raise UpstreamUnavailableError(
            "The indexed row source member is invalid.", subtype="asset_invalid"
        ) from exc


def _fallback(content_ref: str, pointer: str) -> dict[str, Any]:
    return {
        "content_ref": content_ref,
        "fallback_tool": "get_source_content",
        "fallback_args": {
            "content_ref": content_ref,
            "pointer": pointer,
            "representation": "structure",
        },
    }


def render_parent_context(
    child: dict[str, Any],
    context: dict[str, Any],
    asset_response: SourceResponse,
    snapshot_id: str,
) -> dict[str, Any]:
    """Fence source strings while preserving executable original-member recovery."""
    source = asset_response.source
    content_ref = _member_ref(asset_response, snapshot_id)
    if context.get("status") != "available":
        reason = str(context.get("reason", "parent_context_unavailable"))
        pointer = child.get("parent_pointer")
        relation_was_valid = reason in {
            "parent_record_missing",
            "parent_record_ambiguous",
            "parent_record_malformed",
            "parent_record_oversized",
            "parent_lookup_budget_exhausted",
        }
        recovery_pointer = (
            pointer
            if relation_was_valid and isinstance(pointer, str) and pointer.startswith("/")
            else ""
        )
        return {
            "status": "unavailable",
            "reason": reason,
            **_fallback(content_ref, recovery_pointer),
        }
    parent = context.get("parent")
    entries = context.get("entries")
    if not isinstance(parent, dict) or not isinstance(entries, list):
        return {
            "status": "unavailable",
            "reason": "parent_context_unavailable",
            **_fallback(content_ref, ""),
        }
    record_id = str(parent["record_id"])
    pointer = str(parent["json_pointer"])
    presented_parent = {
        "record_id": record_id,
        "dataset_id": str(parent["dataset_id"]),
        "member": fence_text(str(parent["member"]), source=source, record_id=record_id),
        "ordinal": int(parent["ordinal"]),
        "json_pointer": fence_text(pointer, source=source, record_id=record_id),
        "parent_pointer": fence_text(
            str(parent["parent_pointer"]), source=source, record_id=record_id
        ),
        "content_ref": content_ref,
        "provenance": row_provenance(source),
    }
    presented_entries: list[dict[str, Any]] = []
    for entry in entries:
        name = str(entry["field"])
        original_pointer = f"{pointer}/{name}"
        presented: dict[str, Any] = {
            "field": name,
            "status": str(entry["status"]),
            "original_pointer": fence_text(original_pointer, source=source, record_id=record_id),
        }
        if entry.get("status") == "value":
            value = entry.get("value")
            presented["value"] = (
                fence_text(value, source=source, record_id=record_id)
                if isinstance(value, str)
                else value
            )
        elif entry.get("status") == "deferred":
            presented.update(_fallback(content_ref, original_pointer))
        presented_entries.append(presented)
    return {"status": "available", "parent": presented_parent, "entries": presented_entries}


__all__ = [
    "render_parent_context",
    "validate_parent_fields",
    "validate_parent_pointer_compatibility",
]
