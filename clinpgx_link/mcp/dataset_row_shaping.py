"""Wire-safe shaping for normalized rows in the immutable dataset index."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from clinpgx_link.content.assets import AssetReference
from clinpgx_link.content.store import ContentStore
from clinpgx_link.exceptions import InvalidInputError, UpstreamUnavailableError
from clinpgx_link.mcp.dataset_record_fields import (
    profiled_nested_fields_are_safe,
    profiled_selected_nested_value_is_safe,
    trusted_fields_for_row,
)
from clinpgx_link.mcp.row_provenance import derived_record_source, row_provenance
from clinpgx_link.mcp.untrusted_content import fence_text
from clinpgx_link.models import SourceInfo, SourceResponse

_MAX_INLINE_FIELD_BYTES = 12_000
_MAX_POINTER_CHARACTERS = 4096
_MAX_POINTER_SEGMENTS = 128


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _retain_row(store: ContentStore, row: dict[str, Any], source: SourceInfo) -> str:
    raw = _json(row)
    return store.put(
        raw, derived_record_source(source, str(row["record_id"]), raw), "application/json"
    )


def _has_oversized_string(value: Any) -> bool:
    if isinstance(value, str):
        return len(value.encode("utf-8")) > _MAX_INLINE_FIELD_BYTES
    if isinstance(value, dict):
        return any(_has_oversized_string(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_oversized_string(item) for item in value)
    return False


def _pointer_is_safe(pointer: str) -> bool:
    return (
        pointer.startswith("/")
        and len(pointer) <= _MAX_POINTER_CHARACTERS
        and pointer.count("/") <= _MAX_POINTER_SEGMENTS
        and re.search(r"~(?![01])", pointer) is None
    )


def _has_unsafe_field_key(
    value: Any, *, trusted_field_names: frozenset[str] | None = None, depth: int = 0
) -> bool:
    if isinstance(value, dict):
        if depth >= _MAX_POINTER_SEGMENTS:
            return True
        for key, item in value.items():
            key_text = str(key)
            if (
                depth == 0
                and trusted_field_names is not None
                and key_text not in trusted_field_names
            ):
                return True
            if (
                not key_text
                or len(key_text) > 512
                or any(ord(char) < 0x20 or ord(char) == 0x7F for char in key_text)
                or "/" in key_text
                or "~" in key_text
            ):
                return True
            if _has_unsafe_field_key(
                item, trusted_field_names=trusted_field_names, depth=depth + 1
            ):
                return True
    elif isinstance(value, list):
        if depth >= _MAX_POINTER_SEGMENTS:
            return True
        return any(_has_unsafe_field_key(item, depth=depth + 1) for item in value)
    return False


def _has_overlong_field_pointer(value: Any, pointer: str = "/fields") -> bool:
    if not _pointer_is_safe(pointer):
        return True
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{pointer}/{_escape_pointer_token(str(key))}"
            if _has_overlong_field_pointer(item, child):
                return True
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if _has_overlong_field_pointer(item, f"{pointer}/{index}"):
                return True
    return False


def _needs_deferred_fields(
    value: Any, *, trusted_field_names: frozenset[str] | None = None
) -> bool:
    return _has_unsafe_field_key(value, trusted_field_names=trusted_field_names)


def _escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _deferred_field(
    value: str, pointer: str, derived_ref: str, source: SourceInfo, record_id: str
) -> dict[str, Any]:
    if not _pointer_is_safe(pointer):
        return _fields_descriptor(derived_ref, pointer="", representation="base64")
    return {
        "deferred_content": True,
        "content_ref": derived_ref,
        "pointer": pointer,
        "representation": "normalized_record_json",
        "derived": True,
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "length": len(value),
        "unit": "characters",
        "fallback_tool": "get_source_content",
        "fallback_args": {
            "content_ref": derived_ref,
            "pointer": pointer,
            "representation": "text",
        },
        "provenance": {
            "source": source.source,
            "record_id": record_id,
            "retrieved_at": source.retrieved_at,
        },
    }


def _fields_descriptor(
    derived_ref: str, *, pointer: str = "/fields", representation: str = "structure"
) -> dict[str, Any]:
    return {
        "deferred_content": True,
        "content_ref": derived_ref,
        "pointer": pointer,
        "representation": "normalized_record_json",
        "derived": True,
        "recovery_representation": representation,
        "fallback_tool": "get_source_content",
        "fallback_args": {
            "content_ref": derived_ref,
            "pointer": pointer,
            "representation": representation,
        },
    }


def _shape_fields(
    value: Any,
    *,
    pointer: str,
    derived_ref: str | None,
    source: SourceInfo,
    record_id: str,
) -> Any:
    if isinstance(value, dict):
        return {
            key: _shape_fields(
                item,
                pointer=f"{pointer}/{_escape_pointer_token(str(key))}",
                derived_ref=derived_ref,
                source=source,
                record_id=record_id,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _shape_fields(
                item,
                pointer=f"{pointer}/{index}",
                derived_ref=derived_ref,
                source=source,
                record_id=record_id,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_INLINE_FIELD_BYTES and derived_ref is not None:
            return _deferred_field(value, pointer, derived_ref, source, record_id)
        return fence_text(value, source=source, record_id=record_id)
    return value


def _asset_reference(response: SourceResponse, snapshot_id: str) -> str:
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


def shape_dataset_row(
    row: dict[str, Any],
    response: SourceResponse,
    snapshot_id: str,
    store: ContentStore,
    *,
    asset_response: SourceResponse | None = None,
    trusted_field_names: frozenset[str] | None = None,
    force_defer_fields: bool = False,
    retained_row: dict[str, Any] | None = None,
    profile_projection: bool = False,
) -> dict[str, Any]:
    owning_response = asset_response or response
    source = owning_response.source
    source_ref = _asset_reference(owning_response, snapshot_id)
    fields = row.get("fields", {})
    owned_names = trusted_fields_for_row(row)
    trusted_names = (
        owned_names
        if trusted_field_names is None
        else frozenset(trusted_field_names).intersection(owned_names)
    )
    projected_nested_safe = isinstance(fields, dict) and all(
        profiled_selected_nested_value_is_safe(retained_row or row, str(name), value)
        for name, value in fields.items()
    )
    defer_fields = (
        force_defer_fields
        or not (
            projected_nested_safe if profile_projection else profiled_nested_fields_are_safe(row)
        )
        or _needs_deferred_fields(fields, trusted_field_names=trusted_names)
    )
    raw_row = retained_row or row
    derived_ref = (
        _retain_row(store, raw_row, source) if defer_fields or _has_oversized_string(row) else None
    )
    result = dict(row)
    result["content_ref"] = source_ref
    result["provenance"] = row_provenance(source)
    record_id = str(row["record_id"])
    result["member"] = fence_text(str(row["member"]), source=source, record_id=record_id)
    asset = owning_response.details.get("asset")
    if isinstance(asset, dict) and "sha256" in asset:
        result["member_sha256"] = str(asset["sha256"])
    for pointer_key in ("json_pointer", "parent_pointer"):
        if pointer_key in result and result[pointer_key] is not None:
            result[pointer_key] = fence_text(
                str(result[pointer_key]), source=source, record_id=record_id
            )
    if defer_fields:
        root_ref = derived_ref or _retain_row(store, raw_row, source)
        result["fields"] = _fields_descriptor(
            root_ref,
            pointer="" if _has_overlong_field_pointer(fields) else "/fields",
            representation="base64" if _has_overlong_field_pointer(fields) else "structure",
        )
    else:
        result["fields"] = _shape_fields(
            fields,
            pointer="/fields",
            derived_ref=derived_ref,
            source=source,
            record_id=record_id,
        )
        if isinstance(fields, dict) and not profile_projection:
            result["field_names"] = [
                fence_text(str(name), source=source, record_id=record_id) for name in fields
            ]
    return result


__all__ = ["_asset_reference", "_json", "shape_dataset_row"]
