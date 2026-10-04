from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from clinpgx_link.releases.schema import RecordCounts, build_database_schema, canonical_bytes


def _probe_tree(tmp_path: Path) -> tuple[Path, str]:
    database = tmp_path / "generation" / "clinpgx.sqlite"
    database.parent.mkdir()
    snapshot = "sha256:" + "a" * 64
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute("INSERT INTO metadata VALUES ('snapshot_id',?)", (snapshot,))
        connection.execute("CREATE TABLE record(record_pk INTEGER PRIMARY KEY)")
        connection.executemany("INSERT INTO record VALUES (?)", [(2,), (1,)])
    schema = build_database_schema(
        "1.0.0",
        RecordCounts(dataset=0, record=2, source_archive=0, source_member=0, membership=0),
    )
    (database.parent / "schema.json").write_bytes(canonical_bytes(schema))
    return database, snapshot


def test_probe_returns_exact_fleet_fields_from_fixed_read_only_query(tmp_path: Path):
    from clinpgx_link.data_probe import probe

    database, snapshot = _probe_tree(tmp_path)
    assert probe(database, expected_snapshot=snapshot) == {
        "data_schema_version": "1.0.0",
        "record_count": 2,
        "query_result_sha256": hashlib.sha256(b"1").hexdigest(),
    }


def test_probe_refuses_wrong_snapshot_and_oversized_schema_without_path_details(tmp_path: Path):
    from clinpgx_link.data_probe import probe

    database, _snapshot = _probe_tree(tmp_path)
    with pytest.raises(ValueError, match="snapshot is unavailable") as mismatch:
        probe(database, expected_snapshot="sha256:" + "b" * 64)
    assert str(database) not in str(mismatch.value)
    schema = database.parent / "schema.json"
    schema.write_bytes(b" " * 65_537)
    with pytest.raises(ValueError, match="snapshot is unavailable"):
        probe(database, expected_snapshot=_snapshot)


def test_probe_rejects_schema_fifo_without_blocking(tmp_path: Path):
    from clinpgx_link.data_probe import probe

    database, snapshot = _probe_tree(tmp_path)
    schema = database.parent / "schema.json"
    schema.unlink()
    os.mkfifo(schema)
    with pytest.raises(ValueError, match="snapshot is unavailable"):
        probe(database, expected_snapshot=snapshot)


def test_module_reports_missing_production_pins_without_settings_traceback(tmp_path: Path):
    repository_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "clinpgx_link.data_probe"],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(repository_root),
            "CLINPGX_RUNTIME_MODE": "production",
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "ClinPGx data probe unavailable\n"
    assert str(repository_root) not in result.stderr
