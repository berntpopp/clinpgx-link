"""Candidate validation and strict loading for code-owned record profiles."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, cast

from clinpgx_link.data.record_profiles import (
    PROFILE_DEFINITION_DIGEST,
    RECORD_PROFILES,
    RecordProfile,
    profile_declaration,
    profile_for_row,
    profile_for_shape,
    profiles_for_member,
)

PROFILE_RECEIPT_SCHEMA_VERSION = 1
ProfileGateStatus = Literal["passing", "nonpassing", "unknown"]
ProfileStatus = Literal["active", "profile_drift"]
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "snapshot_id",
        "profile_definition_digest",
        "gate_status",
        "profiles",
        "unprofiled_shapes",
    }
)
_ASSESSMENT_KEYS = frozenset(
    {
        "profile_id",
        "dataset_id",
        "member",
        "shape_id",
        "status",
        "missing_required_fields",
    }
)
_UNPROFILED_KEYS = frozenset({"dataset_id", "member", "shape_id"})


@dataclass(frozen=True)
class LoadedProfileValidation:
    gate_status: ProfileGateStatus
    assessments: dict[str, dict[str, Any]]
    unprofiled_shapes: tuple[dict[str, str], ...]
    receipt_valid: bool

    def dataset_gate_status(self, dataset_id: str) -> ProfileGateStatus:
        if not self.receipt_valid:
            return "unknown"
        selected = [item for item in self.assessments.values() if item["dataset_id"] == dataset_id]
        if not selected:
            return "unknown"
        return (
            "nonpassing"
            if any(item["status"] == "profile_drift" for item in selected)
            else "passing"
        )

    def member_profiles(self, dataset_id: str, member: str) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for profile in profiles_for_member(dataset_id, member):
            assessment = self.assessments.get(profile.profile_id)
            if assessment is None:
                assessment = _drift_assessment(profile)
            values.append(
                {
                    **profile_declaration(profile),
                    "status": assessment["status"],
                    "missing_required_fields": list(
                        cast(list[str], assessment["missing_required_fields"])
                    ),
                }
            )
        return values

    def row_profile(
        self, dataset_id: str, member: str, json_pointer: str | None
    ) -> dict[str, Any] | None:
        profile = profile_for_row(dataset_id, member, json_pointer)
        if profile is None:
            return None
        assessment = self.assessments.get(profile.profile_id)
        if assessment is None:
            assessment = _drift_assessment(profile)
        return {
            **profile_declaration(profile),
            "status": assessment["status"],
            "missing_required_fields": list(cast(list[str], assessment["missing_required_fields"])),
        }


def _drift_assessment(profile: RecordProfile) -> dict[str, Any]:
    return {
        "profile_id": profile.profile_id,
        "dataset_id": profile.dataset_id,
        "member": profile.member,
        "shape_id": profile.shape_id,
        "status": "profile_drift",
        "missing_required_fields": list(profile.required_fields),
    }


def _profile_assessment(
    connection: sqlite3.Connection, profile: RecordProfile, headers_json: str | None, count: int
) -> dict[str, Any]:
    if profile.selector.kind == "tabular":
        try:
            headers = json.loads(headers_json) if headers_json is not None else None
        except (json.JSONDecodeError, TypeError):
            headers = None
        present = (
            set(headers)
            if isinstance(headers, list) and all(isinstance(item, str) for item in headers)
            else set()
        )
        missing = [name for name in profile.required_fields if name not in present]
        if count == 0:
            missing = list(profile.required_fields)
    else:
        rows = connection.execute(
            "SELECT json_pointer,fields_json FROM record WHERE dataset_id=? AND member=? "
            "ORDER BY ordinal,json_pointer",
            (profile.dataset_id, profile.member),
        ).fetchall()
        matched = 0
        missing_set: set[str] = set()
        for pointer, fields_json in rows:
            if not profile.selector.matches(pointer):
                continue
            matched += 1
            try:
                fields = json.loads(fields_json)
            except (json.JSONDecodeError, TypeError):
                fields = None
            keys = set(fields) if isinstance(fields, dict) else set()
            missing_set.update(name for name in profile.required_fields if name not in keys)
        missing = [name for name in profile.required_fields if name in missing_set]
        if matched == 0:
            missing = list(profile.required_fields)
    status: ProfileStatus = "profile_drift" if missing else "active"
    return {
        "profile_id": profile.profile_id,
        "dataset_id": profile.dataset_id,
        "member": profile.member,
        "shape_id": profile.shape_id,
        "status": status,
        "missing_required_fields": missing,
    }


def _unprofiled_shape(
    dataset_id: str, member: str, headers_json: str | None, parser_status: str
) -> dict[str, str]:
    suffix = PurePosixPath(member).suffix.lower()
    if suffix == ".json" and parser_status == "indexed":
        shape_id = "unprofiled_json"
    elif headers_json is not None and suffix in {".tsv", ".csv"}:
        shape_id = "unprofiled_tabular"
    elif headers_json is not None and suffix == ".xlsx":
        shape_id = "unprofiled_spreadsheet"
    else:
        shape_id = "unprofiled_member"
    return {"dataset_id": dataset_id, "member": member, "shape_id": shape_id}


def validate_candidate_profiles(connection: sqlite3.Connection, snapshot_id: str) -> dict[str, Any]:
    """Assess installed declared shapes without changing parser or byte state."""
    members = connection.execute(
        "SELECT dataset_id,path,headers_json,parser_status,record_count "
        "FROM source_member ORDER BY dataset_id,path"
    ).fetchall()
    installed = {(str(row[0]), str(row[1])): row for row in members}
    assessments = []
    for profile in RECORD_PROFILES:
        row = installed.get((profile.dataset_id, profile.member))
        if row is not None:
            assessments.append(_profile_assessment(connection, profile, row[2], int(row[4])))
    unprofiled: list[dict[str, str]] = []
    for row in members:
        dataset_id, member = str(row[0]), str(row[1])
        profiles = profiles_for_member(dataset_id, member)
        if not profiles:
            unprofiled.append(_unprofiled_shape(dataset_id, member, row[2], str(row[3])))
            continue
        if any(profile.selector.kind == "json_pointer" for profile in profiles):
            pointers = connection.execute(
                "SELECT json_pointer FROM record WHERE dataset_id=? AND member=?",
                (dataset_id, member),
            ).fetchall()
            if any(
                not any(profile.selector.matches(pointer[0]) for profile in profiles)
                for pointer in pointers
            ):
                unprofiled.append(
                    {"dataset_id": dataset_id, "member": member, "shape_id": "unprofiled_json"}
                )
    return {
        "schema_version": PROFILE_RECEIPT_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "profile_definition_digest": PROFILE_DEFINITION_DIGEST,
        "gate_status": (
            "passing" if all(item["status"] == "active" for item in assessments) else "nonpassing"
        ),
        "profiles": assessments,
        "unprofiled_shapes": unprofiled,
    }


def _valid_text(value: object, *, maximum: int = 4096) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum


def _fallback(installed_members: set[tuple[str, str]]) -> LoadedProfileValidation:
    assessments = {
        profile.profile_id: _drift_assessment(profile)
        for profile in RECORD_PROFILES
        if (profile.dataset_id, profile.member) in installed_members
    }
    return LoadedProfileValidation("unknown", assessments, (), False)


def load_profile_validation(
    raw: str | None,
    installed_members: set[tuple[str, str]],
    expected_snapshot: str,
) -> LoadedProfileValidation:
    """Strictly decode one bounded receipt; invalid state grants no active profile."""
    fallback = _fallback(installed_members)
    if raw is None or len(raw.encode("utf-8")) > 128_000:
        return fallback
    try:
        receipt = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeError):
        return fallback
    if not isinstance(receipt, dict) or frozenset(receipt) != _RECEIPT_KEYS:
        return fallback
    if (
        receipt.get("schema_version") != PROFILE_RECEIPT_SCHEMA_VERSION
        or receipt.get("snapshot_id") != expected_snapshot
        or receipt.get("profile_definition_digest") != PROFILE_DEFINITION_DIGEST
        or receipt.get("gate_status") not in {"passing", "nonpassing"}
        or not isinstance(receipt.get("profiles"), list)
        or not isinstance(receipt.get("unprofiled_shapes"), list)
        or len(receipt["profiles"]) > len(RECORD_PROFILES)
        or len(receipt["unprofiled_shapes"]) > len(installed_members)
    ):
        return fallback
    expected = {
        profile.profile_id: profile
        for profile in RECORD_PROFILES
        if (profile.dataset_id, profile.member) in installed_members
    }
    assessments: dict[str, dict[str, Any]] = {}
    for item in receipt["profiles"]:
        if not isinstance(item, dict) or frozenset(item) != _ASSESSMENT_KEYS:
            return fallback
        profile_id = item.get("profile_id")
        dataset_id = item.get("dataset_id")
        member = item.get("member")
        shape_id = item.get("shape_id")
        if not all(isinstance(value, str) for value in (profile_id, dataset_id, member, shape_id)):
            return fallback
        profile = profile_for_shape(cast(str, dataset_id), cast(str, member), cast(str, shape_id))
        missing = item.get("missing_required_fields")
        if (
            profile is None
            or profile.profile_id != profile_id
            or profile_id not in expected
            or item.get("status") not in {"active", "profile_drift"}
            or not isinstance(missing, list)
            or len(missing) > len(profile.required_fields)
            or any(type(name) is not str for name in missing)
            or len(set(missing)) != len(missing)
            or any(name not in profile.required_fields for name in missing)
            or missing != [name for name in profile.required_fields if name in missing]
            or (item["status"] == "active") != (not missing)
            or profile.profile_id in assessments
        ):
            return fallback
        assessments[profile.profile_id] = dict(item)
    if set(assessments) != set(expected):
        return fallback
    unprofiled: list[dict[str, str]] = []
    seen_shapes: set[tuple[str, str, str]] = set()
    for item in receipt["unprofiled_shapes"]:
        if not isinstance(item, dict) or frozenset(item) != _UNPROFILED_KEYS:
            return fallback
        dataset_id, member, shape_id = (
            item.get("dataset_id"),
            item.get("member"),
            item.get("shape_id"),
        )
        if (
            not _valid_text(dataset_id, maximum=512)
            or not _valid_text(member)
            or shape_id
            not in {
                "unprofiled_json",
                "unprofiled_tabular",
                "unprofiled_spreadsheet",
                "unprofiled_member",
            }
            or (dataset_id, member) not in installed_members
            or (dataset_id, member, shape_id) in seen_shapes
        ):
            return fallback
        assert isinstance(dataset_id, str)
        assert isinstance(member, str)
        assert isinstance(shape_id, str)
        seen_shapes.add((dataset_id, member, shape_id))
        unprofiled.append(cast(dict[str, str], dict(item)))
    statuses = {item["status"] for item in assessments.values()}
    expected_gate = "nonpassing" if "profile_drift" in statuses else "passing"
    if receipt["gate_status"] != expected_gate:
        return fallback
    return LoadedProfileValidation(
        cast(ProfileGateStatus, receipt["gate_status"]),
        assessments,
        tuple(unprofiled),
        True,
    )


__all__ = [
    "PROFILE_RECEIPT_SCHEMA_VERSION",
    "LoadedProfileValidation",
    "load_profile_validation",
    "validate_candidate_profiles",
]
