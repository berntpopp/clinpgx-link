"""Immutable references must remain valid for their advertised retention window."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from clinpgx_link.models import SourceInfo


def source(raw: bytes) -> SourceInfo:
    return SourceInfo(
        source="ClinPGx",
        url="https://api.clinpgx.org/v1/data/gene/PA124",
        retrieved_at="2026-09-05T10:00:00Z",
        sha256=hashlib.sha256(raw).hexdigest(),
        data_source="api",
    )


def test_reference_survives_process_reopen_and_preserves_provenance(tmp_path):
    from clinpgx_link.content.store import ContentStore

    raw = b'{"id":"PA124"}'
    store = ContentStore(tmp_path / "content.sqlite", max_bytes=100, max_entries=2)
    ref = store.put(raw, source(raw), "application/json")
    store.close()
    reopened = ContentStore(tmp_path / "content.sqlite", max_bytes=100, max_entries=2)
    record = reopened.get(ref)
    assert record.raw == raw
    assert record.source == source(raw)
    assert record.media_type == "application/json"
    assert record.reference == ref
    reopened.close()


def test_capacity_failure_never_evicts_unexpired_content(tmp_path):
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.exceptions import RateLimitedError

    store = ContentStore(tmp_path / "content.sqlite", max_bytes=10, max_entries=1)
    ref = store.put(b"1234567890", source(b"1234567890"), "text/plain")
    with pytest.raises(RateLimitedError):
        store.put(b"new", source(b"new"), "text/plain")
    assert store.get(ref).raw == b"1234567890"
    assert store.put(b"1234567890", source(b"1234567890"), "text/plain") == ref
    store.close()


def test_expiry_is_explicit_and_frees_capacity(tmp_path):
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.exceptions import NotFoundError

    now = [100.0]
    store = ContentStore(
        tmp_path / "content.sqlite",
        max_bytes=5,
        max_entries=1,
        ttl_seconds=10,
        clock=lambda: now[0],
    )
    ref = store.put(b"old", source(b"old"), "text/plain")
    now[0] = 109.0
    assert store.get(ref).expires_at == 110.0
    now[0] = 110.0
    with pytest.raises(NotFoundError) as failure:
        store.get(ref)
    assert failure.value.subtype == "content_expired"
    new_ref = store.put(b"new", source(b"new"), "text/plain")
    assert store.get(new_ref).raw == b"new"
    store.close()


def test_source_digest_mismatch_is_rejected_before_storage(tmp_path):
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.exceptions import DataValidationError

    store = ContentStore(tmp_path / "content.sqlite", max_bytes=100, max_entries=2)
    with pytest.raises(DataValidationError):
        store.put(b"wrong", source(b"right"), "text/plain")
    store.close()


def test_same_bytes_with_different_sources_have_distinct_references(tmp_path):
    from clinpgx_link.content.store import ContentStore

    raw = b"abc"
    store = ContentStore(tmp_path / "content.sqlite", max_bytes=100, max_entries=3)
    first = store.put(raw, source(raw), "text/plain")
    second_source = replace(
        source(raw), url="https://api.clinpgx.org/v1/site/gene/PA124", data_source="website"
    )
    second = store.put(raw, second_source, "text/plain")
    assert first != second
    assert store.get(first).source.url != store.get(second).source.url
    store.close()


def test_symlink_database_is_never_opened(tmp_path):
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.exceptions import InvalidInputError

    target = tmp_path / "target"
    target.write_bytes(b"do not touch")
    link = tmp_path / "content.sqlite"
    link.symlink_to(target)
    with pytest.raises(InvalidInputError):
        ContentStore(link)
    assert target.read_bytes() == b"do not touch"


@pytest.mark.parametrize("reference", ["../../secret", "content:bad", "https://example.org", ""])
def test_invalid_reference_never_becomes_a_path_or_url(tmp_path, reference):
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.exceptions import InvalidInputError

    store = ContentStore(tmp_path / "content.sqlite")
    with pytest.raises(InvalidInputError):
        store.get(reference)
    store.close()


def test_tampered_body_is_not_returned_as_source_truth(tmp_path):
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.exceptions import DataValidationError

    path = tmp_path / "content.sqlite"
    store = ContentStore(path)
    ref = store.put(b"original", source(b"original"), "text/plain")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE content SET body = ? WHERE reference = ?", (b"tampered", ref))
    with pytest.raises(DataValidationError):
        store.get(ref)
    store.close()


def test_two_connections_cannot_overbook_retention_capacity(tmp_path):
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.exceptions import RateLimitedError

    path = tmp_path / "content.sqlite"
    stores = [ContentStore(path, max_bytes=3, max_entries=1) for _ in range(2)]
    ready = threading.Barrier(2)

    def admit(index):
        raw = [b"one", b"two"][index]
        ready.wait(timeout=5)
        try:
            return stores[index].put(raw, source(raw), "text/plain")
        except RateLimitedError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        refs = list(executor.map(admit, range(2)))
    successful = [ref for ref in refs if ref is not None]
    assert len(successful) == 1
    assert stores[0].get(successful[0]).raw in {b"one", b"two"}
    for store in stores:
        store.close()
