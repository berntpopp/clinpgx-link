"""Read-only queries over one immutable ClinPGx source snapshot."""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, cast

from clinpgx_link.config import settings
from clinpgx_link.data.coverage import field_metadata, known_filters
from clinpgx_link.exceptions import (
    DataValidationError,
    InvalidInputError,
    NotFoundError,
    ResponseTooLargeError,
    UpstreamUnavailableError,
)
from clinpgx_link.models import SourceInfo, SourceResponse

_FILTERS = frozenset({"id", "name", "gene", "chemical", "variant", "source", "annotation_id"})
_ENTITY_TYPES = frozenset(
    {"allele", "annotation_id", "chemical", "disease", "gene", "literature", "variant"}
)
_FTS_TOKEN = re.compile(r"\w+", re.UNICODE)


class DatasetRepository:
    """Repository pinned to an immutable SQLite database file and snapshot identity."""

    def __init__(self, database: Path) -> None:
        if not database.is_absolute() or database.is_symlink() or not database.is_file():
            raise InvalidInputError("Snapshot database must be an absolute regular file")
        self.database = database.resolve(strict=True)
        uri = f"file:{self.database.as_posix()}?mode=ro&immutable=1"
        self._connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._snapshot_id = self._metadata("snapshot_id")
        self._release_tag = self._metadata("release_tag")

    def close(self) -> None:
        self._connection.close()

    def _metadata(self, key: str) -> str:
        row = self._connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if row is None:
            raise UpstreamUnavailableError(
                "Snapshot metadata is incomplete", subtype="snapshot_invalid"
            )
        return str(row[0])

    def _validate_snapshot(self, expected_snapshot: str | None) -> None:
        if expected_snapshot is not None and expected_snapshot != self._snapshot_id:
            raise UpstreamUnavailableError(
                "Requested snapshot does not match the open immutable snapshot",
                subtype="snapshot_mismatch",
            )

    def status(self) -> dict[str, Any]:
        return {
            "snapshot_id": self._snapshot_id,
            "release_tag": self._release_tag,
            "database": str(self.database),
            "ready": True,
        }

    def _dataset(self, dataset_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM dataset WHERE dataset_id=?", (dataset_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("Dataset is not installed in this snapshot", field="dataset_id")
        return cast(sqlite3.Row, row)

    def _source(
        self, dataset: sqlite3.Row | None = None, *, digest: str | None = None
    ) -> SourceInfo:
        if dataset is None:
            row = self._connection.execute(
                "SELECT source_url,retrieved_at FROM dataset ORDER BY retrieved_at DESC LIMIT 1"
            ).fetchone()
            url = "https://www.clinpgx.org/downloads" if row is None else str(row["source_url"])
            retrieved_at = "1970-01-01T00:00:00Z" if row is None else str(row["retrieved_at"])
            warnings: tuple[str, ...] = ()
        else:
            url = str(dataset["source_url"])
            retrieved_at = str(dataset["retrieved_at"])
            warnings = tuple(json.loads(dataset["warnings_json"]))
        return SourceInfo(
            source="ClinPGx local snapshot",
            url=url,
            retrieved_at=retrieved_at,
            sha256=digest or self._snapshot_id.removeprefix("sha256:"),
            data_source="download",
            release_tag=self._release_tag,
            coverage="installed_snapshot",
            warnings=warnings,
        )

    def list_datasets(self) -> SourceResponse:
        rows = self._connection.execute(
            "SELECT dataset_id,file_name,published_at AS source_date,byte_count,sha256,"
            "license_id,tier,record_count,limitations_json,warnings_json "
            "FROM dataset ORDER BY dataset_id"
        ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["limitations"] = json.loads(value.pop("limitations_json"))
            value["warnings"] = json.loads(value.pop("warnings_json"))
            values.append(value)
        return SourceResponse(value=values, source=self._source())

    def describe(self, dataset_id: str) -> SourceResponse:
        dataset = self._dataset(dataset_id)
        members = self._connection.execute(
            "SELECT path,media_type,byte_count,sha256,is_directory,parser_status,limitation,"
            "headers_json,record_count FROM source_member WHERE dataset_id=? ORDER BY path",
            (dataset_id,),
        ).fetchall()
        described_members: list[dict[str, Any]] = []
        for row in members:
            value = dict(row)
            value["is_directory"] = bool(value["is_directory"])
            headers_value = json.loads(value.pop("headers_json")) if row["headers_json"] else None
            if isinstance(headers_value, list) and all(
                isinstance(item, str) for item in headers_value
            ):
                value["fields"] = field_metadata(dataset_id, str(row["path"]), tuple(headers_value))
            else:
                value["fields"] = []
                if headers_value is not None:
                    value["sheets"] = headers_value
            described_members.append(value)
        result = {
            "dataset_id": dataset["dataset_id"],
            "file_name": dataset["file_name"],
            "source_url": dataset["source_url"],
            "source_date": dataset["published_at"],
            "retrieved_at": dataset["retrieved_at"],
            "sha256": dataset["sha256"],
            "byte_count": dataset["byte_count"],
            "license_id": dataset["license_id"],
            "tier": dataset["tier"],
            "record_count": dataset["record_count"],
            "limitations": json.loads(dataset["limitations_json"]),
            "warnings": json.loads(dataset["warnings_json"]),
            "members": described_members,
        }
        return SourceResponse(value=result, source=self._source(dataset))

    @staticmethod
    def _validate_page(limit: int, offset: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise InvalidInputError("Limit must be between 1 and 100", field="limit")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise InvalidInputError("Offset must be non-negative", field="offset")

    @staticmethod
    def _validate_filter_values(filters: dict[str, str]) -> None:
        for key, value in filters.items():
            if not isinstance(value, str) or not value:
                raise InvalidInputError("Filter values must be nonempty strings", field=key)

    @classmethod
    def _validate_filters(cls, filters: dict[str, str]) -> None:
        cls._validate_filter_values(filters)
        for key in filters:
            if key not in _FILTERS:
                raise InvalidInputError("Unknown canonical dataset filter", field=key)

    @staticmethod
    def _fts_query(query: str) -> str | None:
        tokens = _FTS_TOKEN.findall(query)
        if not tokens:
            return None
        return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)

    def _query_records(
        self,
        *,
        clauses: list[str],
        parameters: list[Any],
        query: str | None,
        filters: dict[str, str],
        match: str,
        limit: int,
        offset: int,
        join_parent: str | None = None,
        relation_kind: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        for key, value in filters.items():
            if match == "exact":
                clauses.append(
                    "EXISTS (SELECT 1 FROM membership m WHERE m.record_pk=r.record_pk "
                    "AND m.kind=? AND m.value=? AND m.match_mode='exact')"
                )
            else:
                clauses.append(
                    "EXISTS (SELECT 1 FROM membership m WHERE m.record_pk=r.record_pk "
                    "AND m.kind=? AND m.value=? AND m.match_mode IN ('exact','member'))"
                )
            parameters.extend([key, value])
        if query is not None:
            expression = self._fts_query(query)
            if expression is None:
                clauses.append("0")
            else:
                clauses.append(
                    "r.record_pk IN (SELECT rowid FROM record_fts WHERE record_fts MATCH ?)"
                )
                parameters.append(expression)
        where = " AND ".join(clauses) if clauses else "1"
        count_sql = f"SELECT count(*) FROM record r WHERE {where}"  # noqa: S608
        total = int(self._connection.execute(count_sql, parameters).fetchone()[0])
        select_sql = f"SELECT r.* FROM record r WHERE {where} ORDER BY r.dataset_id,r.member,r.ordinal,r.json_pointer,r.record_id LIMIT ? OFFSET ?"  # noqa: S608
        rows = self._connection.execute(select_sql, [*parameters, limit, offset]).fetchall()
        result_rows = [self._record_value(row) for row in rows]
        if join_parent is not None and relation_kind is not None:
            for result_row in result_rows:
                result_row["join"] = {
                    "parent_record_id": join_parent,
                    "relation_kind": relation_kind,
                    "source_row_ids": [result_row["record_id"]],
                }
        return result_rows, total

    @staticmethod
    def _record_value(row: sqlite3.Row) -> dict[str, Any]:
        value: dict[str, Any] = {
            "record_id": row["record_id"],
            "dataset_id": row["dataset_id"],
            "member": row["member"],
            "ordinal": row["ordinal"],
            "fields": json.loads(row["fields_json"]),
        }
        if row["validated_id"] is not None:
            value["id"] = row["validated_id"]
        if row["json_pointer"] is not None:
            value["json_pointer"] = row["json_pointer"]
            value["parent_pointer"] = row["parent_pointer"]
        return value

    def _page_response(
        self,
        values: list[dict[str, Any]],
        total: int,
        offset: int,
        dataset: sqlite3.Row | None,
    ) -> SourceResponse:
        return SourceResponse(
            value=values,
            source=self._source(dataset),
            details={
                "total_count": total,
                "offset": offset,
                "returned": len(values),
                "has_more": offset + len(values) < total,
                "snapshot_id": self._snapshot_id,
            },
        )

    def search(
        self,
        dataset_id: str,
        *,
        member: str | None = None,
        query: str | None = None,
        filters: dict[str, str] | None = None,
        limit: int = 20,
        offset: int = 0,
        match: str = "exact",
        expected_snapshot: str | None = None,
    ) -> SourceResponse:
        """Search one dataset; lowercase canonical filter names are reserved semantics."""
        self._validate_snapshot(expected_snapshot)
        self._validate_page(limit, offset)
        if match not in {"exact", "member"}:
            raise InvalidInputError("Match must be exact or member", field="match")
        selected_filters = filters or {}
        self._validate_filter_values(selected_filters)
        dataset = self._dataset(dataset_id)
        member_row: sqlite3.Row | None = None
        if member is not None:
            member_row = self._connection.execute(
                "SELECT headers_json FROM source_member WHERE dataset_id=? AND path=?",
                (dataset_id, member),
            ).fetchone()
            if member_row is None:
                raise NotFoundError("Dataset member is not installed", field="member")
        supported = known_filters(dataset_id, member)
        canonical_filters = {
            key: value for key, value in selected_filters.items() if key in _FILTERS
        }
        source_filters = {
            key: value for key, value in selected_filters.items() if key not in _FILTERS
        }
        unsupported = set(canonical_filters) - supported
        if unsupported:
            raise InvalidInputError(
                "Filter semantics are not declared for this dataset/member",
                field=sorted(unsupported)[0],
            )
        clauses = ["r.dataset_id=?"]
        parameters: list[Any] = [dataset_id]
        if member is not None:
            clauses.append("r.member=?")
            parameters.append(member)
        if source_filters:
            if member is None or member_row is None:
                raise InvalidInputError(
                    "Source field filters require an exact member", field=sorted(source_filters)[0]
                )
            headers = json.loads(member_row["headers_json"]) if member_row["headers_json"] else None
            if not isinstance(headers, list) or not all(isinstance(item, str) for item in headers):
                raise InvalidInputError(
                    "Source field filters require a tabular text member",
                    field=sorted(source_filters)[0],
                )
            metadata = {
                str(item["name"]): item
                for item in field_metadata(dataset_id, member, tuple(headers))
            }
            for key, value in source_filters.items():
                declared = metadata.get(key)
                if declared is None:
                    raise InvalidInputError("Unknown source field filter", field=key)
                if match == "member":
                    if "member" not in declared["match_modes"]:
                        raise InvalidInputError(
                            "Source field has no declared member tokenizer", field=key
                        )
                    clauses.append(
                        "EXISTS (SELECT 1 FROM membership source_value "
                        "WHERE source_value.record_pk=r.record_pk "
                        "AND source_value.source_field=? AND source_value.value=? "
                        "AND source_value.match_mode='member')"
                    )
                else:
                    clauses.append(
                        "EXISTS (SELECT 1 FROM json_each(r.fields_json) source_value "
                        "WHERE source_value.key=? AND source_value.type='text' "
                        "AND source_value.value=?)"
                    )
                parameters.extend([key, value])
        values, total = self._query_records(
            clauses=clauses,
            parameters=parameters,
            query=query,
            filters=canonical_filters,
            match=match,
            limit=limit,
            offset=offset,
        )
        return self._page_response(values, total, offset, dataset)

    def get_record(self, record_id: str, *, expected_snapshot: str | None = None) -> SourceResponse:
        self._validate_snapshot(expected_snapshot)
        row = self._connection.execute(
            "SELECT * FROM record WHERE record_id=?", (record_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("Record is not installed in this snapshot", field="record_id")
        dataset = self._dataset(str(row["dataset_id"]))
        asset = self._connection.execute(
            "SELECT media_type,byte_count,sha256 FROM source_member WHERE dataset_id=? AND path=?",
            (row["dataset_id"], row["member"]),
        ).fetchone()
        return SourceResponse(
            value=self._record_value(row),
            source=self._source(dataset),
            details={
                "snapshot_id": self._snapshot_id,
                "asset": {
                    "dataset_id": row["dataset_id"],
                    "member": row["member"],
                    "sha256": asset["sha256"],
                    "total_bytes": asset["byte_count"],
                    "media_type": asset["media_type"],
                },
            },
        )

    def search_entities(
        self,
        entity_type: str,
        *,
        query: str | None = None,
        filters: dict[str, str] | None = None,
        limit: int = 20,
        offset: int = 0,
        expected_snapshot: str | None = None,
    ) -> SourceResponse:
        self._validate_snapshot(expected_snapshot)
        self._validate_page(limit, offset)
        if entity_type not in _ENTITY_TYPES:
            raise InvalidInputError("Unknown local entity type", field="entity_type")
        selected_filters = filters or {}
        self._validate_filters(selected_filters)
        supported_rows = self._connection.execute(
            "SELECT DISTINCT candidate.kind FROM membership entity "
            "JOIN membership candidate ON candidate.record_pk=entity.record_pk "
            "WHERE entity.kind=?",
            (entity_type,),
        ).fetchall()
        supported_filters = {str(row[0]) for row in supported_rows if str(row[0]) in _FILTERS} | {
            "id"
        }
        unsupported = set(selected_filters) - supported_filters
        if unsupported:
            raise InvalidInputError(
                "Filter semantics are not installed for this entity type",
                field=sorted(unsupported)[0],
            )
        clauses = [
            "r.record_pk IN (SELECT entity.record_pk FROM membership entity WHERE entity.kind=?)"
        ]
        parameters: list[Any] = [entity_type]
        entity_filters = dict(selected_filters)
        identifier = entity_filters.pop("id", None)
        if identifier is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM membership identity WHERE identity.record_pk=r.record_pk "
                "AND identity.value=? AND identity.kind IN ('id',?))"
            )
            parameters.extend([identifier, entity_type])
        values, total = self._query_records(
            clauses=clauses,
            parameters=parameters,
            query=query,
            filters=entity_filters,
            match="member",
            limit=limit,
            offset=offset,
        )
        return self._page_response(values, total, offset, None)

    def related(
        self,
        record_id: str,
        *,
        result_type: str,
        other_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        expected_snapshot: str | None = None,
    ) -> SourceResponse:
        self._validate_snapshot(expected_snapshot)
        self._validate_page(limit, offset)
        parent = self._connection.execute(
            "SELECT * FROM record WHERE record_id=?", (record_id,)
        ).fetchone()
        if parent is None:
            raise NotFoundError("Related-record parent is not installed", field="record_id")
        dataset = self._dataset(str(parent["dataset_id"]))
        if (
            result_type in {"evidence", "allele", "literature"}
            and parent["member"] == "summary_annotations.tsv"
        ):
            annotation = self._connection.execute(
                "SELECT m.value FROM membership m JOIN record r ON r.record_pk=m.record_pk "
                "WHERE r.record_id=? AND m.kind='annotation_id' "
                "AND m.match_mode='exact' LIMIT 1",
                (record_id,),
            ).fetchone()
            if annotation is None:
                raise DataValidationError(
                    "Summary annotation identity is unavailable for this installed row"
                )
            target = (
                "summary_ann_alleles.tsv" if result_type == "allele" else "summary_ann_evidence.tsv"
            )
            clauses = ["r.dataset_id=?", "r.member=?"]
            parameters: list[Any] = [parent["dataset_id"], target]
            filters = {"annotation_id": str(annotation[0])}
            if result_type == "literature":
                clauses.append(
                    "EXISTS (SELECT 1 FROM membership citation "
                    "WHERE citation.record_pk=r.record_pk "
                    "AND citation.kind='literature')"
                )
        elif result_type == "relationship" and parent["member"] == "relationships.tsv":
            clauses = ["r.dataset_id=?", "r.member='relationships.tsv'", "r.record_id=?"]
            parameters = [parent["dataset_id"], record_id]
            filters = {}
        else:
            raise InvalidInputError(
                "Result type is not declared for this source record", field="result_type"
            )
        if other_id is not None:
            filters["literature" if result_type == "literature" else "id"] = other_id
        values, total = self._query_records(
            clauses=clauses,
            parameters=parameters,
            query=None,
            filters=filters,
            match="member",
            limit=limit,
            offset=offset,
            join_parent=record_id,
            relation_kind=result_type,
        )
        if result_type == "literature":
            for value in values:
                value["join"]["limitation"] = "citing_evidence_row_not_bibliographic_detail"
        return self._page_response(values, total, offset, dataset)

    def _asset_metadata(
        self, dataset_id: str, member: str | None
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        dataset = self._dataset(dataset_id)
        if member is None:
            row = self._connection.execute(
                "SELECT media_type,byte_count,sha256 FROM source_archive WHERE dataset_id=?",
                (dataset_id,),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT media_type,byte_count,sha256 FROM source_member "
                "WHERE dataset_id=? AND path=?",
                (dataset_id, member),
            ).fetchone()
        if row is None:
            raise NotFoundError("Source asset is not installed", field="member")
        return dataset, row

    def asset_content(
        self,
        dataset_id: str,
        *,
        member: str | None = None,
        expected_snapshot: str | None = None,
    ) -> SourceResponse:
        self._validate_snapshot(expected_snapshot)
        dataset, metadata = self._asset_metadata(dataset_id, member)
        limit = settings.max_archive_bytes if member is None else settings.max_archive_member_bytes
        if metadata["byte_count"] > limit:
            raise ResponseTooLargeError("Source asset exceeds the configured full-body limit")
        if member is None:
            row = self._connection.execute(
                "SELECT raw FROM source_archive WHERE dataset_id=?", (dataset_id,)
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT raw FROM source_member WHERE dataset_id=? AND path=?",
                (dataset_id, member),
            ).fetchone()
        raw = bytes(row[0])
        return SourceResponse(
            value=raw,
            source=self._source(dataset, digest=str(metadata["sha256"])),
            details={
                "media_type": metadata["media_type"],
                "total_bytes": metadata["byte_count"],
                "snapshot_id": self._snapshot_id,
                "dataset_id": dataset_id,
                "member": member,
            },
        )

    def read_asset(
        self,
        dataset_id: str,
        *,
        member: str | None = None,
        start: int = 0,
        length: int = 4096,
        expected_snapshot: str | None = None,
    ) -> SourceResponse:
        self._validate_snapshot(expected_snapshot)
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise InvalidInputError("Asset start must be non-negative", field="start")
        if isinstance(length, bool) or not isinstance(length, int) or not 1 <= length <= 8192:
            raise InvalidInputError("Asset length must be between 1 and 8192", field="length")
        dataset, metadata = self._asset_metadata(dataset_id, member)
        if member is None:
            row = self._connection.execute(
                "SELECT substr(raw,?,?) FROM source_archive WHERE dataset_id=?",
                (start + 1, length, dataset_id),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT substr(raw,?,?) FROM source_member WHERE dataset_id=? AND path=?",
                (start + 1, length, dataset_id, member),
            ).fetchone()
        raw = bytes(row[0])
        total = int(metadata["byte_count"])
        next_offset = start + len(raw) if start + len(raw) < total else None
        value = {
            "dataset_id": dataset_id,
            "member": member,
            "media_type": metadata["media_type"],
            "total_bytes": total,
            "sha256": metadata["sha256"],
            "offset": start,
            "returned": len(raw),
            "encoding": "base64",
            "data": base64.b64encode(raw).decode("ascii"),
            "next_offset": next_offset,
        }
        return SourceResponse(
            value=value,
            source=self._source(dataset, digest=str(metadata["sha256"])),
            details={"snapshot_id": self._snapshot_id},
        )


__all__ = ["DatasetRepository"]
