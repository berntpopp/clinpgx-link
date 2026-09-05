"""Bounded local acquisition and safe archive-member handling."""

from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from pathlib import Path

import pytest


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    return output.getvalue()


def _source(path: Path):
    from clinpgx_link.data.catalog import SourceInput

    return SourceInput.from_path(
        dataset_id=f"data/{path.name}",
        path=path,
        source_url=f"https://api.clinpgx.org/v1/download/file/data/{path.name}",
        retrieved_at="2026-09-05T08:00:00Z",
        published_at="2026-09-05T00:37:36-07:00",
        media_type="application/zip",
        license_id="operator-local-only",
        tier="approved_registry",
    )


def test_archive_reader_preserves_exact_archive_and_member_bytes(tmp_path: Path) -> None:
    """Catch reserialization that breaks retained source identity or historical retrieval."""
    from clinpgx_link.ingest.acquire import ArchiveLimits, read_local_source

    members = {"genes.tsv": b"ID\tSymbol\nPA124\tCYP2C19\n", "LICENSE.txt": b"terms\r\n"}
    archive_raw = _zip_bytes(members)
    path = tmp_path / "genes.zip"
    path.write_bytes(archive_raw)

    acquired = read_local_source(_source(path), limits=ArchiveLimits.for_tests())

    assert acquired.archive_bytes == archive_raw
    assert acquired.sha256 == hashlib.sha256(archive_raw).hexdigest()
    assert [member.path for member in acquired.members] == ["LICENSE.txt", "genes.tsv"]
    assert {member.path: member.raw for member in acquired.members} == members
    assert all(
        member.sha256 == hashlib.sha256(member.raw).hexdigest() for member in acquired.members
    )


@pytest.mark.parametrize(
    "member_name",
    [
        "../escape.tsv",
        "/absolute.tsv",
        "nested/../../escape.tsv",
        "nested\\evil.tsv",
        "./genes.tsv",
    ],
)
def test_archive_reader_rejects_noncanonical_or_unsafe_member_names(
    tmp_path: Path, member_name: str
) -> None:
    """Catch ZIP members that could escape or alias the canonical member namespace."""
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.ingest.acquire import ArchiveLimits, read_local_source

    path = tmp_path / "unsafe.zip"
    path.write_bytes(_zip_bytes({member_name: b"content"}))
    with pytest.raises(DataValidationError):
        read_local_source(_source(path), limits=ArchiveLimits.for_tests())


def test_archive_reader_rejects_symlink_member(tmp_path: Path) -> None:
    """Catch a ZIP symlink that would make extraction semantics path-dependent."""
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.ingest.acquire import ArchiveLimits, read_local_source

    output = io.BytesIO()
    info = zipfile.ZipInfo("genes.tsv")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(info, "../../secret")
    path = tmp_path / "symlink.zip"
    path.write_bytes(output.getvalue())

    with pytest.raises(DataValidationError):
        read_local_source(_source(path), limits=ArchiveLimits.for_tests())


@pytest.mark.parametrize(
    ("limits", "members"),
    [
        ({"max_archive_bytes": 10}, {"one.tsv": b"x" * 20}),
        ({"max_member_bytes": 10}, {"one.tsv": b"x" * 20}),
        ({"max_expanded_bytes": 15}, {"one.tsv": b"x" * 10, "two.tsv": b"y" * 10}),
        ({"max_members": 1}, {"one.tsv": b"x", "two.tsv": b"y"}),
    ],
)
def test_archive_reader_enforces_each_byte_and_count_limit(
    tmp_path: Path, limits: dict[str, int], members: dict[str, bytes]
) -> None:
    """Catch admission based only on compressed size or only on per-member size."""
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.ingest.acquire import ArchiveLimits, read_local_source

    path = tmp_path / "bounded.zip"
    path.write_bytes(_zip_bytes(members))
    configured = ArchiveLimits.for_tests(**limits)
    with pytest.raises(DataValidationError):
        read_local_source(_source(path), limits=configured)


def test_archive_reader_rejects_zip_extension_with_wrong_signature(tmp_path: Path) -> None:
    """Catch parser selection from extension when source bytes are not a ZIP archive."""
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.ingest.acquire import ArchiveLimits, read_local_source

    path = tmp_path / "fake.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(DataValidationError):
        read_local_source(_source(path), limits=ArchiveLimits.for_tests())


def test_archive_reader_preserves_safe_directory_entries_without_extracting(tmp_path: Path) -> None:
    """Catch rejection or silent loss of published ZIP directory members."""
    from clinpgx_link.ingest.acquire import ArchiveLimits, read_local_source

    path = tmp_path / "frequency.zip"
    path.write_bytes(_zip_bytes({"frequencies/": b"", "frequencies/data.tsv": b"ID\nPA1\n"}))
    acquired = read_local_source(_source(path), limits=ArchiveLimits.for_tests())

    directory = next(member for member in acquired.members if member.path == "frequencies/")
    assert directory.raw == b""
    assert directory.is_directory is True
    assert directory.media_type == "application/x-directory"


def test_archive_reader_passes_compressed_cap_into_verified_file_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catch allocating an attacker-replaced file before enforcing the archive cap."""
    from clinpgx_link.data.catalog import SourceInput
    from clinpgx_link.ingest.acquire import ArchiveLimits, read_local_source

    path = tmp_path / "bounded.zip"
    path.write_bytes(_zip_bytes({"data.tsv": b"ID\nPA1\n"}))
    source = _source(path)
    original = SourceInput.read_verified
    observed: list[int | None] = []

    def guarded(self: SourceInput, *, max_bytes: int | None = None) -> bytes:
        observed.append(max_bytes)
        return original(self, max_bytes=max_bytes)

    monkeypatch.setattr(SourceInput, "read_verified", guarded)
    read_local_source(source, limits=ArchiveLimits.for_tests(max_archive_bytes=1024))

    assert observed == [1024]
