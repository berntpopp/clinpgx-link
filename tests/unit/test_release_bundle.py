"""Determinism, authentication, and bounded extraction tests for release bundles."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
from pathlib import Path

import pytest
import zstandard

from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases.bundle import (
    BundleLimits,
    expanded_tree_identity,
    pack_bundle,
    verify_and_extract_bundle,
)
from clinpgx_link.releases.manifest import ArtifactIdentity

_FILES = {
    "clinpgx.sqlite": b"sqlite",
    "licenses.json": b"{}\n",
    "schema.json": b'{"schema":1}\n',
    "source-manifest.json": b'{"sources":[]}\n',
}


def _private(path: Path) -> Path:
    path.mkdir(mode=0o700)
    return path


def _source(path: Path, files: dict[str, bytes] | None = None) -> Path:
    _private(path)
    for name, raw in (files or _FILES).items():
        target = path / name
        target.write_bytes(raw)
        target.chmod(0o444)
    return path


def _expected(path: Path, receipt=None, **updates: object) -> ArtifactIdentity:
    values: dict[str, object] = {
        "filename": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "compressed_size": path.stat().st_size,
        "max_compressed_size": max(path.stat().st_size, 1),
        "expanded_tree_sha256": "0" * 64,
        "expanded_size": 1,
        "max_expanded_size": 1024 * 1024,
        "member_count": 4,
        "max_members": 4,
    }
    if receipt is not None:
        values.update(
            expanded_tree_sha256=receipt.expanded_tree_sha256,
            expanded_size=receipt.expanded_size,
            member_count=receipt.member_count,
        )
    values.update(updates)
    return ArtifactIdentity(**values)


def _raw_tar(
    entries: list[tuple[tarfile.TarInfo, bytes]], *, format: int = tarfile.USTAR_FORMAT
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=format) as archive:
        for info, raw in entries:
            if info.isreg():
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            else:
                archive.addfile(info)
    return output.getvalue()


def _info(name: str, *, mode: int = 0o444, kind: bytes = tarfile.REGTYPE) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.mtime = 1
    info.type = kind
    return info


def _compress(raw: bytes) -> bytes:
    return zstandard.ZstdCompressor(
        level=9,
        threads=0,
        write_checksum=True,
        write_content_size=False,
        write_dict_id=False,
    ).compress(raw)


def _artifact(parent: Path, raw_tar: bytes) -> Path:
    path = parent / "bundle.tar.zst"
    path.write_bytes(_compress(raw_tar))
    path.chmod(0o600)
    return path


def test_bundle_limits_are_strict_frozen_and_exact_inventory() -> None:
    limits = BundleLimits()
    assert limits.max_compressed_bytes == 256 * 1024 * 1024
    assert limits.max_expanded_bytes == 2 * 1024 * 1024 * 1024
    assert limits.max_member_bytes == 2 * 1024 * 1024 * 1024
    assert limits.max_members == 4
    with pytest.raises((AttributeError, TypeError)):
        limits.max_members = 5  # type: ignore[misc]
    for value in (True, 0, -1, 1.5):
        with pytest.raises(DataValidationError):
            BundleLimits(max_compressed_bytes=value)  # type: ignore[arg-type]


def test_expanded_tree_identity_matches_literal_golden(tmp_path: Path) -> None:
    identity = expanded_tree_identity(_source(tmp_path / "source"), limits=BundleLimits())
    literal_listing = (
        b"clinpgx.sqlite\x000444\x006\x000cd8666848bf286d951c3d230e8b6e092fde03c3a080e3454467e496e7b14e78\n"
        b"licenses.json\x000444\x003\x00ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356\n"
        b"schema.json\x000444\x0013\x006b823fa123b900a4139de2101277275af8329f3a3d34c00ef3bf4fc6bf60287e\n"
        b"source-manifest.json\x000444\x0015\x00bee7a88039d6c7375b5ec2d8eb062c1766a9113ff51c2a15c5290d27e4d8841d\n"
    )
    assert hashlib.sha256(literal_listing).hexdigest() == (
        "beab6e5c7c12d5c69248e8dae8ef3621e34b5649972945f78f6fabf2428286bd"
    )
    assert identity.expanded_tree_sha256 == hashlib.sha256(literal_listing).hexdigest()
    assert identity.expanded_size == 37
    assert identity.member_count == 4
    assert tuple(item.path for item in identity.files) == tuple(sorted(_FILES))


def test_pack_is_byte_deterministic_across_directories_and_round_trips(tmp_path: Path) -> None:
    first_parent = _private(tmp_path / "first-parent")
    second_parent = _private(tmp_path / "second-parent")
    first = first_parent / "bundle.tar.zst"
    second = second_parent / "bundle.tar.zst"
    receipt1 = pack_bundle(
        _source(tmp_path / "source-a"),
        first,
        source_date_epoch=1_700_000_000,
        limits=BundleLimits(),
    )
    receipt2 = pack_bundle(
        _source(tmp_path / "source-b"),
        second,
        source_date_epoch=1_700_000_000,
        limits=BundleLimits(),
    )
    assert first.read_bytes() == second.read_bytes()
    assert receipt1 == receipt2
    parameters = zstandard.get_frame_parameters(first.read_bytes()[:18])
    assert parameters.has_checksum is True
    assert parameters.content_size == zstandard.CONTENTSIZE_UNKNOWN
    assert parameters.dict_id == 0
    tar_raw = zstandard.ZstdDecompressor().decompress(
        first.read_bytes(), max_output_size=1024 * 1024
    )
    with tarfile.open(fileobj=io.BytesIO(tar_raw), mode="r:") as archive:
        members = archive.getmembers()
    assert [member.name for member in members] == sorted(_FILES)
    assert all(
        member.mode == 0o444
        and member.uid == 0
        and member.gid == 0
        and member.uname == ""
        and member.gname == ""
        and member.mtime == 1_700_000_000
        for member in members
    )

    extract_parent = _private(tmp_path / "extract-parent")
    extracted = extract_parent / "staged"
    verified = verify_and_extract_bundle(
        first, extracted, _expected(first, receipt1), limits=BundleLimits()
    )
    assert verified == receipt1
    assert {path.name: path.read_bytes() for path in extracted.iterdir()} == _FILES
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o444 for path in extracted.iterdir())


def test_content_and_epoch_affect_only_appropriate_identities(tmp_path: Path) -> None:
    parent = _private(tmp_path / "outputs")
    source = _source(tmp_path / "source")
    one = pack_bundle(source, parent / "one.zst", source_date_epoch=1, limits=BundleLimits())
    two = pack_bundle(source, parent / "two.zst", source_date_epoch=2, limits=BundleLimits())
    assert one.sha256 != two.sha256
    assert one.expanded_tree_sha256 == two.expanded_tree_sha256
    (source / "schema.json").chmod(0o600)
    (source / "schema.json").write_bytes(b'{"schema":2}\n')
    (source / "schema.json").chmod(0o444)
    three = pack_bundle(source, parent / "three.zst", source_date_epoch=1, limits=BundleLimits())
    assert one.sha256 != three.sha256
    assert one.expanded_tree_sha256 != three.expanded_tree_sha256


@pytest.mark.parametrize("epoch", [True, -1, 0o100000000000])
def test_pack_rejects_invalid_ustar_epoch(tmp_path: Path, epoch: int) -> None:
    with pytest.raises(DataValidationError):
        pack_bundle(
            _source(tmp_path / "source"),
            _private(tmp_path / "output") / "bundle.zst",
            source_date_epoch=epoch,
            limits=BundleLimits(),
        )


def test_pack_refuses_existing_output_and_preserves_it(tmp_path: Path) -> None:
    destination = _private(tmp_path / "output") / "bundle.zst"
    destination.write_bytes(b"old")
    with pytest.raises(DataValidationError):
        pack_bundle(
            _source(tmp_path / "source"),
            destination,
            source_date_epoch=1,
            limits=BundleLimits(),
        )
    assert destination.read_bytes() == b"old"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda root: (root / "extra.json").write_bytes(b"extra"),
        lambda root: (root / "schema.json").chmod(0o644),
        lambda root: (root / "schema.json").unlink(),
    ],
)
def test_source_requires_exact_inventory_and_modes(tmp_path: Path, mutate) -> None:
    source = _source(tmp_path / "source")
    mutate(source)
    with pytest.raises(DataValidationError):
        pack_bundle(
            source,
            _private(tmp_path / "output") / "bundle.zst",
            source_date_epoch=1,
            limits=BundleLimits(),
        )


def test_source_rejects_symlink_hardlink_and_fifo(tmp_path: Path) -> None:
    for index, kind in enumerate(("symlink", "hardlink", "fifo")):
        source = _source(tmp_path / f"source-{index}")
        target = source / "schema.json"
        target.chmod(0o600)
        target.unlink()
        if kind == "symlink":
            target.symlink_to(source / "licenses.json")
        elif kind == "hardlink":
            os.link(source / "licenses.json", target)
        else:
            os.mkfifo(target, 0o444)
        with pytest.raises(DataValidationError):
            pack_bundle(
                source,
                _private(tmp_path / f"out-{index}") / "bundle.zst",
                source_date_epoch=1,
                limits=BundleLimits(),
            )


def test_verify_authenticates_compressed_bytes_before_decoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(_private(tmp_path / "artifacts"), b"not consulted")

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("decoder was reached")

    monkeypatch.setattr(zstandard, "ZstdDecompressor", explode)
    expected = _expected(artifact, sha256="0" * 64)
    with pytest.raises(DataValidationError):
        verify_and_extract_bundle(
            artifact,
            _private(tmp_path / "output") / "staged",
            expected,
            limits=BundleLimits(),
        )


@pytest.mark.parametrize("identity_update", [{"sha256": "0" * 64}, {"compressed_size": 1}])
def test_compressed_digest_and_size_mismatch_precede_decoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identity_update: dict[str, object]
) -> None:
    artifact = _artifact(_private(tmp_path / "artifacts"), b"not consulted")
    monkeypatch.setattr(
        zstandard,
        "ZstdDecompressor",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("decoder reached")),
    )
    with pytest.raises(DataValidationError):
        verify_and_extract_bundle(
            artifact,
            _private(tmp_path / "output") / "staged",
            _expected(artifact, **identity_update),
            limits=BundleLimits(),
        )


@pytest.mark.parametrize(
    ("name", "kind", "mode", "format"),
    [
        ("../schema.json", tarfile.REGTYPE, 0o444, tarfile.USTAR_FORMAT),
        ("schema\\json", tarfile.REGTYPE, 0o444, tarfile.USTAR_FORMAT),
        ("schema.json", tarfile.SYMTYPE, 0o444, tarfile.USTAR_FORMAT),
        ("schema.json", tarfile.LNKTYPE, 0o444, tarfile.USTAR_FORMAT),
        ("schema.json", tarfile.CHRTYPE, 0o444, tarfile.USTAR_FORMAT),
        ("nested/", tarfile.DIRTYPE, 0o555, tarfile.USTAR_FORMAT),
        ("schema.json", tarfile.REGTYPE, 0o644, tarfile.USTAR_FORMAT),
        ("schema.json", tarfile.REGTYPE, 0o444, tarfile.PAX_FORMAT),
        ("x" * 101, tarfile.REGTYPE, 0o444, tarfile.GNU_FORMAT),
    ],
)
def test_verifier_rejects_unsafe_type_mode_or_extension_format(
    tmp_path: Path, name: str, kind: bytes, mode: int, format: int
) -> None:
    info = _info(name, mode=mode, kind=kind)
    if format == tarfile.PAX_FORMAT:
        info.pax_headers = {"comment": "extension"}
    artifact = _artifact(_private(tmp_path / "artifacts"), _raw_tar([(info, b"x")], format=format))
    output_parent = _private(tmp_path / "output")
    with pytest.raises(DataValidationError):
        verify_and_extract_bundle(
            artifact, output_parent / "staged", _expected(artifact), limits=BundleLimits()
        )
    assert list(output_parent.iterdir()) == []


@pytest.mark.parametrize("names", [["schema.json", "schema.json"], ["schema.json"], ["unknown"]])
def test_verifier_rejects_duplicate_missing_or_unknown_inventory(
    tmp_path: Path, names: list[str]
) -> None:
    entries = [(_info(name), b"x") for name in names]
    artifact = _artifact(_private(tmp_path / "artifacts"), _raw_tar(entries))
    output_parent = _private(tmp_path / "output")
    with pytest.raises(DataValidationError):
        verify_and_extract_bundle(
            artifact, output_parent / "staged", _expected(artifact), limits=BundleLimits()
        )
    assert list(output_parent.iterdir()) == []


def test_verifier_rejects_nonzero_decoded_tail_and_truncated_tar(tmp_path: Path) -> None:
    entries = [(_info(name), raw) for name, raw in sorted(_FILES.items())]
    clean_tar = _raw_tar(entries)
    parent = _private(tmp_path / "artifacts")
    for index, raw in enumerate((clean_tar + b"nonzero", clean_tar[:2500])):
        artifact = _artifact(parent, raw)
        renamed = parent / f"bundle-{index}.zst"
        artifact.rename(renamed)
        with pytest.raises(DataValidationError):
            verify_and_extract_bundle(
                renamed,
                _private(tmp_path / f"out-{index}") / "staged",
                _expected(renamed),
                limits=BundleLimits(),
            )


def test_verifier_rejects_truncated_zstd_frame(tmp_path: Path) -> None:
    entries = [(_info(name), raw) for name, raw in sorted(_FILES.items())]
    compressed = _compress(_raw_tar(entries))[:-1]
    parent = _private(tmp_path / "artifacts")
    artifact = parent / "bundle.zst"
    artifact.write_bytes(compressed)
    artifact.chmod(0o600)
    with pytest.raises(DataValidationError):
        verify_and_extract_bundle(
            artifact,
            _private(tmp_path / "output") / "staged",
            _expected(
                artifact,
                expanded_tree_sha256=(
                    "beab6e5c7c12d5c69248e8dae8ef3621e34b5649972945f78f6fabf2428286bd"
                ),
                expanded_size=37,
            ),
            limits=BundleLimits(),
        )


def test_artifact_metadata_mutation_during_decode_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clinpgx_link.releases import bundle_io

    source = _source(tmp_path / "source")
    artifact = _private(tmp_path / "artifacts") / "bundle.zst"
    receipt = pack_bundle(source, artifact, source_date_epoch=1, limits=BundleLimits())
    original = bundle_io.revalidate_regular
    calls = 0

    def mutate_after_hash(
        descriptor: int, directory_fd: int, name: str, before: os.stat_result
    ) -> None:
        nonlocal calls
        calls += 1
        original(descriptor, directory_fd, name, before)
        if calls == 1:
            os.utime(artifact, ns=(before.st_atime_ns, before.st_mtime_ns + 1))

    monkeypatch.setattr(bundle_io, "revalidate_regular", mutate_after_hash)
    with pytest.raises(DataValidationError):
        verify_and_extract_bundle(
            artifact,
            _private(tmp_path / "output") / "staged",
            _expected(artifact, receipt),
            limits=BundleLimits(),
        )
    assert calls == 2


def test_source_inventory_mutation_during_pack_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clinpgx_link.releases import bundle_io

    source = _source(tmp_path / "source")
    original = bundle_io.revalidate_regular

    def mutate_after_last_member(
        descriptor: int, directory_fd: int, name: str, before: os.stat_result
    ) -> None:
        original(descriptor, directory_fd, name, before)
        if name == "source-manifest.json":
            (source / "unexpected").write_bytes(b"mutation")

    monkeypatch.setattr(bundle_io, "revalidate_regular", mutate_after_last_member)
    output_parent = _private(tmp_path / "output")
    with pytest.raises(DataValidationError):
        pack_bundle(
            source,
            output_parent / "bundle.zst",
            source_date_epoch=1,
            limits=BundleLimits(),
        )
    assert list(output_parent.iterdir()) == []


def test_source_root_path_replacement_during_pack_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clinpgx_link.releases import bundle_io

    source = _source(tmp_path / "source")
    original = bundle_io.revalidate_regular

    def replace_root_after_last_member(
        descriptor: int, directory_fd: int, name: str, before: os.stat_result
    ) -> None:
        original(descriptor, directory_fd, name, before)
        if name == "source-manifest.json":
            source.rename(tmp_path / "moved-source")
            _source(source)

    monkeypatch.setattr(bundle_io, "revalidate_regular", replace_root_after_last_member)
    output_parent = _private(tmp_path / "output")
    with pytest.raises(DataValidationError):
        pack_bundle(
            source, output_parent / "bundle.zst", source_date_epoch=1, limits=BundleLimits()
        )
    assert list(output_parent.iterdir()) == []


def test_verifier_rejects_expansion_and_member_limits(tmp_path: Path) -> None:
    entries = [(_info(name), raw) for name, raw in sorted(_FILES.items())]
    artifact = _artifact(_private(tmp_path / "artifacts"), _raw_tar(entries))
    limits = BundleLimits(max_expanded_bytes=20, max_member_bytes=10)
    with pytest.raises(DataValidationError):
        verify_and_extract_bundle(
            artifact,
            _private(tmp_path / "output") / "staged",
            _expected(artifact),
            limits=limits,
        )


def test_existing_empty_staging_destination_is_never_replaced(tmp_path: Path) -> None:
    source = _source(tmp_path / "source")
    artifact = _private(tmp_path / "artifacts") / "bundle.zst"
    receipt = pack_bundle(source, artifact, source_date_epoch=1, limits=BundleLimits())
    output_parent = _private(tmp_path / "output")
    destination = _private(output_parent / "staged")
    with pytest.raises(DataValidationError):
        verify_and_extract_bundle(
            artifact, destination, _expected(artifact, receipt), limits=BundleLimits()
        )
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_unsupported_rename_noreplace_leaves_no_published_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clinpgx_link.releases import bundle_io

    source = _source(tmp_path / "source")
    artifact = _private(tmp_path / "artifacts") / "bundle.zst"
    receipt = pack_bundle(source, artifact, source_date_epoch=1, limits=BundleLimits())
    output_parent = _private(tmp_path / "output")

    monkeypatch.setattr(bundle_io, "_renameat2", None)
    with pytest.raises(DataValidationError):
        verify_and_extract_bundle(
            artifact,
            output_parent / "staged",
            _expected(artifact, receipt),
            limits=BundleLimits(),
        )
    assert list(output_parent.iterdir()) == []


def test_rename_noreplace_preserves_destination_created_during_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clinpgx_link.releases import bundle_io

    source = _source(tmp_path / "source")
    artifact = _private(tmp_path / "artifacts") / "bundle.zst"
    receipt = pack_bundle(source, artifact, source_date_epoch=1, limits=BundleLimits())
    output_parent = _private(tmp_path / "output")
    destination = output_parent / "staged"
    original = bundle_io.rename_noreplace

    def race(directory_fd: int, source_name: str, destination_name: str) -> None:
        destination.mkdir()
        original(directory_fd, source_name, destination_name)

    monkeypatch.setattr(bundle_io, "rename_noreplace", race)
    with pytest.raises(DataValidationError):
        verify_and_extract_bundle(
            artifact, destination, _expected(artifact, receipt), limits=BundleLimits()
        )
    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert list(output_parent.iterdir()) == [destination]
