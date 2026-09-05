"""Bounded, non-extracting acquisition of explicit local source artifacts."""

from __future__ import annotations

import hashlib
import io
import mimetypes
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from clinpgx_link.config import settings
from clinpgx_link.data.catalog import SourceInput
from clinpgx_link.exceptions import DataValidationError


@dataclass(frozen=True)
class ArchiveLimits:
    """Independent compressed, expanded, member, and count admission bounds."""

    max_archive_bytes: int
    max_expanded_bytes: int
    max_member_bytes: int
    max_members: int

    @classmethod
    def from_settings(cls) -> ArchiveLimits:
        return cls(
            max_archive_bytes=settings.max_archive_bytes,
            max_expanded_bytes=settings.max_expanded_archive_bytes,
            max_member_bytes=settings.max_archive_member_bytes,
            max_members=settings.max_archive_members,
        )

    @classmethod
    def for_tests(cls, **overrides: int) -> ArchiveLimits:
        values = {
            "max_archive_bytes": 1024 * 1024,
            "max_expanded_bytes": 4 * 1024 * 1024,
            "max_member_bytes": 2 * 1024 * 1024,
            "max_members": 100,
        }
        unknown = set(overrides) - set(values)
        if unknown:
            raise ValueError(f"Unknown archive limits: {sorted(unknown)}")
        values.update(overrides)
        return cls(**values)


@dataclass(frozen=True)
class AcquiredMember:
    path: str
    raw: bytes
    sha256: str
    byte_count: int
    media_type: str
    compressed_bytes: int
    is_directory: bool


@dataclass(frozen=True)
class AcquiredSource:
    source: SourceInput
    archive_bytes: bytes
    sha256: str
    members: tuple[AcquiredMember, ...]


def _validate_limits(limits: ArchiveLimits) -> None:
    if (
        limits.max_archive_bytes <= 0
        or limits.max_expanded_bytes <= 0
        or limits.max_member_bytes <= 0
        or limits.max_members <= 0
        or limits.max_member_bytes > limits.max_expanded_bytes
    ):
        raise DataValidationError("Archive limits are internally inconsistent")


def _canonical_member_path(value: str, *, is_directory: bool) -> str:
    canonical_value = value[:-1] if is_directory and value.endswith("/") else value
    path = PurePosixPath(canonical_value)
    if (
        not canonical_value
        or value.startswith("/")
        or "\\" in value
        or "." in path.parts
        or ".." in path.parts
        or str(path) != canonical_value
        or value.endswith("/") != is_directory
        or "\x00" in value
    ):
        raise DataValidationError("Archive contains an unsafe or noncanonical member path")
    return value


def _media_type(path: str, *, is_directory: bool) -> str:
    if is_directory:
        return "application/x-directory"
    explicit = {
        ".json": "application/json",
        ".tsv": "text/tab-separated-values",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".owl": "application/rdf+xml",
    }
    suffix = PurePosixPath(path).suffix.lower()
    return explicit.get(suffix) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, max_member_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        with archive.open(info, "r") as stream:
            while chunk := stream.read(min(1024 * 1024, max_member_bytes + 1 - total)):
                total += len(chunk)
                if total > max_member_bytes:
                    raise DataValidationError("Archive member exceeds its expanded byte limit")
                chunks.append(chunk)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise DataValidationError("Archive member could not be decoded") from exc
    raw = b"".join(chunks)
    if len(raw) != info.file_size:
        raise DataValidationError("Archive member size differs from its directory entry")
    return raw


def read_local_source(
    source: SourceInput, *, limits: ArchiveLimits | None = None
) -> AcquiredSource:
    """Verify and inspect a ZIP in memory without extracting to the filesystem."""
    configured = limits or ArchiveLimits.from_settings()
    _validate_limits(configured)
    raw = source.read_verified(max_bytes=configured.max_archive_bytes)
    if len(raw) > configured.max_archive_bytes:
        raise DataValidationError("Source archive exceeds its compressed byte limit")
    if source.media_type != "application/zip" or not zipfile.is_zipfile(io.BytesIO(raw)):
        raise DataValidationError("Source declared as ZIP does not have a valid ZIP signature")
    members: list[AcquiredMember] = []
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            infos = archive.infolist()
            if len(infos) > configured.max_members:
                raise DataValidationError("Archive exceeds its member-count limit")
            total_expanded = 0
            for info in infos:
                is_directory = info.is_dir()
                path = _canonical_member_path(info.filename, is_directory=is_directory)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or info.flag_bits & 0x1:
                    raise DataValidationError("Archive contains a link or encrypted member")
                if path in seen:
                    raise DataValidationError("Archive contains duplicate member paths")
                seen.add(path)
                if info.file_size > configured.max_member_bytes:
                    raise DataValidationError("Archive member exceeds its expanded byte limit")
                total_expanded += info.file_size
                if total_expanded > configured.max_expanded_bytes:
                    raise DataValidationError("Archive exceeds its total expanded byte limit")
                member_raw = _read_member(archive, info, configured.max_member_bytes)
                members.append(
                    AcquiredMember(
                        path=path,
                        raw=member_raw,
                        sha256=hashlib.sha256(member_raw).hexdigest(),
                        byte_count=len(member_raw),
                        media_type=_media_type(path, is_directory=is_directory),
                        compressed_bytes=info.compress_size,
                        is_directory=is_directory,
                    )
                )
    except zipfile.BadZipFile as exc:
        raise DataValidationError("Source ZIP central directory is invalid") from exc
    return AcquiredSource(
        source=source,
        archive_bytes=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        members=tuple(sorted(members, key=lambda member: member.path)),
    )


__all__ = ["AcquiredMember", "AcquiredSource", "ArchiveLimits", "read_local_source"]
