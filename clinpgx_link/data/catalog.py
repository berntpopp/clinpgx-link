"""Immutable catalog and local source-input contracts for ClinPGx exports."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from clinpgx_link.exceptions import DataValidationError, InvalidInputError, NotFoundError

CatalogTier = Literal[
    "canonical_page", "approved_registry", "experimental_or_legacy", "related_project"
]

_TIERS = frozenset(
    {"canonical_page", "approved_registry", "experimental_or_legacy", "related_project"}
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RELEASE_TAG = re.compile(r"data-clinpgx-(?:core|extended)-[0-9a-f]{16}\Z")
_ALLOWED_SOURCE_HOSTS = frozenset({"api.clinpgx.org", "s3.pgkb.org"})
_FILE_CHUNK_BYTES = 1024 * 1024


def _file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_FILE_CHUNK_BYTES):
                total += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise InvalidInputError("Source file cannot be read", field="path") from exc
    return digest.hexdigest(), total


def _validate_timestamp(value: str, *, require_utc: bool = False) -> None:
    if not isinstance(value, str) or not value or (require_utc and not value.endswith("Z")):
        raise InvalidInputError("Acquisition time must be canonical UTC", field="retrieved_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidInputError("Timestamp must be ISO 8601", field="timestamp") from exc
    if parsed.tzinfo is None:
        raise InvalidInputError("Timestamp must include an offset", field="timestamp")


def _validate_dataset_id(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidInputError("Dataset ID must be a canonical data/ path", field="dataset_id")
    path = PurePosixPath(value)
    if (
        not value.startswith("data/")
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or "\\" in value
        or str(path) != value
        or path.name == ""
    ):
        raise InvalidInputError("Dataset ID must be a canonical data/ path", field="dataset_id")


def _validate_source_url(value: str, dataset_id: str) -> None:
    if not isinstance(value, str):
        raise InvalidInputError("Source URL is outside the ClinPGx allowlist", field="source_url")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_SOURCE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise InvalidInputError("Source URL is outside the ClinPGx allowlist", field="source_url")
    expected_path = (
        f"/v1/download/file/{dataset_id}"
        if parsed.hostname == "api.clinpgx.org"
        else f"/{dataset_id}"
    )
    if parsed.path != expected_path:
        raise InvalidInputError("Source URL does not match its dataset identity", field="source_url")


def is_canonical_release_tag(value: object) -> bool:
    """Return whether a candidate tag is accepted by the production release contract."""
    return isinstance(value, str) and _RELEASE_TAG.fullmatch(value) is not None


@dataclass(frozen=True)
class CatalogEntry:
    """One discovered registry export without fabricated rights or usability claims."""

    dataset_id: str
    file_name: str
    source_date: str
    reported_size: int
    status: str
    license_tier: str
    distribution_allowed: bool
    limitations: tuple[str, ...]
    evidence: tuple[str, ...]


class DownloadCatalog:
    """Validated join of the captured download registry and coverage ledger."""

    def __init__(self, entries: tuple[CatalogEntry, ...]) -> None:
        self._entries = entries
        self._by_id = {entry.dataset_id: entry for entry in entries}

    @classmethod
    def load(cls, registry_path: Path, coverage_path: Path) -> DownloadCatalog:
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataValidationError("Catalog evidence cannot be decoded") from exc
        registry_rows = registry.get("files")
        coverage_rows = coverage.get("datasets")
        if not isinstance(registry_rows, list) or not isinstance(coverage_rows, list):
            raise DataValidationError("Catalog evidence has an invalid shape")
        covered: dict[str, dict[str, object]] = {}
        for row in coverage_rows:
            if not isinstance(row, dict) or not isinstance(row.get("dataset_id"), str):
                raise DataValidationError("Coverage ledger entry must have a dataset identity")
            coverage_id = row["dataset_id"]
            if coverage_id in covered:
                raise DataValidationError("Coverage ledger contains a duplicate dataset identity")
            covered[coverage_id] = row
        entries: list[CatalogEntry] = []
        seen: set[str] = set()
        for row in registry_rows:
            if not isinstance(row, dict):
                raise DataValidationError("Registry entry must be an object")
            dataset_id = row.get("path")
            file_name = row.get("fileName")
            source_date = row.get("lastModified")
            reported_size = row.get("size")
            if (
                not isinstance(dataset_id, str)
                or not isinstance(file_name, str)
                or not isinstance(source_date, str)
                or type(reported_size) is not int
                or reported_size < 0
            ):
                raise DataValidationError("Registry entry is missing identity metadata")
            _validate_dataset_id(dataset_id)
            _validate_timestamp(source_date)
            if dataset_id in seen or PurePosixPath(dataset_id).name != file_name:
                raise DataValidationError("Registry contains a duplicate or inconsistent path")
            seen.add(dataset_id)
            ledger = covered.get(dataset_id)
            if not isinstance(ledger, dict):
                raise DataValidationError("Coverage ledger omits a registry entry")
            limitations = ledger.get("limitations")
            evidence = ledger.get("evidence")
            if not isinstance(limitations, list) or not all(
                isinstance(item, str) for item in limitations
            ):
                raise DataValidationError("Coverage limitations must be strings")
            if not isinstance(evidence, list) or not all(
                isinstance(item, str) for item in evidence
            ):
                raise DataValidationError("Coverage evidence must be strings")
            entries.append(
                CatalogEntry(
                    dataset_id=dataset_id,
                    file_name=file_name,
                    source_date=source_date,
                    reported_size=reported_size,
                    status=str(ledger.get("status", "published_unassessed")),
                    license_tier="needs_review",
                    distribution_allowed=False,
                    limitations=tuple(limitations),
                    evidence=tuple(evidence),
                )
            )
        if set(covered) != seen:
            raise DataValidationError("Registry and coverage dataset identities differ")
        return cls(tuple(sorted(entries, key=lambda item: item.dataset_id)))

    def list_entries(self) -> tuple[CatalogEntry, ...]:
        return self._entries

    def get(self, dataset_id: str) -> CatalogEntry:
        try:
            return self._by_id[dataset_id]
        except KeyError as exc:
            raise NotFoundError("Dataset is not present in the captured catalog") from exc


@dataclass(frozen=True)
class SourceInput:
    """Frozen acquisition receipt for one explicit local build input."""

    dataset_id: str
    file_name: str
    path: Path
    source_url: str
    retrieved_at: str
    published_at: str | None
    sha256: str
    byte_count: int
    media_type: str
    license_id: str
    tier: CatalogTier
    etag: str | None = None
    last_modified: str | None = None
    version_id: str | None = None

    def __post_init__(self) -> None:
        _validate_dataset_id(self.dataset_id)
        expected_name = PurePosixPath(self.dataset_id).name
        if self.file_name != expected_name:
            raise InvalidInputError("Source filename does not match its dataset", field="file_name")
        _validate_source_url(self.source_url, self.dataset_id)
        _validate_timestamp(self.retrieved_at, require_utc=True)
        if self.published_at is not None:
            _validate_timestamp(self.published_at)
        if self.tier not in _TIERS:
            raise InvalidInputError("Unsupported catalog tier", field="tier")
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise InvalidInputError("Source path must be absolute", field="path")
        if not _SHA256.fullmatch(self.sha256):
            raise InvalidInputError("Source receipt digest is not canonical", field="sha256")
        if type(self.byte_count) is not int or self.byte_count < 0:
            raise InvalidInputError("Source receipt byte count is invalid", field="byte_count")
        if self.media_type != "application/zip":
            raise InvalidInputError("Snapshot source must be a ZIP archive", field="media_type")
        if not isinstance(self.license_id, str) or not self.license_id or len(self.license_id) > 256:
            raise InvalidInputError("Source license identifier is invalid", field="license_id")
        for value in (self.etag, self.last_modified, self.version_id):
            if value is not None and (not isinstance(value, str) or len(value) > 4096):
                raise InvalidInputError("Source version metadata is invalid", field="source_metadata")

    @classmethod
    def from_path(
        cls,
        *,
        dataset_id: str,
        path: Path,
        source_url: str,
        retrieved_at: str,
        published_at: str | None,
        media_type: str,
        license_id: str,
        tier: str,
        etag: str | None = None,
        last_modified: str | None = None,
        version_id: str | None = None,
    ) -> SourceInput:
        _validate_dataset_id(dataset_id)
        _validate_source_url(source_url, dataset_id)
        _validate_timestamp(retrieved_at, require_utc=True)
        if published_at is not None:
            _validate_timestamp(published_at)
        if tier not in _TIERS:
            raise InvalidInputError("Unsupported catalog tier", field="tier")
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise InvalidInputError("Source path must be an absolute regular file", field="path")
        digest, byte_count = _file_digest(path)
        return cls(
            dataset_id=dataset_id,
            file_name=PurePosixPath(dataset_id).name,
            path=path,
            source_url=source_url,
            retrieved_at=retrieved_at,
            published_at=published_at,
            sha256=digest,
            byte_count=byte_count,
            media_type=media_type,
            license_id=license_id,
            tier=tier,  # type: ignore[arg-type]
            etag=etag,
            last_modified=last_modified,
            version_id=version_id,
        )

    def read_verified(self, *, max_bytes: int | None = None) -> bytes:
        if not _SHA256.fullmatch(self.sha256):
            raise DataValidationError("Source receipt has an invalid digest")
        if self.path.is_symlink() or not self.path.is_file():
            raise DataValidationError("Source path no longer names the acquired regular file")
        if max_bytes is not None and (
            isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0
        ):
            raise DataValidationError("Source read limit is invalid")
        try:
            if max_bytes is not None and self.path.stat().st_size > max_bytes:
                raise DataValidationError("Source archive exceeds its compressed byte limit")
            chunks: list[bytes] = []
            total = 0
            with self.path.open("rb") as stream:
                while chunk := stream.read(
                    _FILE_CHUNK_BYTES
                    if max_bytes is None
                    else min(_FILE_CHUNK_BYTES, max_bytes + 1 - total)
                ):
                    total += len(chunk)
                    if max_bytes is not None and total > max_bytes:
                        raise DataValidationError(
                            "Source archive exceeds its compressed byte limit"
                        )
                    chunks.append(chunk)
        except OSError as exc:
            raise DataValidationError("Source file cannot be read") from exc
        raw = b"".join(chunks)
        if len(raw) != self.byte_count or hashlib.sha256(raw).hexdigest() != self.sha256:
            raise DataValidationError("Local source bytes changed after acquisition")
        return raw


__all__ = [
    "CatalogEntry",
    "CatalogTier",
    "DownloadCatalog",
    "SourceInput",
    "is_canonical_release_tag",
]
