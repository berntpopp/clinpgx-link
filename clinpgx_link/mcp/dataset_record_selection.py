"""Profile-authorized field projection and normalized-record selection metadata."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from typing import Any, Literal

from clinpgx_link.content.reader import select_value
from clinpgx_link.content.store import ContentStore
from clinpgx_link.data.repository import DatasetRepository
from clinpgx_link.exceptions import InvalidInputError, RecoverableSelectionError
from clinpgx_link.mcp.dataset_record_fields import (
    profiled_selected_nested_value_is_safe,
    trusted_fields_for_row,
)
from clinpgx_link.mcp.selection import finite_json_bytes, resolve_scalars
from clinpgx_link.mcp.untrusted_content import fence_text
from clinpgx_link.models import SourceInfo, SourceResponse

ResponseMode = Literal["minimal", "compact", "standard", "full"]
_MAX_FIELDS = 16
_MAX_FIELD_CHARACTERS = 512
_JSON_NUMBER_KEY = "$clinpgxJsonNumber"


def _invalid_fields(*, subtype: str | None = None) -> InvalidInputError:
    return InvalidInputError(
        "Invalid profiled field selection.",
        field="include_fields",
        hint="Use 1 through 16 unique field names advertised by get_dataset.",
        subtype=subtype,
    )


def validate_include_fields(value: Sequence[str] | None) -> tuple[str, ...] | None:
    """Validate generic field-list syntax without consulting a repository."""
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _invalid_fields()
    if not 1 <= len(value) <= _MAX_FIELDS:
        raise _invalid_fields()
    selected: list[str] = []
    seen: set[str] = set()
    for name in value:
        if (
            not isinstance(name, str)
            or not name
            or len(name) > _MAX_FIELD_CHARACTERS
            or name in seen
        ):
            raise _invalid_fields()
        try:
            name.encode("utf-8")
        except UnicodeError as exc:
            raise _invalid_fields() from exc
        selected.append(name)
        seen.add(name)
    return tuple(selected)


def authorized_field_names(
    profile: dict[str, Any] | None,
    include_fields: tuple[str, ...] | None,
    response_mode: ResponseMode,
) -> tuple[tuple[str, ...] | None, bool]:
    """Resolve explicit/profile-mode fields; ``None`` requests retained discovery."""
    if profile is None:
        if include_fields is not None:
            raise _invalid_fields(subtype="field_selection_unsupported")
        return None, False
    if profile.get("status") != "active":
        if include_fields is not None:
            raise _invalid_fields(subtype="profile_drift")
        return None, False
    required = profile.get("required_fields")
    optional = profile.get("optional_fields")
    modes = profile.get("modes")
    if (
        not isinstance(required, list)
        or not isinstance(optional, list)
        or not all(isinstance(name, str) for name in [*required, *optional])
        or not isinstance(modes, dict)
    ):
        if include_fields is not None:
            raise _invalid_fields(subtype="profile_drift")
        return None, False
    authorized = frozenset([*required, *optional])
    if include_fields is not None:
        if any(name not in authorized for name in include_fields):
            raise _invalid_fields(subtype="field_selection_unsupported")
        return include_fields, True
    policy = modes.get(response_mode)
    if not isinstance(policy, dict) or not isinstance(policy.get("fields"), list):
        return None, False
    names = tuple(name for name in policy["fields"] if isinstance(name, str))
    if policy.get("include_all_reachable") is True:
        return None, False
    return names, False


def escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def field_pointer(name: str) -> str:
    return "/fields/" + escape_pointer_token(name)


def original_locator(
    row: dict[str, Any], normalized_pointer: str, member_ref: str
) -> dict[str, Any]:
    """Return an unfenced locator skeleton; the wire renderer fences source text."""
    prefix = "/fields/"
    json_pointer = row.get("json_pointer")
    suffix = normalized_pointer[len(prefix) :] if normalized_pointer.startswith(prefix) else ""
    if json_pointer is None and normalized_pointer.startswith(prefix) and "/" not in suffix:
        return {
            "kind": "tabular_cell",
            "content_ref": member_ref,
            "row_ordinal": int(row["ordinal"]),
            "ordinal_basis": "logical_data_row_1_based",
            "column": suffix.replace("~1", "/").replace("~0", "~"),
        }
    if json_pointer is None:
        return _unavailable(member_ref, "original_source_path_unavailable")
    if not isinstance(json_pointer, str):
        return _unavailable(member_ref, "original_source_path_unavailable")
    if json_pointer.startswith("/sheets/"):
        return _unavailable(member_ref, "normalized_spreadsheet_path_is_synthetic")
    if not normalized_pointer.startswith(prefix):
        return _unavailable(member_ref, "normalized_metadata_has_no_original_source_path")
    suffix = normalized_pointer[len("/fields") :]
    if any(
        token.replace("~1", "/").replace("~0", "~") == _JSON_NUMBER_KEY
        for token in suffix.split("/")
    ):
        return _unavailable(member_ref, "normalized_number_wrapper_is_synthetic")
    original = json_pointer + suffix
    if (
        len(original) > 4096
        or original.count("/") > 128
        or re.search(r"~(?![01])", original) is not None
    ):
        return _unavailable(member_ref, "original_source_path_unavailable")
    return {
        "kind": "json_pointer",
        "content_ref": member_ref,
        "pointer": original,
    }


def _unavailable(member_ref: str, reason: str) -> dict[str, Any]:
    return {"kind": "unavailable", "content_ref": member_ref, "reason": reason}


def _derived_source(source: SourceInfo, suffix: str, raw: bytes) -> SourceInfo:
    return SourceInfo(
        "ClinPGx Link derived dataset record",
        "clinpgx://dataset-record/" + suffix,
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


def _retain_json(store: ContentStore, value: Any, source: SourceInfo, suffix: str) -> str:
    raw = finite_json_bytes(value)
    return store.put(raw, _derived_source(source, suffix, raw), "application/json")


def _fence_locator(locator: dict[str, Any], source: SourceInfo, record_id: str) -> dict[str, Any]:
    result = dict(locator)
    for key in ("pointer", "column"):
        if isinstance(result.get(key), str):
            result[key] = fence_text(result[key], source=source, record_id=record_id)
    return result


def _wire_value(value: Any, source: SourceInfo, record_id: str) -> Any:
    if isinstance(value, str):
        return fence_text(value, source=source, record_id=record_id)
    if isinstance(value, dict):
        return {key: _wire_value(child, source, record_id) for key, child in value.items()}
    if isinstance(value, list):
        return [_wire_value(child, source, record_id) for child in value]
    return value


def _deferred_entry(
    pointer: str,
    value: Any,
    source: SourceInfo,
    record_id: str,
    store: ContentStore,
    locator: dict[str, Any],
) -> dict[str, Any]:
    raw = finite_json_bytes(value)
    scalar_ref = store.put(
        raw,
        _derived_source(source, record_id + "/selection", raw),
        "application/json",
    )
    return {
        "pointer": fence_text(pointer, source=source, record_id=record_id),
        "status": "deferred",
        "derived": True,
        "byte_length": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "content_ref": scalar_ref,
        "fallback_tool": "get_source_content",
        "fallback_args": {
            "content_ref": scalar_ref,
            "pointer": "",
            "representation": "structure",
        },
        "original_locator": _fence_locator(locator, source, record_id),
    }


def render_field_selections(
    row: dict[str, Any],
    names: tuple[str, ...],
    member_ref: str,
    source: SourceInfo,
    store: ContentStore,
) -> tuple[str, list[dict[str, Any]]]:
    """Render ordered profiled values with one greedy per-row inline budget."""
    record_id = str(row["record_id"])
    normalized_ref = _retain_json(store, row, source, record_id)
    fields = row.get("fields")
    if not isinstance(fields, dict):
        fields = {}
    remaining = 12_000
    selections: list[dict[str, Any]] = []
    for name in names:
        pointer = field_pointer(name)
        locator = original_locator(row, pointer, member_ref)
        if name not in fields:
            selections.append(
                {
                    "pointer": fence_text(pointer, source=source, record_id=record_id),
                    "status": "absent",
                    "original_locator": _fence_locator(locator, source, record_id),
                }
            )
            continue
        value = fields[name]
        raw = finite_json_bytes(value)
        unsafe_container = not profiled_selected_nested_value_is_safe(row, name, value)
        if unsafe_container or len(raw) > remaining:
            selections.append(_deferred_entry(pointer, value, source, record_id, store, locator))
            continue
        remaining -= len(raw)
        selections.append(
            {
                "pointer": fence_text(pointer, source=source, record_id=record_id),
                "status": "value",
                "value": _wire_value(value, source, record_id),
                "original_locator": _fence_locator(locator, source, record_id),
            }
        )
    return normalized_ref, selections


def render_pointer_selections(
    row: dict[str, Any],
    pointers: tuple[str, ...],
    member_ref: str,
    source: SourceInfo,
    store: ContentStore,
) -> tuple[str, list[dict[str, Any]]]:
    """Render scalar-kernel results without exposing user pointers as arguments."""
    record_id = str(row["record_id"])
    normalized_ref = _retain_json(store, row, source, record_id)
    try:
        resolved = resolve_scalars(row, pointers)
    except InvalidInputError as exc:
        if exc.subtype != "scalar_selection_required":
            raise
        raise RecoverableSelectionError(content_ref=normalized_ref, subtype=exc.subtype) from exc
    selections: list[dict[str, Any]] = []
    for item in resolved:
        locator = original_locator(row, item.pointer, member_ref)
        base = {
            "pointer": fence_text(item.pointer, source=source, record_id=record_id),
            "status": item.status,
            "original_locator": _fence_locator(locator, source, record_id),
        }
        if item.status == "value":
            base["value"] = _wire_value(item.value, source, record_id)
        elif item.status == "deferred":
            value = select_value(row, item.pointer)
            base = _deferred_entry(item.pointer, value, source, record_id, store, locator)
        selections.append(base)
    return normalized_ref, selections


def profiled_row(
    row: dict[str, Any],
    response: SourceResponse,
    snapshot_id: str,
    store: ContentStore,
    repository: DatasetRepository,
    response_mode: ResponseMode,
    include_fields: tuple[str, ...] | None,
    shape_row: Callable[..., dict[str, Any]],
    *,
    asset_response: SourceResponse | None = None,
    force_defer_fields: bool = False,
) -> dict[str, Any]:
    """Apply candidate-bound profile projection before any field safety checks."""
    profile = repository.record_profile(
        str(row.get("dataset_id", "")),
        str(row.get("member", "")),
        row.get("json_pointer") if isinstance(row.get("json_pointer"), str) else None,
    )
    names, explicit = authorized_field_names(profile, include_fields, response_mode)
    owning_response = asset_response or response
    if explicit:
        result = shape_row(
            {**row, "fields": {}},
            response,
            snapshot_id,
            store,
            asset_response=asset_response,
            trusted_field_names=frozenset(),
            retained_row=row,
            profile_projection=True,
        )
        result.pop("fields", None)
        result.pop("field_names", None)
        normalized_ref, selections = render_field_selections(
            row, names or (), str(result["content_ref"]), owning_response.source, store
        )
        result["normalized_record_ref"] = normalized_ref
        result["selections"] = selections
        return result
    if names is None:
        trusted = (
            trusted_fields_for_row(row)
            if profile and profile.get("status") == "active"
            else frozenset()
        )
        result = shape_row(
            row,
            response,
            snapshot_id,
            store,
            asset_response=asset_response,
            trusted_field_names=trusted,
            force_defer_fields=force_defer_fields,
        )
        if profile is None:
            result["record_profile_status"] = "unprofiled"
        elif profile.get("status") != "active":
            result["record_profile_status"] = "profile_drift"
        return result
    fields = row.get("fields")
    selected_fields = (
        {name: fields[name] for name in names if name in fields} if isinstance(fields, dict) else {}
    )
    return shape_row(
        {**row, "fields": selected_fields},
        response,
        snapshot_id,
        store,
        asset_response=asset_response,
        trusted_field_names=frozenset(names),
        force_defer_fields=force_defer_fields,
        retained_row=row,
        profile_projection=True,
    )


def select_dataset_record_value(
    row: dict[str, Any], pointer: str, response: SourceResponse, store: ContentStore
) -> dict[str, Any]:
    """Retain legacy one-pointer selection in the normalized-record namespace."""
    selected = select_value(row, pointer)
    raw = finite_json_bytes(selected)
    derived_ref = _retain_json(store, row, response.source, str(row["record_id"]))
    if len(raw) > 12_000:
        return {
            "deferred_content": True,
            "content_ref": derived_ref,
            "pointer": pointer,
            "representation": "normalized_record_json",
            "derived": True,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "length": len(raw),
            "unit": "bytes",
            "fallback_tool": "get_source_content",
            "fallback_args": {
                "content_ref": derived_ref,
                "pointer": pointer,
                "representation": "structure",
            },
        }
    return {
        "pointer": pointer,
        "representation": "normalized_record_json",
        "derived": True,
        "content_ref": derived_ref,
        "data": fence_text(raw.decode(), source=response.source, record_id=str(row["record_id"])),
    }


def pointer_selected_row(
    row: dict[str, Any],
    response: SourceResponse,
    snapshot_id: str,
    store: ContentStore,
    pointers: tuple[str, ...],
    shape_row: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Project pointers before whole-row field safety logic can inspect unselected data."""
    result = shape_row(
        {**row, "fields": {}},
        response,
        snapshot_id,
        store,
        trusted_field_names=frozenset(),
        retained_row=row,
        profile_projection=True,
    )
    result.pop("fields", None)
    result.pop("field_names", None)
    normalized_ref, selections = render_pointer_selections(
        row, pointers, str(result["content_ref"]), response.source, store
    )
    result["normalized_record_ref"] = normalized_ref
    result["selections"] = selections
    return result


__all__ = [
    "ResponseMode",
    "authorized_field_names",
    "field_pointer",
    "original_locator",
    "pointer_selected_row",
    "profiled_row",
    "render_field_selections",
    "render_pointer_selections",
    "select_dataset_record_value",
    "validate_include_fields",
]
