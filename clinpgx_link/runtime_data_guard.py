"""Bind production requests to one verified immutable ClinPGx generation."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases.runtime_identity import verify_runtime_identity

_GENERATION = re.compile(r"versions/([0-9a-f]{64})\Z")
_IDENTITY_FILES = (
    "clinpgx.sqlite",
    "licenses.json",
    "materialization.json",
    "schema.json",
    "source-manifest.json",
    "data-identity-manifest.json",
)


def _signature(path: Path, *, directory: bool = False) -> tuple[int, ...]:
    info = os.lstat(path)
    if directory:
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise OSError("runtime directory changed")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OSError("runtime file changed")
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _directory_identity(path: Path) -> tuple[int, int, int]:
    signature = _signature(path, directory=True)
    return (signature[0], signature[1], signature[2])


def _inventory(path: Path) -> set[str]:
    names: set[str] = set()
    with os.scandir(path) as entries:
        for index, entry in enumerate(entries):
            if index >= len(_IDENTITY_FILES):
                raise OSError("runtime directory inventory changed")
            if entry.name not in _IDENTITY_FILES:
                raise OSError("runtime directory inventory changed")
            names.add(entry.name)
    if names != set(_IDENTITY_FILES):
        raise OSError("runtime directory inventory changed")
    return names


@dataclass(slots=True)
class RuntimeDataGuard:
    """Stat-only integrity guard for the verified tree (never hashes per request)."""

    generation: Path
    release_tag: str
    digest: str
    _directory_signature: tuple[int, ...]
    _file_signatures: dict[str, tuple[int, ...]]
    _data_root_identity: tuple[int, int, int]
    _versions_identity: tuple[int, int, int]

    @classmethod
    def start(
        cls,
        data_root: Path,
        *,
        expected_release_tag: str,
        expected_digest: str,
    ) -> RuntimeDataGuard:
        """Resolve current once, then verify the exact immutable version directory."""
        current = data_root / "current"
        data_root_info = os.lstat(data_root)
        if stat.S_ISLNK(data_root_info.st_mode) or not stat.S_ISDIR(data_root_info.st_mode):
            raise DataValidationError(
                "Installed data generation is unavailable.", subtype="runtime_identity_unsafe"
            )
        if not stat.S_ISLNK(os.lstat(current).st_mode):
            raise DataValidationError(
                "Installed data generation is unavailable.", subtype="runtime_identity_unsafe"
            )
        relative = os.readlink(current)
        match = _GENERATION.fullmatch(relative)
        if match is None:
            raise DataValidationError(
                "Installed data generation is unavailable.", subtype="runtime_identity_unsafe"
            )
        versions = data_root / "versions"
        if stat.S_ISLNK(os.lstat(versions).st_mode) or not stat.S_ISDIR(os.lstat(versions).st_mode):
            raise DataValidationError(
                "Installed data generation is unavailable.", subtype="runtime_identity_unsafe"
            )
        generation = versions / match.group(1)
        generation_info = os.lstat(generation)
        if stat.S_ISLNK(generation_info.st_mode) or not stat.S_ISDIR(generation_info.st_mode):
            raise DataValidationError(
                "Installed data generation is unavailable.", subtype="runtime_identity_unsafe"
            )
        data_root_identity = _directory_identity(data_root)
        versions_identity = _directory_identity(versions)
        initial_directory_signature = _signature(generation, directory=True)
        initial_file_signatures = {name: _signature(generation / name) for name in _IDENTITY_FILES}
        _inventory(generation)
        verified = verify_runtime_identity(
            generation,
            expected_digest=expected_digest,
            expected_release_tag=expected_release_tag,
        )
        if _inventory(generation) != set(_IDENTITY_FILES):
            raise DataValidationError(
                "Installed data generation is unavailable.", subtype="runtime_inventory"
            )
        directory_signature = _signature(generation, directory=True)
        file_signatures = {name: _signature(generation / name) for name in _IDENTITY_FILES}
        if (
            directory_signature != initial_directory_signature
            or file_signatures != initial_file_signatures
            or data_root_identity != _directory_identity(data_root)
            or versions_identity != _directory_identity(versions)
        ):
            raise DataValidationError(
                "Installed data generation changed during admission.",
                subtype="runtime_identity_changed",
            )
        return cls(
            generation=generation,
            release_tag=verified["release_tag"],
            digest=verified["digest"],
            _directory_signature=directory_signature,
            _file_signatures=file_signatures,
            _data_root_identity=data_root_identity,
            _versions_identity=versions_identity,
        )

    @property
    def database_path(self) -> Path:
        return self.generation / "clinpgx.sqlite"

    def is_intact(self) -> bool:
        """Check a fixed bounded inventory using metadata only."""
        try:
            if _inventory(self.generation) != set(_IDENTITY_FILES):
                return False
            if _directory_identity(self.generation.parent.parent) != self._data_root_identity:
                return False
            if _directory_identity(self.generation.parent) != self._versions_identity:
                return False
            if _signature(self.generation, directory=True) != self._directory_signature:
                return False
            return all(
                _signature(self.generation / name) == expected
                for name, expected in self._file_signatures.items()
            )
        except OSError:
            return False

    def health_identity(self) -> dict[str, object]:
        identity = {"release_tag": self.release_tag, "digest": self.digest}
        return {
            "schema_version": 1,
            "data_identity": {"expected": dict(identity), "actual": dict(identity)},
        }
