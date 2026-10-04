"""Offline operator entrypoint for independently pinned data releases."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from clinpgx_link import __version__
from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases.bundle import BundleLimits
from clinpgx_link.releases.manifest import MAX_MANIFEST_BYTES, CompatibilityRange
from clinpgx_link.releases.materialize import MaterializationReceipt, ReleaseInput, install_release

_MIB = 1024 * 1024
_GIB = 1024 * _MIB


def application_version() -> str:
    """Use the installed application's immutable package version, never an operator override."""
    return __version__


def _manifest_bytes(path: Path) -> bytes:
    if not path.is_absolute():
        raise DataValidationError("Manifest path must be absolute")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise DataValidationError("Manifest input must be a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise DataValidationError("Manifest exceeds byte limit")
        return raw
    except OSError as exc:
        raise DataValidationError("Manifest input is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def install_offline(
    *,
    manifest_path: Path,
    manifest_sha256: str,
    artifact_path: Path,
    data_root: Path,
    previous_manifest_path: Path | None = None,
    previous_manifest_sha256: str | None = None,
    previous_artifact_path: Path | None = None,
) -> MaterializationReceipt:
    """Install local bytes through the native manifest, rights, and bundle verifier."""
    if (previous_manifest_path is None) != (previous_manifest_sha256 is None):
        raise DataValidationError("Previous manifest path and digest must be supplied together")
    if previous_artifact_path is not None and previous_manifest_path is None:
        raise DataValidationError("Previous artifact requires an independently pinned manifest")
    if not artifact_path.is_absolute():
        raise DataValidationError("Artifact path must be absolute")
    if previous_artifact_path is not None and not previous_artifact_path.is_absolute():
        raise DataValidationError("Previous artifact path must be absolute")
    candidate = ReleaseInput(_manifest_bytes(manifest_path), manifest_sha256, artifact_path)
    previous = (
        ReleaseInput(
            _manifest_bytes(previous_manifest_path),
            previous_manifest_sha256,
            previous_artifact_path,
        )
        if previous_manifest_path is not None and previous_manifest_sha256 is not None
        else None
    )
    return install_release(
        candidate,
        previous=previous,
        data_root=data_root,
        application_version=application_version(),
        supported_schema=CompatibilityRange(minimum="1.0.0", maximum="1.0.0"),
        limits=BundleLimits(
            max_compressed_bytes=256 * _MIB,
            max_expanded_bytes=2 * _GIB,
            max_member_bytes=2 * _GIB,
        ),
    )
