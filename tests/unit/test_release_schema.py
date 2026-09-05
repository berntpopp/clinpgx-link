"""Strict semantic database-schema contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _value() -> dict[str, object]:
    return {
        "schema_version": 1,
        "database_schema_version": "1.0.0",
        "sqlite_application_id": 1129072728,
        "sqlite_user_version": 1,
        "record_counts": {
            "dataset": 1,
            "record": 2,
            "source_archive": 1,
            "source_member": 1,
            "membership": 2,
        },
    }


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def test_database_schema_build_parse_and_canonical_vendor_parity() -> None:
    from clinpgx_link.releases.schema import (
        build_database_schema,
        canonical_bytes,
        database_schema_schema_bytes,
        parse_database_schema,
    )

    raw = _canonical(_value())
    parsed = parse_database_schema(raw)
    assert canonical_bytes(parsed) == raw
    assert build_database_schema("1.0.0", parsed.record_counts) == parsed
    expected = (
        Path(__file__).parents[2] / "vendor/clinpgx/database-schema.schema.json"
    ).read_bytes()
    assert database_schema_schema_bytes() == expected


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("schema_version",), True),
        (("database_schema_version",), "1"),
        (("sqlite_application_id",), 1129072729),
        (("sqlite_user_version",), False),
        (("record_counts", "dataset"), True),
        (("record_counts", "record"), -1),
        (("record_counts", "extra"), 0),
        (("extra",), 0),
    ],
)
def test_database_schema_rejects_wrong_exact_shape(
    path: tuple[str, ...], replacement: object
) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.schema import parse_database_schema

    value = _value()
    target = value
    for name in path[:-1]:
        target = target[name]  # type: ignore[assignment,index]
    target[path[-1]] = replacement
    with pytest.raises(DataValidationError):
        parse_database_schema(_canonical(value))


def test_database_schema_requires_canonical_bounded_json() -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.schema import parse_database_schema

    for raw in (json.dumps(_value(), indent=2).encode(), b"{}", b"!\xff", b"x" * 65537):
        with pytest.raises(DataValidationError):
            parse_database_schema(raw)
