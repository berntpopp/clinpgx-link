"""Frozen acquisition receipts for the ClinPGx core profile.

An acquisition receipt records exactly which official archives were downloaded, when,
and with which HTTP identity. The builder consumes a receipt plus its archive bytes and
never touches the network, so the same receipt always rebuilds the same release.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
from pydantic import Field, ValidationError

from clinpgx_link.config import Settings
from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases.acquire import SourceDownloader
from clinpgx_link.releases.contract_json import (
    HttpsUrl,
    NonNegativeInt,
    SchemaVersion,
    Sha256Hex,
    ShortText,
    StrictModel,
    Timestamp,
)

REGISTRY_URL = "https://api.clinpgx.org/v1/data/file/data/?view=min"
RECEIPT_NAME = "acquisition.json"
SOURCES_DIRECTORY = "sources"
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_REGISTRY_BYTES = 4 * 1024 * 1024
CORE_ARCHIVES = (
    "chemicals.zip",
    "clinicalVariants.zip",
    "drugLabels.zip",
    "drugs.zip",
    "genes.zip",
    "guidelineAnnotations.json.zip",
    "occurrences.zip",
    "pathways-biopax.zip",
    "pathways-tsv.zip",
    "pathways.json.zip",
    "phenotypes.zip",
    "relationships.zip",
    "summaryAnnotations.zip",
    "variantAnnotations.zip",
    "variants.zip",
)
FileName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.zip$")]


class RegistryObservation(StrictModel):
    url: HttpsUrl
    retrieved_at: Timestamp
    sha256: Sha256Hex
    etag: ShortText | None
    last_modified: ShortText | None


class SourceReceipt(StrictModel):
    file_name: FileName
    dataset_id: ShortText
    registry_last_modified: Timestamp
    registry_size: NonNegativeInt
    acquisition_url: HttpsUrl
    final_url: HttpsUrl
    retrieved_at: Timestamp
    http_etag: ShortText | None
    http_last_modified: ShortText | None
    http_version_id: ShortText | None
    http_content_length: NonNegativeInt | None
    byte_count: NonNegativeInt
    sha256: Sha256Hex
    media_type: ShortText


class AcquisitionReceipt(StrictModel):
    schema_version: SchemaVersion
    registry: RegistryObservation
    sources: tuple[SourceReceipt, ...]


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def load_acquisition(directory: Path) -> AcquisitionReceipt:
    """Admit a frozen receipt only when every archive still has its recorded bytes."""
    raw = (directory / RECEIPT_NAME).read_bytes()
    if len(raw) > MAX_RECEIPT_BYTES:
        raise DataValidationError("Acquisition receipt exceeds its byte bound")
    try:
        receipt = AcquisitionReceipt.model_validate_json(raw)
    except ValidationError as exc:
        raise DataValidationError("Acquisition receipt is invalid") from exc
    names = [source.file_name for source in receipt.sources]
    if sorted(names) != sorted(CORE_ARCHIVES) or len(set(names)) != len(names):
        raise DataValidationError("Acquisition inventory is not the exact core profile")
    for source in receipt.sources:
        path = directory / SOURCES_DIRECTORY / source.file_name
        if path.is_symlink() or not path.is_file():
            raise DataValidationError(f"Acquired archive is missing: {source.file_name}")
        if _sha256_file(path) != (source.sha256, source.byte_count):
            raise DataValidationError(f"Acquired archive bytes changed: {source.file_name}")
        if source.dataset_id != f"data/{source.file_name}":
            raise DataValidationError(f"Dataset ID does not match file: {source.file_name}")
    return receipt


async def _registry(
    client: httpx.AsyncClient,
) -> tuple[RegistryObservation, dict[str, dict[str, Any]]]:
    response = await client.get(REGISTRY_URL, headers={"Accept": "application/json"})
    response.raise_for_status()
    raw = response.content
    if len(raw) > MAX_REGISTRY_BYTES:
        raise DataValidationError("Download registry exceeds its byte bound")
    rows = {row["fileName"]: row for row in json.loads(raw)["data"]}
    missing = [name for name in CORE_ARCHIVES if name not in rows]
    if missing:
        raise DataValidationError(f"Core archives missing from the registry: {missing}")
    observation = RegistryObservation(
        url=REGISTRY_URL,
        retrieved_at=_utc(datetime.now(UTC).replace(microsecond=0)),
        sha256=hashlib.sha256(raw).hexdigest(),
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
    )
    return observation, rows


async def acquire_core(
    directory: Path, settings: Settings, *, client: httpx.AsyncClient | None = None
) -> AcquisitionReceipt:
    """Download the 15 core archives into an empty private directory and freeze a receipt."""
    sources_dir = directory / SOURCES_DIRECTORY
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise DataValidationError("Acquisition directory must be empty")
    sources_dir.mkdir(mode=0o700)
    async with client or httpx.AsyncClient(
        follow_redirects=False, trust_env=False, timeout=settings.request_timeout_seconds
    ) as client:
        registry, rows = await _registry(client)
        downloader = SourceDownloader(settings, client=client)
        receipts = []
        for name in CORE_ARCHIVES:
            row = rows[name]
            got = await downloader.download(row["path"], sources_dir / name)
            if got.byte_count != row["size"] or got.media_type != "application/zip":
                raise DataValidationError(f"Registry size or media type mismatch: {name}")
            receipts.append(
                SourceReceipt(
                    file_name=name,
                    dataset_id=row["path"],
                    registry_last_modified=row["lastModified"],
                    registry_size=row["size"],
                    acquisition_url=got.original_url,
                    final_url=got.final_url,
                    retrieved_at=_utc(got.retrieved_at),
                    http_etag=got.etag,
                    http_last_modified=got.last_modified,
                    http_version_id=got.version_id,
                    http_content_length=got.content_length,
                    byte_count=got.byte_count,
                    sha256=got.sha256,
                    media_type=got.media_type,
                )
            )
    receipt = AcquisitionReceipt(schema_version=1, registry=registry, sources=tuple(receipts))
    raw = (json.dumps(receipt.model_dump(mode="json"), indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(directory / RECEIPT_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
    return load_acquisition(directory)


__all__ = [
    "CORE_ARCHIVES",
    "AcquisitionReceipt",
    "SourceReceipt",
    "acquire_core",
    "load_acquisition",
]
