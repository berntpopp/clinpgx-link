"""Deterministic candidate-only SQLite builder for catalogued ClinPGx sources."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from clinpgx_link.config import settings
from clinpgx_link.data.catalog import SourceInput, is_canonical_release_tag
from clinpgx_link.data.coverage import (
    Membership,
    contextual_json_memberships,
    json_memberships,
    primary_identifier,
    spreadsheet_memberships,
    tabular_memberships,
)
from clinpgx_link.exceptions import DataValidationError, InvalidInputError
from clinpgx_link.ingest.acquire import AcquiredMember, AcquiredSource, read_local_source
from clinpgx_link.ingest.json_records import iter_json_records
from clinpgx_link.ingest.spreadsheets import parse_spreadsheet
from clinpgx_link.ingest.tabular import TabularReader

_SCHEMA_VERSION = 1
_TRANSFORM_CONTRACT = "clinpgx-link-ingest-v1"
_TRANSFORM_FILES = (
    "data/coverage.py",
    "data/schema.sql",
    "ingest/builder.py",
    "ingest/json_records.py",
    "ingest/spreadsheets.py",
    "ingest/tabular.py",
)
_QUARANTINED_LEGACY_DATASET = "data/haplotypes.zip"


@dataclass(frozen=True)
class BuiltSnapshot:
    database: Path
    manifest: dict[str, Any]
    snapshot_id: str
    release_tag: str


@dataclass(frozen=True)
class _ParsedRecord:
    ordinal: int
    pointer: str | None
    parent_pointer: str | None
    fields: Any
    memberships: tuple[Membership, ...]


def _canonical_json(value: Any) -> str:
    def default(item: Any) -> Any:
        if is_dataclass(item):
            return asdict(item)  # type: ignore[arg-type]
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        raise TypeError(f"Unsupported normalized value: {type(item).__name__}")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=default,
    )


def _source_projection(source: SourceInput) -> dict[str, Any]:
    return {
        "byte_count": source.byte_count,
        "dataset_id": source.dataset_id,
        "etag": source.etag,
        "last_modified": source.last_modified,
        "license_id": source.license_id,
        "media_type": source.media_type,
        "published_at": source.published_at,
        "retrieved_at": source.retrieved_at,
        "sha256": source.sha256,
        "source_url": source.source_url,
        "tier": source.tier,
        "version_id": source.version_id,
    }


def _build_config() -> dict[str, int]:
    return {
        "max_archive_bytes": settings.max_archive_bytes,
        "max_archive_member_bytes": settings.max_archive_member_bytes,
        "max_archive_members": settings.max_archive_members,
        "max_expanded_archive_bytes": settings.max_expanded_archive_bytes,
        "max_ingest_rows": settings.max_ingest_rows,
    }


def _transform_identity() -> str:
    package = Path(__file__).parents[1]
    digest = hashlib.sha256()
    for relative in _TRANSFORM_FILES:
        digest.update(relative.encode())
        digest.update(b"\x00")
        digest.update((package / relative).read_bytes())
        digest.update(b"\x00")
    return f"{_TRANSFORM_CONTRACT}:sha256:{digest.hexdigest()}"


def _snapshot_id(
    sources: Sequence[SourceInput], transform_identity: str, build_config: dict[str, int]
) -> str:
    projection = {
        "build_config": build_config,
        "schema_version": _SCHEMA_VERSION,
        "sources": [_source_projection(source) for source in sources],
        "transform_contract": transform_identity,
    }
    digest = hashlib.sha256((_canonical_json(projection) + "\n").encode()).hexdigest()
    return f"sha256:{digest}"


def _record_id(
    snapshot_id: str,
    dataset_id: str,
    member: str,
    ordinal: int,
    pointer: str | None,
) -> str:
    identity = "\x00".join(
        [snapshot_id, dataset_id, member, str(ordinal), pointer if pointer is not None else "-"]
    )
    return "record:" + hashlib.sha256(identity.encode()).hexdigest()


def _text_values(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _text_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _text_values(child)
    elif value is not None:
        yield str(value)


def _tabular_records(
    dataset_id: str, member: AcquiredMember, delimiter: str
) -> tuple[tuple[str, ...], Iterable[_ParsedRecord]]:
    reader = TabularReader(io.BytesIO(member.raw), delimiter=delimiter)

    def records() -> Iterator[_ParsedRecord]:
        for row in reader:
            memberships = tabular_memberships(dataset_id, member.path, row.fields)
            yield _ParsedRecord(row.ordinal, None, None, row.fields, memberships)

    return reader.headers, records()


def _json_records(dataset_id: str, member: AcquiredMember) -> Iterable[_ParsedRecord]:
    root_genes: dict[str, str] = {}
    for ordinal, row in enumerate(iter_json_records(io.BytesIO(member.raw)), start=1):
        if dataset_id == "data/pathways.json.zip" and row.parent_pointer != "":
            continue
        if row.parent_pointer == "" and isinstance(row.value, dict):
            gene = row.value.get("gene")
            if isinstance(gene, str) and gene:
                root_genes[row.pointer] = gene
        root_pointer = "/" + row.pointer.split("/", maxsplit=2)[1] if row.pointer else ""
        context_gene = root_genes.get(root_pointer)
        memberships = (
            *json_memberships(row.value),
            *contextual_json_memberships(
                dataset_id, member.path, row.pointer, row.value, context_gene
            ),
        )
        yield _ParsedRecord(
            ordinal,
            row.pointer,
            row.parent_pointer,
            row.value,
            tuple(dict.fromkeys(memberships)),
        )


def _spreadsheet_records(
    dataset_id: str,
    member: AcquiredMember,
) -> tuple[list[dict[str, Any]], Iterable[_ParsedRecord]]:
    workbook = parse_spreadsheet(io.BytesIO(member.raw))
    if dataset_id == "data/cpic.drug.mapping.zip":
        _validate_cpic_mapping(workbook)
    sheet_metadata = [
        {
            "name": sheet.name,
            "headers": list(sheet.headers),
            "hidden": sheet.hidden,
            "merged_ranges": list(sheet.merged_ranges),
        }
        for sheet in workbook.sheets
    ]

    def records() -> Iterator[_ParsedRecord]:
        ordinal = 0
        for sheet in workbook.sheets:
            for row in sheet.rows:
                ordinal += 1
                fields: dict[str, Any] = {name: asdict(cell) for name, cell in row.fields.items()}
                fields["_sheet"] = sheet.name
                fields["_sheet_ordinal"] = row.ordinal
                yield _ParsedRecord(
                    ordinal,
                    f"/sheets/{sheet.name}/rows/{row.ordinal}",
                    f"/sheets/{sheet.name}",
                    fields,
                    spreadsheet_memberships(dataset_id, member.path, fields),
                )

    return sheet_metadata, records()


def _validate_cpic_mapping(workbook: Any) -> None:
    expected_headers = ("Drug or Ingredient", "Source", "Code Type", "Code")
    expected_pairs = (
        ("RxNorm", "RxCUI"),
        ("DrugBank", "Accession Number"),
        ("ATC", "ATC Code"),
        ("PharmGKB", "PharmGKB Accession ID"),
    )
    if len(workbook.sheets) != 1 or workbook.sheets[0].headers != expected_headers:
        raise DataValidationError("CPIC drug mapping workbook schema differs from its profile")
    sheet = workbook.sheets[0]
    if len(sheet.rows) != 4:
        raise DataValidationError("CPIC drug mapping workbook must contain four source rows")
    observed: list[tuple[Any, Any]] = []
    drugs: set[Any] = set()
    for row in sheet.rows:
        source = row.fields["Source"]
        code_type = row.fields["Code Type"]
        drug = row.fields["Drug or Ingredient"]
        if source.formula or code_type.formula or drug.formula or not drug.present:
            raise DataValidationError("CPIC drug mapping identity cells must be literal values")
        observed.append((source.value, code_type.value))
        drugs.add(drug.value)
    if tuple(observed) != expected_pairs or len(drugs) != 1 or None in drugs:
        raise DataValidationError("CPIC drug mapping source rows differ from their profile")


def _insert_record(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    dataset_id: str,
    member: str,
    record: _ParsedRecord,
) -> None:
    fields_json = _canonical_json(record.fields)
    search_text = " ".join(_text_values(record.fields))
    identifier = primary_identifier(record.memberships)
    record_id = _record_id(snapshot_id, dataset_id, member, record.ordinal, record.pointer)
    connection.execute(
        "INSERT INTO record "
        "(record_id,dataset_id,member,ordinal,json_pointer,parent_pointer,fields_json,"
        "validated_id) VALUES (?,?,?,?,?,?,?,?)",
        (
            record_id,
            dataset_id,
            member,
            record.ordinal,
            record.pointer,
            record.parent_pointer,
            fields_json,
            identifier,
        ),
    )
    record_pk = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    connection.execute(
        "INSERT INTO record_fts(rowid,search_text) VALUES (?,?)", (record_pk, search_text)
    )
    connection.executemany(
        "INSERT OR IGNORE INTO membership "
        "(record_pk,kind,value,match_mode,source_field,tokenizer) VALUES (?,?,?,?,?,?)",
        (
            (
                record_pk,
                membership.kind,
                membership.value,
                membership.match_mode,
                membership.source_field,
                membership.tokenizer,
            )
            for membership in record.memberships
        ),
    )


def _is_readme(path: str) -> bool:
    return Path(path).name.lower().startswith("readme")


def _is_platform_metadata(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return "__MACOSX" in parts or PurePosixPath(path).name.startswith("._")


def _ingest_member(
    connection: sqlite3.Connection,
    *,
    source: AcquiredSource,
    member: AcquiredMember,
    snapshot_id: str,
    remaining_rows: int,
) -> tuple[str, str | None, Any, int]:
    if member.is_directory:
        return "preserved", "Directory entry retained without extraction", None, 0
    if _is_platform_metadata(member.path):
        return "preserved", "Platform metadata retained without tabular parsing", None, 0
    suffix = Path(member.path).suffix.lower()
    headers: Any = None
    try:
        if suffix in {".tsv", ".csv"}:
            headers, records = _tabular_records(
                source.source.dataset_id, member, "\t" if suffix == ".tsv" else ","
            )
        elif suffix == ".json":
            records = _json_records(source.source.dataset_id, member)
        elif suffix == ".xlsx":
            headers, records = _spreadsheet_records(source.source.dataset_id, member)
        else:
            return "preserved", "No normalized parser is declared for this member format", None, 0
        count = 0
        for record in records:
            count += 1
            if count > remaining_rows:
                raise DataValidationError(
                    "Snapshot exceeds its configured normalized-row limit",
                    subtype="resource_limit",
                )
            _insert_record(
                connection,
                snapshot_id=snapshot_id,
                dataset_id=source.source.dataset_id,
                member=member.path,
                record=record,
            )
        return "indexed", None, headers, count
    except DataValidationError as exc:
        legacy_quarantine = source.source.dataset_id == _QUARANTINED_LEGACY_DATASET and (
            suffix == ".xlsx" or (suffix == ".json" and _is_readme(member.path))
        )
        if legacy_quarantine and exc.subtype != "resource_limit":
            return "quarantined", str(exc), headers, 0
        raise


def _create_database(
    database: Path,
    sources: Sequence[AcquiredSource],
    snapshot_id: str,
    release_tag: str,
    transform_identity: str,
    build_config: dict[str, int],
) -> dict[str, Any]:
    schema = (Path(__file__).parents[1] / "data" / "schema.sql").read_text(encoding="utf-8")
    connection = sqlite3.connect(database)
    artifacts: list[dict[str, Any]] = []
    total_records = 0
    try:
        connection.executescript(schema)
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES ('snapshot_id',?)", (snapshot_id,)
        )
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES ('release_tag',?)", (release_tag,)
        )
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES ('schema_version',?)",
            (str(_SCHEMA_VERSION),),
        )
        for acquired in sources:
            source = acquired.source
            connection.execute(
                "INSERT INTO dataset "
                "(dataset_id,file_name,source_url,retrieved_at,published_at,sha256,byte_count,"
                "media_type,license_id,tier,etag,last_modified,version_id,limitations_json,warnings_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source.dataset_id,
                    source.file_name,
                    source.source_url,
                    source.retrieved_at,
                    source.published_at,
                    source.sha256,
                    source.byte_count,
                    source.media_type,
                    source.license_id,
                    source.tier,
                    source.etag,
                    source.last_modified,
                    source.version_id,
                    "[]",
                    "[]",
                ),
            )
            connection.execute(
                "INSERT INTO source_archive(dataset_id,media_type,byte_count,sha256,raw) "
                "VALUES (?,?,?,?,?)",
                (
                    source.dataset_id,
                    source.media_type,
                    len(acquired.archive_bytes),
                    acquired.sha256,
                    acquired.archive_bytes,
                ),
            )
            member_manifest: list[dict[str, Any]] = []
            limitations: list[str] = []
            dataset_records = 0
            for member in acquired.members:
                connection.execute(
                    "INSERT INTO source_member "
                    "(dataset_id,path,media_type,byte_count,compressed_bytes,is_directory,sha256,raw,"
                    "parser_status,limitation,headers_json) VALUES (?,?,?,?,?,?,?,?,'pending',NULL,NULL)",
                    (
                        source.dataset_id,
                        member.path,
                        member.media_type,
                        member.byte_count,
                        member.compressed_bytes,
                        int(member.is_directory),
                        member.sha256,
                        member.raw,
                    ),
                )
                connection.execute("SAVEPOINT normalize_member")
                try:
                    status, limitation, headers, count = _ingest_member(
                        connection,
                        source=acquired,
                        member=member,
                        snapshot_id=snapshot_id,
                        remaining_rows=settings.max_ingest_rows - total_records,
                    )
                    if status == "quarantined":
                        connection.execute("ROLLBACK TO normalize_member")
                    connection.execute("RELEASE normalize_member")
                except Exception:
                    connection.execute("ROLLBACK TO normalize_member")
                    connection.execute("RELEASE normalize_member")
                    raise
                total_records += count
                dataset_records += count
                if limitation:
                    limitations.append(f"{member.path}: {limitation}")
                headers_json = _canonical_json(headers) if headers is not None else None
                connection.execute(
                    "UPDATE source_member SET parser_status=?,limitation=?,headers_json=?,"
                    "record_count=? WHERE dataset_id=? AND path=?",
                    (status, limitation, headers_json, count, source.dataset_id, member.path),
                )
                member_manifest.append(
                    {
                        "byte_count": member.byte_count,
                        "compressed_bytes": member.compressed_bytes,
                        "is_directory": member.is_directory,
                        "limitation": limitation,
                        "media_type": member.media_type,
                        "path": member.path,
                        "record_count": count,
                        "sha256": member.sha256,
                        "status": status,
                    }
                )
            warnings: list[str] = []
            if source.dataset_id == "data/genes.zip":
                vip_values = connection.execute(
                    "SELECT DISTINCT json_extract(fields_json, '$.\"Is VIP\"') FROM record "
                    "WHERE dataset_id=? AND member='genes.tsv'",
                    (source.dataset_id,),
                ).fetchall()
                if vip_values and {row[0] for row in vip_values} == {"Yes"}:
                    warnings.append("upstream_anomaly:genes_is_vip_all_yes")
            connection.execute(
                "UPDATE dataset SET limitations_json=?,warnings_json=?,record_count=? "
                "WHERE dataset_id=?",
                (
                    _canonical_json(limitations),
                    _canonical_json(warnings),
                    dataset_records,
                    source.dataset_id,
                ),
            )
            artifact = _source_projection(source)
            artifact["members"] = member_manifest
            artifact["record_count"] = dataset_records
            artifact["limitations"] = limitations
            artifact["warnings"] = warnings
            artifacts.append(artifact)
        manifest = {
            "build_config": build_config,
            "schema_version": _SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "release_tag": release_tag,
            "transform_contract": transform_identity,
            "artifacts": artifacts,
            "record_count": total_records,
        }
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES ('manifest_json',?)",
            (_canonical_json(manifest),),
        )
        connection.commit()
        connection.execute("ANALYZE")
        connection.commit()
        connection.execute("VACUUM")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return manifest


def build_snapshot(
    sources: Sequence[SourceInput], destination: Path, release_tag: str
) -> BuiltSnapshot:
    """Create one validated candidate without changing any active release pointer."""
    if not sources:
        raise InvalidInputError("At least one explicit source is required", field="sources")
    if not destination.is_absolute() or destination.is_symlink():
        raise InvalidInputError("Candidate destination must be an absolute nonsymlink path")
    if not is_canonical_release_tag(release_tag):
        raise InvalidInputError("Release tag is not canonical", field="release_tag")
    ordered = tuple(sorted(sources, key=lambda item: item.dataset_id))
    if len({source.dataset_id for source in ordered}) != len(ordered):
        raise InvalidInputError("Build source dataset IDs must be unique", field="sources")
    acquired = tuple(read_local_source(source) for source in ordered)
    transform_identity = _transform_identity()
    build_config = _build_config()
    identity = _snapshot_id(ordered, transform_identity, build_config)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / release_tag
    if target.exists() or target.is_symlink():
        raise InvalidInputError("Candidate release tag already exists", field="release_tag")
    temporary = Path(tempfile.mkdtemp(prefix=f".{release_tag}-", dir=destination))
    database = temporary / "clinpgx.sqlite"
    try:
        manifest = _create_database(
            database,
            acquired,
            identity,
            release_tag,
            transform_identity,
            build_config,
        )
        os.replace(temporary, target)
    except Exception:
        if temporary.exists() and temporary.parent == destination:
            shutil.rmtree(temporary)
        raise
    return BuiltSnapshot(
        database=target / "clinpgx.sqlite",
        manifest=manifest,
        snapshot_id=identity,
        release_tag=release_tag,
    )


__all__ = ["BuiltSnapshot", "build_snapshot"]
