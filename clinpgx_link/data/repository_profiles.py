"""Record-profile support kept separate from core repository queries."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from clinpgx_link.data.profile_validation import (
    LoadedProfileValidation,
    load_profile_validation,
)
from clinpgx_link.exceptions import UpstreamUnavailableError


class RepositoryProfileSupport:
    """Mixin for candidate-bound profile state loaded once per repository."""

    database: Path
    _connection: sqlite3.Connection
    _snapshot_id: str
    _release_tag: str
    _profile_validation: LoadedProfileValidation

    def _load_profile_validation(self) -> None:
        has_member_inventory = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_member'"
        ).fetchone()
        if has_member_inventory is None:
            self._profile_validation = load_profile_validation(None, set(), self._snapshot_id)
            return
        installed_members = {
            (str(row[0]), str(row[1]))
            for row in self._connection.execute(
                "SELECT dataset_id,path FROM source_member"
            ).fetchall()
        }
        receipt = self._connection.execute(
            "SELECT value FROM metadata WHERE key='record_profile_validation_json'"
        ).fetchone()
        self._profile_validation = load_profile_validation(
            str(receipt[0]) if receipt is not None else None,
            installed_members,
            self._snapshot_id,
        )

    def _validate_snapshot(self, expected_snapshot: str | None) -> None:
        if expected_snapshot is not None and expected_snapshot != self._snapshot_id:
            raise UpstreamUnavailableError(
                "Requested snapshot does not match the open immutable snapshot",
                subtype="snapshot_mismatch",
            )

    def status(self) -> dict[str, Any]:
        return {
            "snapshot_id": self._snapshot_id,
            "release_tag": self._release_tag,
            "database": str(self.database),
            "ready": True,
        }

    def record_profile(
        self, dataset_id: str, member: str, json_pointer: str | None = None
    ) -> dict[str, Any] | None:
        """Return cached candidate-bound authority for one normalized row shape."""
        return self._profile_validation.row_profile(dataset_id, member, json_pointer)

    def _profile_member_metadata(self, dataset_id: str, member: str) -> dict[str, Any]:
        profiles = self._profile_validation.member_profiles(dataset_id, member)
        unprofiled = [
            {"shape_id": item["shape_id"], "status": "unprofiled"}
            for item in self._profile_validation.unprofiled_shapes
            if item["dataset_id"] == dataset_id and item["member"] == member
        ]
        return {
            "record_profile_status": (
                str(profiles[0]["status"])
                if profiles
                else "unprofiled"
                if self._profile_validation.receipt_valid
                else "unknown"
            ),
            "record_profiles": profiles,
            "unprofiled_shapes": unprofiled,
        }


__all__ = ["RepositoryProfileSupport"]
