"""Derive the canonical source manifest from a frozen acquisition and a built snapshot."""

from __future__ import annotations

import re
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases.acquisition import AcquisitionReceipt
from clinpgx_link.releases.authorization import LICENSE_ID
from clinpgx_link.releases.contract_json import canonical_value_bytes
from clinpgx_link.releases.source_manifest import canonical_bytes, parse_source_manifest

_METADATA_ONLY = {
    "pathways-biopax.zip": "BioPAX files are retained metadata-only per the approved core design."
}
_CREATED = re.compile(r"Created on (\d{2}/\d{2}/\d{4}) at (\d{2}:\d{2}:\d{2}) (PDT|PST)\.?\s*")
# ClinPGx stamps US Pacific wall time; the abbreviation fixes the offset exactly.
_OFFSETS = {"PDT": timedelta(hours=-7), "PST": timedelta(hours=-8)}


@dataclass(frozen=True)
class ArchiveEvidence:
    notice: str
    marker: str
    created_at: str


def inspect_archive(path: Path) -> ArchiveEvidence:
    """Read the one embedded LICENSE.txt and CREATED_ marker; anything else fails loudly."""
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise DataValidationError(f"{path.name}: archive member CRC failure")
        names = archive.namelist()
        notices = [name for name in names if Path(name).name.upper() == "LICENSE.TXT"]
        markers = [name for name in names if Path(name).name.startswith("CREATED_")]
        if len(notices) != 1 or len(markers) != 1:
            raise DataValidationError(f"{path.name}: notice or creation marker inventory changed")
        notice = archive.read(notices[0]).decode("utf-8")
        text = archive.read(markers[0]).decode("utf-8")
    match = _CREATED.fullmatch(text)
    if match is None:
        raise DataValidationError(f"{path.name}: creation marker cannot be parsed")
    wall = datetime.strptime(f"{match[1]} {match[2]}", "%m/%d/%Y %H:%M:%S")
    created = wall.replace(tzinfo=timezone(_OFFSETS[match[3]])).isoformat()
    return ArchiveEvidence(notice=notice, marker=markers[0], created_at=created)


def _members(database: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, int]]]:
    connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    try:
        rows = connection.execute(
            "SELECT dataset_id,path,byte_count,compressed_bytes,sha256,parser_status "
            "FROM source_member WHERE is_directory=0 ORDER BY dataset_id,path"
        ).fetchall()
        sums = connection.execute(
            "SELECT dataset_id,media_type,sum(record_count) FROM source_member "
            "GROUP BY dataset_id,media_type"
        ).fetchall()
    finally:
        connection.close()
    members: dict[str, list[dict[str, Any]]] = {}
    for dataset_id, path, size, compressed, digest, status in rows:
        members.setdefault(dataset_id, []).append(
            {
                "path": path,
                "compressed_size": compressed,
                "uncompressed_size": size,
                "sha256": digest,
                "consumed": status == "indexed",
                "indexed": status == "indexed",
            }
        )
    imported: dict[str, dict[str, int]] = {}
    for dataset_id, media_type, count in sums:
        target = "documents" if media_type == "application/json" else "rows"
        imported.setdefault(dataset_id, {"rows": 0, "documents": 0})[target] += count or 0
    return members, imported


def render_source_manifest(
    *,
    receipt: AcquisitionReceipt,
    evidence: dict[str, ArchiveEvidence],
    built_manifest: dict[str, Any],
    database: Path,
    source_set_identity: str,
    transformation_sha256: str,
    revision: str,
    parser_version: str,
) -> bytes:
    receipts = {source.dataset_id: source for source in receipt.sources}
    members, imported = _members(database)
    artifacts = []
    anomalies = []
    for built in built_manifest["artifacts"]:
        source = receipts[built["dataset_id"]]
        name = source.file_name
        indexed = [member for member in members.get(source.dataset_id, []) if member["indexed"]]
        if name not in _METADATA_ONLY and not indexed:
            raise DataValidationError(f"{name}: no member was indexed")
        if "upstream_anomaly:genes_is_vip_all_yes" in built["warnings"]:
            anomalies.append(
                {
                    "anomaly_id": "broken_gene_vip_flag",
                    "affected_sources": [source.dataset_id],
                    "count": built["record_count"],
                }
            )
        artifacts.append(
            {
                "logical_name": source.dataset_id,
                "registry_filename": name,
                "tier": "canonical_page",
                "acquisition_url": source.acquisition_url,
                "final_url": source.final_url,
                "registry_last_modified": source.registry_last_modified,
                "registry_size": source.registry_size,
                "http_last_modified": source.http_last_modified,
                "http_etag": source.http_etag,
                "http_version_id": source.http_version_id,
                "http_content_length": source.http_content_length,
                "sha256": source.sha256,
                "byte_count": source.byte_count,
                "retrieved_at": source.retrieved_at,
                "embedded_marker": evidence[name].marker,
                "embedded_created_at": evidence[name].created_at,
                "members": members.get(source.dataset_id, []),
                "parser": None,
                "transformation_sha256": transformation_sha256,
                "status": "metadata_only" if name in _METADATA_ONLY else "indexed",
                "reason": _METADATA_ONLY.get(name),
                "imported_counts": imported.get(source.dataset_id, {"rows": 0, "documents": 0}),
                "rejected_rows": 0,
                "validation_result": (
                    "passed_with_warnings"
                    if built["limitations"] or built["warnings"]
                    else "passed"
                ),
                "license_id": LICENSE_ID,
                "coverage": [],
            }
        )
    registry = receipt.registry
    value = {
        "schema_version": 1,
        "profile": "core",
        "source_set_identity": source_set_identity,
        "transformation": {
            "sha256": transformation_sha256,
            "revision": revision,
            "schema_version": "1.0.0",
        },
        "registry": {
            "url": registry.url,
            "retrieved_at": registry.retrieved_at,
            "sha256": registry.sha256,
            "etag": registry.etag,
            "last_modified": registry.last_modified,
            "parser_version": parser_version,
        },
        "artifacts": artifacts,
        "coverage": [],
        "anomalies": anomalies,
    }
    return canonical_bytes(parse_source_manifest(canonical_value_bytes(value)))


__all__ = ["ArchiveEvidence", "inspect_archive", "render_source_manifest"]
