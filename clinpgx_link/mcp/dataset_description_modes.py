"""Pure presentation projections for dataset-description metadata."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

ResponseMode = Literal["minimal", "compact", "standard", "full"]

_MINIMAL_PROFILE_KEYS = (
    "profile_id",
    "shape_id",
    "status",
    "missing_required_fields",
)
_COMPACT_PROFILE_KEYS = (
    *_MINIMAL_PROFILE_KEYS,
    "required_fields",
    "optional_fields",
    "modes",
)
_DESCRIPTIVE_PROSE_KEYS = frozenset({"description", "inclusion_reason"})


def _without_descriptive_prose(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_descriptive_prose(item)
            for key, item in value.items()
            if key not in _DESCRIPTIVE_PROSE_KEYS
        }
    if isinstance(value, list):
        return [_without_descriptive_prose(item) for item in value]
    return deepcopy(value)


def _profile_projection(profile: dict[str, Any], mode: ResponseMode) -> dict[str, Any]:
    if mode == "full":
        return deepcopy(profile)
    if mode == "standard":
        projected = deepcopy(profile)
        fields = projected.get("fields")
        if isinstance(fields, list):
            projected["fields"] = [
                {key: value for key, value in field.items() if key != "inclusion_reason"}
                if isinstance(field, dict)
                else deepcopy(field)
                for field in fields
            ]
        return projected

    keys = _MINIMAL_PROFILE_KEYS if mode == "minimal" else _COMPACT_PROFILE_KEYS
    projected = {key: deepcopy(profile[key]) for key in keys if key in profile}
    if mode == "compact" and isinstance(profile.get("selector"), dict):
        selector = profile["selector"]
        projected["selector"] = {
            key: deepcopy(selector[key])
            for key in ("kind", "pointer_pattern")
            if key in selector and selector[key] is not None
        }
    return projected


def _member_projection(member: dict[str, Any], mode: ResponseMode) -> dict[str, Any]:
    projected = deepcopy(member)
    profiles = projected.get("record_profiles")
    if isinstance(profiles, list):
        projected["record_profiles"] = [
            _profile_projection(profile, mode) if isinstance(profile, dict) else deepcopy(profile)
            for profile in profiles
        ]

    if mode == "minimal":
        projected.pop("supported_filters", None)
        projected.pop("fields", None)
        projected.pop("sheets", None)
    elif mode == "compact":
        for key in ("fields", "sheets"):
            if key in projected:
                projected[key] = _without_descriptive_prose(projected[key])
    return projected


def project_dataset_description(
    description: dict[str, Any], mode: ResponseMode
) -> tuple[dict[str, Any], bool]:
    """Return a defensive mode projection and whether optional detail was removed."""
    projected = deepcopy(description)
    members = projected.get("members")
    if isinstance(members, list):
        projected["members"] = [
            _member_projection(member, mode) if isinstance(member, dict) else deepcopy(member)
            for member in members
        ]
    return projected, projected != description


__all__ = ["ResponseMode", "project_dataset_description"]
