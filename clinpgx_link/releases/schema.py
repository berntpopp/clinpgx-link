"""Strict semantic schema contract for installed ClinPGx SQLite snapshots."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from pydantic import BeforeValidator, ConfigDict, WithJsonSchema

from clinpgx_link.releases.contract_json import (
    SCHEMA_METADATA,
    NonNegativeInt,
    SchemaVersion,
    SemanticVersion,
    StrictModel,
    parse_canonical,
    schema_bytes,
)
from clinpgx_link.releases.contract_json import (
    canonical_bytes as _canonical_bytes,
)

MAX_DATABASE_SCHEMA_BYTES = 64 * 1024
SQLITE_APPLICATION_ID = 1129072728
SQLITE_USER_VERSION = 1


def _exact_integer(expected: int) -> Callable[[object], object]:
    def validate(value: object) -> object:
        if type(value) is not int or value != expected:
            raise ValueError("exact SQLite integer required")
        return value

    return validate


ApplicationId = Annotated[
    int,
    BeforeValidator(_exact_integer(SQLITE_APPLICATION_ID)),
    WithJsonSchema({"type": "integer", "const": SQLITE_APPLICATION_ID}),
]
UserVersion = Annotated[
    int,
    BeforeValidator(_exact_integer(SQLITE_USER_VERSION)),
    WithJsonSchema({"type": "integer", "const": SQLITE_USER_VERSION}),
]


class RecordCounts(StrictModel):
    dataset: NonNegativeInt
    record: NonNegativeInt
    source_archive: NonNegativeInt
    source_member: NonNegativeInt
    membership: NonNegativeInt


class DatabaseSchema(StrictModel):
    model_config = ConfigDict(**StrictModel.model_config, json_schema_extra=SCHEMA_METADATA)

    schema_version: SchemaVersion
    database_schema_version: SemanticVersion
    sqlite_application_id: ApplicationId
    sqlite_user_version: UserVersion
    record_counts: RecordCounts


def build_database_schema(
    database_schema_version: str, record_counts: RecordCounts
) -> DatabaseSchema:
    return DatabaseSchema(
        schema_version=1,
        database_schema_version=database_schema_version,
        sqlite_application_id=SQLITE_APPLICATION_ID,
        sqlite_user_version=SQLITE_USER_VERSION,
        record_counts=record_counts,
    )


def parse_database_schema(raw: bytes) -> DatabaseSchema:
    return parse_canonical(raw, maximum=MAX_DATABASE_SCHEMA_BYTES, model=DatabaseSchema)


def canonical_bytes(model: DatabaseSchema) -> bytes:
    return _canonical_bytes(model)


def database_schema_schema_bytes() -> bytes:
    return schema_bytes(DatabaseSchema)


__all__ = [
    "DatabaseSchema",
    "RecordCounts",
    "build_database_schema",
    "canonical_bytes",
    "database_schema_schema_bytes",
    "parse_database_schema",
]
