"""Canonical materialization sidecar and fixed SQLite semantic verification."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from pydantic import ConfigDict, Field

from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases.contract_json import (
    NonNegativeInt,
    SchemaVersion,
    SemanticVersion,
    Sha256Hex,
    Sha256Identity,
    StrictModel,
    parse_canonical,
)
from clinpgx_link.releases.contract_json import (
    canonical_bytes as _canonical_bytes,
)
from clinpgx_link.releases.licenses import (
    LicensesManifest,
    parse_licenses,
    validate_source_identity,
)
from clinpgx_link.releases.manifest import DataReleaseManifest
from clinpgx_link.releases.schema import DatabaseSchema, parse_database_schema
from clinpgx_link.releases.source_manifest import SourceManifest, parse_source_manifest

MAX_MATERIALIZATION_BYTES = 2 * 1024 * 1024
_CHUNK = 1024 * 1024
_BUNDLE_FILES = ("clinpgx.sqlite", "licenses.json", "schema.json", "source-manifest.json")


class Materialization(StrictModel):
    model_config = ConfigDict(**StrictModel.model_config)

    schema_version: SchemaVersion
    outer_manifest_text: str = Field(min_length=1, max_length=1024 * 1024)
    outer_manifest_sha256: Sha256Hex
    artifact_sha256: Sha256Hex
    expanded_tree_sha256: Sha256Hex
    expanded_size: NonNegativeInt
    member_count: NonNegativeInt
    database_schema_version: SemanticVersion
    schema_minimum: SemanticVersion
    schema_maximum: SemanticVersion
    release_tag: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    previous_known_good_digest: Sha256Identity
    source_set_identity: Sha256Identity
    snapshot_id: Sha256Identity


@dataclass(frozen=True)
class SemanticReceipt:
    schema: DatabaseSchema
    source: SourceManifest
    snapshot_id: str


def canonical_bytes(model: Materialization) -> bytes:
    return _canonical_bytes(model)


def parse_materialization(raw: bytes) -> Materialization:
    return parse_canonical(raw, maximum=MAX_MATERIALIZATION_BYTES, model=Materialization)


def _read_fixed(root: Path, name: str, maximum: int) -> bytes:
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=root_fd,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o444
        ):
            raise DataValidationError("Generation file is not immutable and regular")
        if info.st_size > maximum:
            raise DataValidationError(
                "Generation file exceeds its byte limit", subtype="resource_limit"
            )
        body = bytearray()
        while raw := os.read(descriptor, min(_CHUNK, maximum + 1 - len(body))):
            body.extend(raw)
            if len(body) > maximum:
                raise DataValidationError(
                    "Generation file exceeds its byte limit", subtype="resource_limit"
                )
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or (after.st_dev, after.st_ino) != (named.st_dev, named.st_ino):
            raise DataValidationError("Generation file changed during verification")
        return bytes(body)
    except OSError as exc:
        raise DataValidationError("Generation file cannot be admitted") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(root_fd)


def read_immutable(root: Path, name: str, maximum: int) -> bytes:
    if name not in {*_BUNDLE_FILES, "materialization.json", "data-identity-manifest.json"}:
        raise DataValidationError("Unknown generation file")
    return _read_fixed(root, name, maximum)


def _blob_digest(connection: sqlite3.Connection, table: str, rowid: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with connection.blobopen(table, "raw", rowid, readonly=True) as blob:
        while raw := blob.read(_CHUNK):
            total += len(raw)
            digest.update(raw)
    return total, digest.hexdigest()


def _metadata(connection: sqlite3.Connection, key: str) -> str:
    cursor = connection.execute("SELECT value FROM metadata WHERE key=?", (key,))
    row = cursor.fetchone()
    if row is None or cursor.fetchone() is not None or not isinstance(row[0], str):
        raise DataValidationError("SQLite release metadata is invalid")
    return row[0]


def _validate_rights_mapping(source: SourceManifest, rights: LicensesManifest) -> None:
    artifacts = {item.logical_name: item.license_id for item in source.artifacts}
    license_ids = {item.license_id for item in rights.licenses}
    affected: dict[str, str] = {}
    for license_record in rights.licenses:
        for name in license_record.affected_artifacts:
            if name not in artifacts or name in affected:
                raise DataValidationError(
                    "Rights evidence does not map exactly to retained artifacts",
                    subtype="rights_invalid",
                )
            affected[name] = license_record.license_id
    if any(
        license_id not in license_ids or affected.get(name) != license_id
        for name, license_id in artifacts.items()
    ):
        raise DataValidationError(
            "Rights evidence does not map exactly to retained artifacts",
            subtype="rights_invalid",
        )


def _verify_sqlite(
    root: Path, manifest: DataReleaseManifest, schema: DatabaseSchema, source: SourceManifest
) -> str:
    database = root / "clinpgx.sqlite"
    before = database.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o444
    ):
        raise DataValidationError("SQLite snapshot is not immutable and regular")
    descriptor = os.open(database, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    opened = os.fstat(descriptor)
    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
        os.close(descriptor)
        raise DataValidationError("SQLite snapshot changed during admission")
    uri = f"file:{quote(f'/proc/self/fd/{descriptor}', safe='/')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA trusted_schema=OFF")
        quick = connection.execute("PRAGMA quick_check")
        if quick.fetchone() != ("ok",) or quick.fetchone() is not None:
            raise DataValidationError("SQLite quick_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DataValidationError("SQLite foreign keys are invalid")
        if (
            connection.execute("PRAGMA application_id").fetchone()[0]
            != schema.sqlite_application_id
        ):
            raise DataValidationError("SQLite application identity is invalid")
        if connection.execute("PRAGMA user_version").fetchone()[0] != schema.sqlite_user_version:
            raise DataValidationError("SQLite user version is invalid")
        counts = {
            "dataset": connection.execute("SELECT count(*) FROM dataset").fetchone()[0],
            "record": connection.execute("SELECT count(*) FROM record").fetchone()[0],
            "source_archive": connection.execute("SELECT count(*) FROM source_archive").fetchone()[
                0
            ],
            "source_member": connection.execute("SELECT count(*) FROM source_member").fetchone()[0],
            "membership": connection.execute("SELECT count(*) FROM membership").fetchone()[0],
        }
        if counts != schema.record_counts.model_dump() or counts != manifest.record_counts:
            raise DataValidationError("SQLite record counts do not match contracts")
        if _metadata(connection, "release_tag") != manifest.dataset.release:
            raise DataValidationError("SQLite release tag does not match")
        snapshot = _metadata(connection, "snapshot_id")
        if not snapshot.startswith("sha256:") or len(snapshot) != 71:
            raise DataValidationError("SQLite snapshot identity is invalid")
        expected = [
            (
                a.logical_name,
                a.sha256,
                a.byte_count,
                a.license_id,
                a.imported_counts.rows + a.imported_counts.documents,
            )
            for a in source.artifacts
        ]
        datasets = connection.execute(
            "SELECT dataset_id,sha256,byte_count,license_id,record_count "
            "FROM dataset ORDER BY dataset_id"
        )
        for expected_row in expected:
            if datasets.fetchone() != expected_row:
                raise DataValidationError("SQLite datasets do not match retained provenance")
        if datasets.fetchone() is not None:
            raise DataValidationError("SQLite datasets do not match retained provenance")
        for artifact in source.artifacts:
            archives = connection.execute(
                "SELECT rowid,byte_count,sha256 FROM source_archive WHERE dataset_id=?",
                (artifact.logical_name,),
            )
            archive = archives.fetchone()
            if (
                archive is None
                or archives.fetchone() is not None
                or archive[1:] != (artifact.byte_count, artifact.sha256)
            ):
                raise DataValidationError("SQLite archive provenance does not match")
            if _blob_digest(connection, "source_archive", archive[0]) != (
                artifact.byte_count,
                artifact.sha256,
            ):
                raise DataValidationError("SQLite archive bytes do not match")
            members = connection.execute(
                "SELECT rowid,path,byte_count,compressed_bytes,sha256,parser_status "
                "FROM source_member WHERE dataset_id=? ORDER BY path",
                (artifact.logical_name,),
            )
            for member in artifact.members:
                row = members.fetchone()
                if row is None:
                    raise DataValidationError("SQLite member inventory does not match")
                consumed = row[5] in {"indexed", "quarantined"}
                indexed = row[5] == "indexed"
                if row[5] not in {"indexed", "quarantined", "preserved"}:
                    raise DataValidationError("SQLite member parser status is invalid")
                if row[1:5] != (
                    member.path,
                    member.uncompressed_size,
                    member.compressed_size,
                    member.sha256,
                ) or (consumed, indexed) != (member.consumed, member.indexed):
                    raise DataValidationError("SQLite member provenance does not match")
                if _blob_digest(connection, "source_member", row[0]) != (
                    member.uncompressed_size,
                    member.sha256,
                ):
                    raise DataValidationError("SQLite member bytes do not match")
            if members.fetchone() is not None:
                raise DataValidationError("SQLite member inventory does not match")
        return snapshot
    except sqlite3.Error as exc:
        raise DataValidationError("SQLite semantic validation failed") from exc
    finally:
        connection.close()
        os.close(descriptor)
        after = database.lstat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise DataValidationError("SQLite snapshot changed during verification")


def validate_semantics(root: Path, manifest: DataReleaseManifest) -> SemanticReceipt:
    schema = parse_database_schema(_read_fixed(root, "schema.json", 64 * 1024))
    source_raw = _read_fixed(root, "source-manifest.json", 4 * 1024 * 1024)
    rights_raw = _read_fixed(root, "licenses.json", 1024 * 1024)
    source = parse_source_manifest(source_raw)
    rights = parse_licenses(rights_raw)
    _validate_rights_mapping(source, rights)
    validate_source_identity(source, rights)
    if hashlib.sha256(source_raw).hexdigest() != str(manifest.dataset.source.sha256):
        raise DataValidationError("Outer source digest does not match")
    if manifest.dataset.source.identifier != f"clinpgx-{source.profile}-source-set-v1":
        raise DataValidationError("Outer source identity is invalid")
    if str(manifest.dataset.source.url) != source.registry.url:
        raise DataValidationError("Outer registry URL does not match")
    if (
        manifest.dataset.name != f"clinpgx-{source.profile}"
        or manifest.dataset.release != validate_source_identity(source, rights).tag
    ):
        raise DataValidationError("Outer dataset identity does not match")
    actual = schema.database_schema_version
    if actual != manifest.schema_identity.actual or actual != source.transformation.schema_version:
        raise DataValidationError("Semantic schema versions do not match")
    if manifest.transformation.revision != source.transformation.revision:
        raise DataValidationError("Transformation revisions do not match")
    snapshot = _verify_sqlite(root, manifest, schema, source)
    return SemanticReceipt(schema, source, snapshot)


__all__ = [
    "Materialization",
    "SemanticReceipt",
    "canonical_bytes",
    "parse_materialization",
    "read_immutable",
    "validate_semantics",
]
