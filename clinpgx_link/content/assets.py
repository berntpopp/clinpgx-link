"""Portable identity references for exact assets in retained local releases.

These are identifiers, not access tokens. The checksum detects corruption, not
authorization; consumers must look up the dataset/member inside a validated pinned
repository and compare both snapshot and byte digest. Never resolve a member as a
filesystem path or interpret a reference as permission to fetch an arbitrary URL.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass

from clinpgx_link.exceptions import InvalidInputError


def _invalid() -> InvalidInputError:
    return InvalidInputError("Invalid retained asset reference.", field="content_ref")


@dataclass(frozen=True)
class AssetReference:
    snapshot_id: str
    dataset_id: str
    member: str | None
    sha256: str

    def encode(self) -> str:
        if (
            not isinstance(self.snapshot_id, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", self.snapshot_id) is None
            or not isinstance(self.sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None
            or not isinstance(self.dataset_id, str)
            or not 1 <= len(self.dataset_id) <= 512
            or (
                self.member is not None
                and (not isinstance(self.member, str) or not 1 <= len(self.member) <= 4096)
            )
        ):
            raise _invalid()
        try:
            raw = json.dumps(
                [1, self.snapshot_id, self.dataset_id, self.member, self.sha256],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except UnicodeError as exc:
            raise _invalid() from exc
        encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        reference = f"asset:{hashlib.sha256(raw).hexdigest()}.{encoded}"
        if len(reference) > 8192:
            raise _invalid()
        return reference

    @classmethod
    def decode(cls, reference: str) -> AssetReference:
        if not isinstance(reference, str) or len(reference) > 8192:
            raise _invalid()
        match = re.fullmatch(r"asset:([0-9a-f]{64})\.([A-Za-z0-9_-]+)", reference)
        if match is None:
            raise _invalid()
        digest, encoded = match.groups()
        try:
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if hashlib.sha256(raw).hexdigest() != digest:
                raise _invalid()
            parts = json.loads(raw)
            if (
                not isinstance(parts, list)
                or len(parts) != 5
                or type(parts[0]) is not int
                or parts[0] != 1
            ):
                raise _invalid()
            result = cls(*parts[1:])
            if result.encode() != reference:
                raise _invalid()
            return result
        except (ValueError, TypeError, UnicodeError) as exc:
            raise _invalid() from exc
