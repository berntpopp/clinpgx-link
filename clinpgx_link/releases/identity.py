"""Content- and contract-addressed release identity, independent of observations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from clinpgx_link.exceptions import DataValidationError


@dataclass(frozen=True)
class ReleaseIdentity:
    canonical_key: bytes
    source_set_identity: str
    tag: str


def _digest(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def release_identity(
    *,
    profile: Literal["core", "extended"],
    sources: Sequence[tuple[str, str]],
    transformation_sha256: str,
    schema_version: str,
    licenses_sha256: str,
) -> ReleaseIdentity:
    """Hash the frozen release-key projection; this neither builds nor activates data.

    Sources are logical-name/content-digest pairs. Acquisition timestamps, local
    paths, workflow IDs and later observations deliberately are not inputs.
    Transformation identity must already cover parser/config/schema contracts;
    license identity must identify the exact canonical rights-evidence bytes.
    Profile inventory and rights approval are separate candidate validation gates.
    """
    if (
        profile not in {"core", "extended"}
        or not _digest(transformation_sha256)
        or not _digest(licenses_sha256)
        or not isinstance(schema_version, str)
        or re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", schema_version)
        is None
        or not sources
        or len(sources) > 10_000
    ):
        raise DataValidationError("Invalid release-key input", subtype="release_key_invalid")
    seen: set[str] = set()
    artifacts: list[dict[str, str]] = []
    for source in sources:
        if not isinstance(source, (tuple, list)) or len(source) != 2:
            raise DataValidationError("Invalid release source pair", subtype="release_key_invalid")
        name, digest = source
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 512
            or name in seen
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
            or not _digest(digest)
        ):
            raise DataValidationError(
                "Invalid release source identity", subtype="release_key_invalid"
            )
        seen.add(name)
        artifacts.append({"logical_name": name, "sha256": digest})
    projection = {
        "profile": profile,
        "sources": sorted(artifacts, key=lambda item: item["logical_name"]),
        "transformation_sha256": transformation_sha256,
        "schema_version": schema_version,
        "licenses_sha256": licenses_sha256,
    }
    try:
        raw = (
            json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    except UnicodeError as exc:
        raise DataValidationError(
            "Invalid release-key Unicode", subtype="release_key_invalid"
        ) from exc
    digest = hashlib.sha256(raw).hexdigest()
    return ReleaseIdentity(raw, f"sha256:{digest}", f"data-clinpgx-{profile}-{digest[:16]}")
