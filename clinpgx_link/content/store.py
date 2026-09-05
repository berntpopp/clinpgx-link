"""Bounded disk cache whose references stay pinned until advertised expiry."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from clinpgx_link.exceptions import (
    DataValidationError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
)
from clinpgx_link.models import SourceInfo


@dataclass(frozen=True)
class StoredContent:
    """Complete pinned bytes plus their unchanged source provenance."""

    reference: str
    raw: bytes
    source: SourceInfo
    media_type: str
    expires_at: float


def _source_json(source: SourceInfo) -> str:
    return json.dumps(asdict(source), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reference(source_json: str, media_type: str) -> str:
    identity = json.dumps([source_json, media_type], ensure_ascii=False, separators=(",", ":"))
    return "content:" + hashlib.sha256(identity.encode()).hexdigest()


def _prepare_database(path: Path) -> None:
    if not path.is_absolute() or path.name in {"", ".", ".."} or ".." in path.parts:
        raise InvalidInputError("Content cache path must be absolute and specific.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.stat()
    if path.parent.is_symlink() or parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise InvalidInputError("Content cache requires a private owned directory.")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise InvalidInputError("Content cache must be an owned regular file.") from None
    else:
        os.close(descriptor)


class ContentStore:
    """Cache complete bodies, never evicting a live reference to admit new data.

    ``clock`` supplies wall time so expiry survives process restarts. A transaction
    bounds admission across processes; the lock protects this connection's threads.
    The database lives outside immutable data releases and contains public sources.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = 512 * 1024 * 1024,
        max_entries: int = 512,
        ttl_seconds: float = 3600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_bytes <= 0 or max_entries <= 0 or ttl_seconds <= 0:
            raise InvalidInputError("Content cache limits must be positive.")
        _prepare_database(path)
        self._db = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS content (
            reference TEXT PRIMARY KEY, body BLOB NOT NULL, source TEXT NOT NULL,
            media_type TEXT NOT NULL, expires REAL NOT NULL)""")
        self._db.commit()
        self._lock = threading.RLock()
        self._max_bytes = max_bytes
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._clock = clock

    def now(self) -> float:
        """Return the authoritative wall clock used by retained-content deadlines."""
        return self._clock()

    def put(self, raw: bytes, source: SourceInfo, media_type: str) -> str:
        """Admit a complete body or fail without breaking existing references."""
        if hashlib.sha256(raw).hexdigest() != source.sha256:
            raise DataValidationError("Source digest does not match content bytes.")
        serialized = _source_json(source)
        if len(serialized.encode()) > 65_536 or len(media_type) > 256:
            raise InvalidInputError("Content provenance exceeds cache metadata limits.")
        reference = _reference(serialized, media_type)
        now = self._clock()
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute("DELETE FROM content WHERE expires <= ?", (now,))
                existing = self._db.execute(
                    "SELECT reference FROM content WHERE reference = ?", (reference,)
                ).fetchone()
                if existing is not None:
                    self._db.commit()
                    return reference
                count, size = self._db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(length(body)), 0) FROM content"
                ).fetchone()
                if count >= self._max_entries or size + len(raw) > self._max_bytes:
                    raise RateLimitedError(
                        "Content cache is full of retained references.",
                        subtype="content_capacity",
                        hint="Retry after content expiry or increase the operator cache capacity.",
                    )
                self._db.execute(
                    "INSERT INTO content VALUES (?, ?, ?, ?, ?)",
                    (reference, raw, serialized, media_type, now + self._ttl),
                )
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return reference

    def get(self, reference: str) -> StoredContent:
        """Load the exact pinned body, validating both content and source identity."""
        if not re.fullmatch(r"content:[0-9a-f]{64}", reference):
            raise InvalidInputError("Invalid content reference.", field="content_ref")
        with self._lock:
            row = self._db.execute(
                "SELECT body, source, media_type, expires FROM content WHERE reference = ?",
                (reference,),
            ).fetchone()
        if row is None:
            raise NotFoundError("Content reference is unavailable.", subtype="content_missing")
        raw, serialized, media_type, expires = row
        if expires <= self._clock():
            raise NotFoundError(
                "Content reference expired.",
                subtype="content_expired",
                hint="Repeat the source operation to acquire a new reference.",
            )
        try:
            fields = json.loads(serialized)
            fields["warnings"] = tuple(fields["warnings"])
            source = SourceInfo(**fields)
            if hashlib.sha256(raw).hexdigest() != source.sha256:
                raise ValueError("Content digest mismatch.")
            if _reference(serialized, media_type) != reference:
                raise ValueError("Reference identity mismatch.")
        except (TypeError, ValueError, KeyError) as exc:
            raise DataValidationError("Cached content failed integrity validation.") from exc
        return StoredContent(reference, raw, source, media_type, expires)

    def close(self) -> None:
        """Close the owned database connection during application shutdown."""
        with self._lock:
            self._db.close()
