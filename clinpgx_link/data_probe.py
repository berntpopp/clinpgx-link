"""Emit the reviewed fleet semantic probe for the installed ClinPGx snapshot."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

from clinpgx_link.releases.schema import MAX_DATABASE_SCHEMA_BYTES, parse_database_schema
from clinpgx_link.runtime_data_guard import RuntimeDataGuard


def _open_regular(path: Path, *, maximum_bytes: int | None = None) -> tuple[int, tuple[int, ...]]:
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("Installed ClinPGx snapshot is unavailable")
    if maximum_bytes is not None and before.st_size > maximum_bytes:
        raise ValueError("Installed ClinPGx snapshot is unavailable")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    opened = os.fstat(descriptor)
    signature = (
        opened.st_dev,
        opened.st_ino,
        opened.st_mode,
        opened.st_nlink,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
    )
    before_signature = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or signature != before_signature:
        os.close(descriptor)
        raise ValueError("Installed ClinPGx snapshot is unavailable")
    return descriptor, signature


def _read_bounded(descriptor: int, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum_bytes + 1
    while remaining:
        chunk = os.read(descriptor, min(remaining, 16 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if len(raw) > maximum_bytes:
        raise ValueError("Installed ClinPGx snapshot is unavailable")
    return raw


def _fd_signature(descriptor: int) -> tuple[int, ...]:
    info = os.fstat(descriptor)
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def probe(database: Path, *, expected_snapshot: str) -> dict[str, object]:
    """Read fixed schema/count/key facts from one SQLite file without mutation."""
    if not database.is_absolute():
        raise ValueError("Installed ClinPGx snapshot is unavailable")
    database_fd, database_signature = _open_regular(database)
    schema_fd = -1
    connection: sqlite3.Connection | None = None
    try:
        uri = f"file:/proc/self/fd/{database_fd}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        schema_fd, schema_signature = _open_regular(
            database.parent / "schema.json", maximum_bytes=MAX_DATABASE_SCHEMA_BYTES
        )
        schema_bytes = _read_bounded(schema_fd, MAX_DATABASE_SCHEMA_BYTES)
        connection.execute("PRAGMA query_only=ON")
        schema = parse_database_schema(schema_bytes)
        snapshot = connection.execute(
            "SELECT value FROM metadata WHERE key='snapshot_id'"
        ).fetchone()
        if snapshot is None or snapshot[0] != expected_snapshot:
            raise ValueError("Installed ClinPGx snapshot is unavailable")
        count = connection.execute("SELECT COUNT(*) FROM record").fetchone()[0]
        first = connection.execute(
            "SELECT record_pk FROM record ORDER BY record_pk LIMIT 1"
        ).fetchone()
        if type(count) is not int or count < 0 or (count > 0 and first is None):
            raise ValueError("Installed ClinPGx snapshot is unavailable")
        key = str(first[0]) if first is not None else ""
        if (
            _fd_signature(database_fd) != database_signature
            or _fd_signature(schema_fd) != schema_signature
        ):
            raise ValueError("Installed ClinPGx snapshot is unavailable")
        return {
            "data_schema_version": schema.database_schema_version,
            "record_count": count,
            "query_result_sha256": hashlib.sha256(key.encode("utf-8")).hexdigest(),
        }
    finally:
        if connection is not None:
            connection.close()
        os.close(database_fd)
        if schema_fd >= 0:
            os.close(schema_fd)


def main() -> int:
    try:
        from clinpgx_link.config import settings

        if (
            not settings.expected_snapshot
            or not settings.expected_release_tag
            or not settings.expected_runtime_digest
        ):
            raise ValueError("runtime pins are missing")
        guard = RuntimeDataGuard.start(
            settings.data_root,
            expected_release_tag=settings.expected_release_tag,
            expected_digest=settings.expected_runtime_digest,
        )
        payload = probe(guard.database_path, expected_snapshot=settings.expected_snapshot)
        if not guard.is_intact():
            raise ValueError("runtime generation changed")
        sys.stdout.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
        return 0
    except Exception:
        sys.stderr.write("ClinPGx data probe unavailable\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
