"""Bounded lookup of explicitly selected original-source parent fields."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clinpgx_link.data.repository_locking import serialized_connection
from clinpgx_link.models import SourceInfo, SourceResponse

PARENT_CONTEXT_DATASET = "data/pharmcat.zip"
PARENT_CONTEXT_MEMBER = "phenotypes.json"
PARENT_CONTEXT_PROFILE = "pharmcat.diplotype.v1"
PARENT_CONTEXT_FIELDS = ("gene", "version")
_CHILD_POINTER = re.compile(r"(/(?:0|[1-9][0-9]*))/diplotypes/(?:0|[1-9][0-9]*)\Z")


@dataclass(frozen=True)
class ParentContextLimits:
    """Injected limits make supplemental lookup failures deterministic in tests."""

    step_budget: int = 50_000
    timeout_seconds: float = 0.05
    max_fields_json_bytes: int = 262_144

    def __post_init__(self) -> None:
        if self.step_budget < 1 or self.timeout_seconds <= 0 or self.max_fields_json_bytes < 1:
            raise ValueError("Parent context limits must be positive")


class RepositoryParentContextSupport:
    """Mixin for one narrowly declared parent relationship in the retained index."""

    database: Path
    _connection: sqlite3.Connection
    _connection_lock: threading.RLock
    _profile_validation: Any
    _progress_hooks: Any
    _snapshot_id: str
    _release_tag: str
    _parent_context_limits: ParentContextLimits
    _validate_snapshot: Callable[[str | None], None]
    _dataset: Callable[[str], sqlite3.Row]
    _source: Callable[[sqlite3.Row | None], SourceInfo]

    def _initialize_parent_context(self, limits: ParentContextLimits | None = None) -> None:
        self._parent_context_limits = limits or ParentContextLimits()

    def _parent_context_member_metadata(self) -> dict[str, Any]:
        profiles = self._profile_validation.member_profiles(
            PARENT_CONTEXT_DATASET, PARENT_CONTEXT_MEMBER
        )
        status = next(
            (
                str(profile["status"])
                for profile in profiles
                if profile.get("profile_id") == PARENT_CONTEXT_PROFILE
            ),
            "profile_drift",
        )
        return {
            "supported_parent_context": {
                "profile_id": PARENT_CONTEXT_PROFILE,
                "status": status,
                "fields": list(PARENT_CONTEXT_FIELDS),
                "description": (
                    "Explicit original-source fields from the enclosing PharmCAT gene object."
                ),
            }
        }

    def _child_parent_key(self, row: dict[str, Any]) -> tuple[str, str, str] | str:
        if (
            row.get("dataset_id") != PARENT_CONTEXT_DATASET
            or row.get("member") != PARENT_CONTEXT_MEMBER
        ):
            return "parent_context_not_supported"
        pointer = row.get("json_pointer")
        parent_pointer = row.get("parent_pointer")
        profile = self._profile_validation.row_profile(
            PARENT_CONTEXT_DATASET,
            PARENT_CONTEXT_MEMBER,
            pointer if isinstance(pointer, str) else None,
        )
        if profile is None or profile.get("profile_id") != PARENT_CONTEXT_PROFILE:
            return "parent_context_not_supported"
        fields = row.get("fields")
        required = profile.get("required_fields")
        optional = profile.get("optional_fields")
        if (
            profile.get("status") != "active"
            or not isinstance(fields, dict)
            or not isinstance(required, list)
            or not isinstance(optional, list)
            or any(not isinstance(name, str) for name in [*required, *optional])
            or any(name not in fields for name in required)
            or set(fields) - {*required, *optional}
        ):
            return "parent_context_profile_unavailable"
        if (
            not isinstance(pointer, str)
            or not isinstance(parent_pointer, str)
            or len(pointer) > 4096
            or len(parent_pointer) > 4096
        ):
            return "parent_relation_invalid"
        match = _CHILD_POINTER.fullmatch(pointer)
        if match is None or parent_pointer != match.group(1):
            return "parent_relation_invalid"
        return (PARENT_CONTEXT_DATASET, PARENT_CONTEXT_MEMBER, parent_pointer)

    def _lookup_parent(
        self, key: tuple[str, str, str], parent_fields: tuple[str, ...]
    ) -> dict[str, Any]:
        dataset_id, member, parent_pointer = key
        rows = self._connection.execute(
            "SELECT record_pk,record_id,dataset_id,member,ordinal,json_pointer,parent_pointer,"
            "length(CAST(fields_json AS BLOB)) AS fields_json_bytes FROM record "
            "INDEXED BY record_parent_pointer WHERE dataset_id=? AND member=? "
            "AND parent_pointer=? AND json_pointer=? LIMIT 2",
            (dataset_id, member, "", parent_pointer),
        ).fetchall()
        if not rows:
            return {"status": "unavailable", "reason": "parent_record_missing"}
        if len(rows) != 1:
            return {"status": "unavailable", "reason": "parent_record_ambiguous"}
        row = rows[0]
        if int(row["fields_json_bytes"]) > self._parent_context_limits.max_fields_json_bytes:
            return {"status": "unavailable", "reason": "parent_record_oversized"}
        body = self._connection.execute(
            "SELECT fields_json FROM record WHERE record_pk=? AND dataset_id=? AND member=? "
            "AND parent_pointer=? AND json_pointer=? LIMIT 1",
            (row["record_pk"], dataset_id, member, "", parent_pointer),
        ).fetchone()
        try:
            fields = json.loads(body[0]) if body is not None else None
        except (json.JSONDecodeError, TypeError, UnicodeError, RecursionError):
            fields = None
        if not isinstance(fields, dict):
            return {"status": "unavailable", "reason": "parent_record_malformed"}
        entries = []
        for name in parent_fields:
            entry: dict[str, Any] = {"field": name, "status": "absent"}
            if name in fields:
                value = fields[name]
                if value is None or (isinstance(value, str) and len(value.encode("utf-8")) <= 512):
                    entry.update(status="value", value=value)
                else:
                    entry["status"] = "deferred"
            entries.append(entry)
        return {
            "status": "available",
            "parent": {
                "record_id": str(row["record_id"]),
                "dataset_id": str(row["dataset_id"]),
                "member": str(row["member"]),
                "ordinal": int(row["ordinal"]),
                "json_pointer": str(row["json_pointer"]),
                "parent_pointer": str(row["parent_pointer"]),
            },
            "entries": entries,
        }

    @serialized_connection
    def parent_contexts(
        self,
        rows: list[dict[str, Any]],
        parent_fields: tuple[str, ...],
        *,
        expected_snapshot: str | None = None,
    ) -> SourceResponse:
        """Read each unique declared root parent once under one supplemental budget."""
        self._validate_snapshot(expected_snapshot)
        if not rows:
            raise ValueError("Parent context lookup requires at least one child row")
        keys = [self._child_parent_key(row) for row in rows]
        unique = list(dict.fromkeys(key for key in keys if isinstance(key, tuple)))
        looked_up: dict[tuple[str, str, str], dict[str, Any]] = {}
        local_budget: Any = None
        try:
            with self._progress_hooks.budget(
                step_budget=self._parent_context_limits.step_budget,
                timeout_seconds=self._parent_context_limits.timeout_seconds,
            ) as local_budget:
                for key in unique:
                    looked_up[key] = self._lookup_parent(key, parent_fields)
        except sqlite3.OperationalError as exc:
            if (
                "interrupted" not in str(exc).lower()
                or local_budget is None
                or not self._progress_hooks.was_local_interruption(local_budget)
            ):
                raise
            for key in unique:
                looked_up.setdefault(
                    key,
                    {"status": "unavailable", "reason": "parent_lookup_budget_exhausted"},
                )
        contexts = [
            looked_up[key] if isinstance(key, tuple) else {"status": "unavailable", "reason": key}
            for key in keys
        ]
        owning_dataset = PARENT_CONTEXT_DATASET if unique else str(rows[0]["dataset_id"])
        owning_member = PARENT_CONTEXT_MEMBER if unique else str(rows[0]["member"])
        dataset = self._dataset(owning_dataset)
        member = self._connection.execute(
            "SELECT media_type,byte_count,sha256 FROM source_member WHERE dataset_id=? AND path=?",
            (owning_dataset, owning_member),
        ).fetchone()
        details: dict[str, Any] = {"snapshot_id": self._snapshot_id}
        if member is not None:
            details["asset"] = {
                "dataset_id": owning_dataset,
                "member": owning_member,
                "sha256": member["sha256"],
                "total_bytes": member["byte_count"],
                "media_type": member["media_type"],
            }
        return SourceResponse(contexts, self._source(dataset), details)


__all__ = [
    "PARENT_CONTEXT_FIELDS",
    "PARENT_CONTEXT_MEMBER",
    "PARENT_CONTEXT_PROFILE",
    "ParentContextLimits",
    "RepositoryParentContextSupport",
]
