from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases import runtime_identity as identity_module
from clinpgx_link.releases.runtime_identity import (
    build_runtime_identity,
    canonical_runtime_json,
    verify_runtime_identity,
)

TAG = "data-clinpgx-core-golden"
MANIFEST_NAME = "data-identity-manifest.json"
FILES = {
    "clinpgx.sqlite": b"sqlite-fixture\n",
    "schema.json": b'{"schema":"1.0.0"}\n',
    "source-manifest.json": b'{"sources":["api"]}\n',
    "licenses.json": b'{"licenses":[]}\n',
    "materialization.json": b'{"profile":"core"}\n',
}
GOLDEN_MANIFEST: dict[str, object] = {
    "schema_version": 1,
    "release_tag": TAG,
    "inputs": [
        {
            "path": "clinpgx.sqlite",
            "size_bytes": 15,
            "sha256": "63233326a6d9ea026b6e3ee319ecbcf7ef92c6cc3fd4bcd651b5cd28dbbb89e4",
        },
        {
            "path": "licenses.json",
            "size_bytes": 16,
            "sha256": "c7cd6b30bc50053f32aa9d15c3bd6ba99538caf601f3ca02c98d4630ba687672",
        },
        {
            "path": "materialization.json",
            "size_bytes": 19,
            "sha256": "88537206f32a69c06aa6c193138394390997f36b075720f0849d5f4fdc1832c0",
        },
        {
            "path": "schema.json",
            "size_bytes": 19,
            "sha256": "43b22117e9bc541e4b514dbfc73cf99f26e502ed7ae24451f7d56865a5c1271f",
        },
        {
            "path": "source-manifest.json",
            "size_bytes": 20,
            "sha256": "cd27375bf822a962a1170f716aee4d04925cbed5a54a1daec0b82c778bfd692d",
        },
    ],
}
GOLDEN_DIGEST = "sha256:463770a6ef187ccd62c4481f7564cb7dff884aa962ed20214583209739b6c55a"


def _tree(path: Path) -> Path:
    path.mkdir(mode=0o700)
    for name, raw in FILES.items():
        target = path / name
        target.write_bytes(raw)
        target.chmod(0o444)
    return path


def _write_manifest(root: Path, value: object = GOLDEN_MANIFEST) -> None:
    target = root / MANIFEST_NAME
    target.write_bytes(json.dumps(value).encode("utf-8"))
    target.chmod(0o444)


def _verified_tree(tmp_path: Path) -> Path:
    root = _tree(tmp_path / "version")
    _write_manifest(root)
    return root


def test_canonical_runtime_json_matches_fleet_bytes_without_newline() -> None:
    assert canonical_runtime_json({"z": "München", "a": {"finite": 1.25}}) == (
        b'{"a":{"finite":1.25},"z":"M\xc3\xbcnchen"}'
    )
    with pytest.raises(DataValidationError, match="canonical"):
        canonical_runtime_json({"value": float("nan")})


def test_build_matches_hand_specified_runtime_v1_golden(tmp_path: Path) -> None:
    root = _tree(tmp_path / "version")

    manifest = build_runtime_identity(root, TAG)

    assert manifest == GOLDEN_MANIFEST
    assert hashlib.sha256(canonical_runtime_json(manifest)).hexdigest() == GOLDEN_DIGEST[7:]


def test_verify_requires_pins_and_rehashes_the_exact_tree(tmp_path: Path) -> None:
    root = _verified_tree(tmp_path)

    assert verify_runtime_identity(
        root, expected_digest=GOLDEN_DIGEST, expected_release_tag=TAG
    ) == {"release_tag": TAG, "digest": GOLDEN_DIGEST}


@pytest.mark.parametrize("name", sorted(FILES))
def test_each_authoritative_file_mutation_changes_identity(tmp_path: Path, name: str) -> None:
    root = _tree(tmp_path / "version")
    original = build_runtime_identity(root, TAG)
    target = root / name
    target.chmod(0o600)
    target.write_bytes(FILES[name] + b"mutation")
    target.chmod(0o444)

    assert build_runtime_identity(root, TAG) != original


def test_tag_changes_identity_and_directory_path_does_not(tmp_path: Path) -> None:
    first = _tree(tmp_path / "one")
    second = _tree(tmp_path / "two")

    assert build_runtime_identity(first, TAG) == build_runtime_identity(second, TAG)
    assert build_runtime_identity(first, TAG + "-next") != build_runtime_identity(first, TAG)


@pytest.mark.parametrize("operation", ["build", "verify"])
@pytest.mark.parametrize("change", ["missing", "extra", "directory"])
def test_exact_root_inventory_is_required(tmp_path: Path, operation: str, change: str) -> None:
    root = _verified_tree(tmp_path)
    if change == "missing":
        (root / "schema.json").unlink()
    elif change == "extra":
        extra = root / "surprise.txt"
        extra.write_bytes(b"no")
        extra.chmod(0o444)
    else:
        (root / "nested").mkdir()

    with pytest.raises(DataValidationError, match="inventory"):
        if operation == "build":
            build_runtime_identity(root, TAG)
        else:
            verify_runtime_identity(root, expected_digest=GOLDEN_DIGEST, expected_release_tag=TAG)


@pytest.mark.parametrize(
    "raw",
    [
        b"{broken",
        b'{"schema_version":1,"schema_version":1,"release_tag":"x","inputs":[]}',
        b'{"schema_version":1,"release_tag":"x","inputs":[],"number":NaN}',
        b"\xff",
    ],
    ids=["syntax", "duplicate", "nonfinite", "utf8"],
)
def test_corrupt_identity_sidecar_is_rejected(tmp_path: Path, raw: bytes) -> None:
    root = _tree(tmp_path / "version")
    target = root / MANIFEST_NAME
    target.write_bytes(raw)
    target.chmod(0o444)

    with pytest.raises(DataValidationError, match="manifest"):
        verify_runtime_identity(root, expected_digest=GOLDEN_DIGEST, expected_release_tag=TAG)


def test_oversize_identity_sidecar_is_rejected_before_decode(tmp_path: Path) -> None:
    root = _tree(tmp_path / "version")
    target = root / MANIFEST_NAME
    target.write_bytes(b" " * (64 * 1024 + 1))
    target.chmod(0o444)

    with pytest.raises(DataValidationError, match="byte limit") as failure:
        verify_runtime_identity(root, expected_digest=GOLDEN_DIGEST, expected_release_tag=TAG)
    assert failure.value.subtype == "resource_limit"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unexpected": 1}),
        lambda value: value.update({"schema_version": True}),
        lambda value: value.update({"release_tag": "latest"}),
        lambda value: value.update({"inputs": list(reversed(value["inputs"]))}),
        lambda value: value["inputs"][0].update({"size_bytes": True}),
        lambda value: value["inputs"][0].update({"path": "unknown"}),
    ],
    ids=["fields", "bool-version", "mutable-tag", "order", "bool-size", "wrong-inventory"],
)
def test_manifest_requires_exact_runtime_v1_shape(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> None:
    root = _tree(tmp_path / "version")
    value = json.loads(json.dumps(GOLDEN_MANIFEST))
    mutate(value)
    _write_manifest(root, value)

    with pytest.raises(DataValidationError, match="manifest"):
        verify_runtime_identity(root, expected_digest=GOLDEN_DIGEST, expected_release_tag=TAG)


@pytest.mark.parametrize(
    "kind", ["root-symlink", "file-symlink", "hardlink", "manifest-symlink", "manifest-hardlink"]
)
def test_filesystem_aliases_are_rejected(tmp_path: Path, kind: str) -> None:
    root = _verified_tree(tmp_path)
    selected = root
    if kind == "root-symlink":
        selected = tmp_path / "alias"
        selected.symlink_to(root, target_is_directory=True)
    elif kind == "file-symlink":
        target = root / "schema.json"
        target.unlink()
        target.symlink_to(root / "licenses.json")
    elif kind == "hardlink":
        target = root / "schema.json"
        target.unlink()
        os.link(root / "licenses.json", target)
    else:
        target = root / MANIFEST_NAME
        raw = target.read_bytes()
        target.unlink()
        outside = tmp_path / "outside-manifest.json"
        outside.write_bytes(raw)
        outside.chmod(0o444)
        if kind == "manifest-symlink":
            target.symlink_to(outside)
        else:
            os.link(outside, target)

    with pytest.raises(DataValidationError, match="safe"):
        verify_runtime_identity(selected, expected_digest=GOLDEN_DIGEST, expected_release_tag=TAG)


@pytest.mark.parametrize("name", ["schema.json", MANIFEST_NAME])
def test_special_files_are_rejected(tmp_path: Path, name: str) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO is unavailable")
    root = _verified_tree(tmp_path)
    (root / name).unlink()
    os.mkfifo(root / name, mode=0o400)

    with pytest.raises(DataValidationError, match="safe"):
        verify_runtime_identity(root, expected_digest=GOLDEN_DIGEST, expected_release_tag=TAG)


@pytest.mark.parametrize("target", ["root", "input", "input-setid", "manifest"])
def test_unsafe_modes_are_rejected(tmp_path: Path, target: str) -> None:
    root = _verified_tree(tmp_path)
    path = {
        "root": root,
        "input": root / "schema.json",
        "input-setid": root / "schema.json",
        "manifest": root / MANIFEST_NAME,
    }[target]
    path.chmod(0o777 if target == "root" else 0o4444 if target == "input-setid" else 0o644)

    with pytest.raises(DataValidationError, match="mode"):
        verify_runtime_identity(root, expected_digest=GOLDEN_DIGEST, expected_release_tag=TAG)


@pytest.mark.parametrize(
    ("digest", "tag"),
    [
        ("0" * 64, TAG),
        ("sha256:" + "A" * 64, TAG),
        (GOLDEN_DIGEST, "latest"),
        (GOLDEN_DIGEST, "bad/tag"),
    ],
)
def test_expected_identity_pins_are_strict(tmp_path: Path, digest: str, tag: str) -> None:
    root = _verified_tree(tmp_path)

    with pytest.raises(DataValidationError, match="expected"):
        verify_runtime_identity(root, expected_digest=digest, expected_release_tag=tag)


@pytest.mark.parametrize("mismatch", ["digest", "tag", "file"])
def test_identity_mismatch_fails_closed(tmp_path: Path, mismatch: str) -> None:
    root = _verified_tree(tmp_path)
    digest, tag = GOLDEN_DIGEST, TAG
    if mismatch == "digest":
        digest = "sha256:" + "0" * 64
    elif mismatch == "tag":
        tag += "-other"
    else:
        target = root / "schema.json"
        target.chmod(0o600)
        target.write_bytes(b"changed")
        target.chmod(0o444)

    with pytest.raises(DataValidationError, match="mismatch"):
        verify_runtime_identity(root, expected_digest=digest, expected_release_tag=tag)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("size_bytes", 999), ("sha256", "0" * 64)],
)
def test_manifest_file_metadata_must_match_actual_bytes(
    tmp_path: Path, field: str, replacement: object
) -> None:
    root = _tree(tmp_path / "version")
    value = json.loads(json.dumps(GOLDEN_MANIFEST))
    value["inputs"][0][field] = replacement
    _write_manifest(root, value)

    with pytest.raises(DataValidationError, match="mismatch"):
        verify_runtime_identity(root, expected_digest=GOLDEN_DIGEST, expected_release_tag=TAG)


def test_per_file_and_aggregate_byte_ceilings_are_enforced(tmp_path: Path) -> None:
    root = _tree(tmp_path / "version")

    with pytest.raises(DataValidationError, match="byte limit"):
        build_runtime_identity(root, TAG, max_file_bytes=19)
    with pytest.raises(DataValidationError, match="byte limit"):
        build_runtime_identity(root, TAG, max_total_bytes=sum(map(len, FILES.values())) - 1)


def test_observed_file_mutation_during_descriptor_read_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path / "version")
    original_read = identity_module.os.read
    changed = False

    def mutating_read(fd: int, count: int) -> bytes:
        nonlocal changed
        raw = original_read(fd, count)
        if raw and not changed:
            changed = True
            os.utime(root / "clinpgx.sqlite", ns=(1, 1))
        return raw

    monkeypatch.setattr(identity_module.os, "read", mutating_read)

    with pytest.raises(DataValidationError, match="changed"):
        build_runtime_identity(root, TAG)


def test_manifest_mutation_during_verification_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _verified_tree(tmp_path)
    original = identity_module._read_manifest_bytes
    calls = 0

    def changing_manifest(root_fd: int) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 2:
            target = root / MANIFEST_NAME
            target.chmod(0o600)
            target.write_bytes(json.dumps(GOLDEN_MANIFEST, indent=2).encode())
            target.chmod(0o444)
        return original(root_fd)

    monkeypatch.setattr(identity_module, "_read_manifest_bytes", changing_manifest)

    with pytest.raises(DataValidationError, match="changed"):
        verify_runtime_identity(root, expected_digest=GOLDEN_DIGEST, expected_release_tag=TAG)


def test_build_validates_an_existing_sidecar_but_does_not_write_one(tmp_path: Path) -> None:
    root = _tree(tmp_path / "version")
    assert build_runtime_identity(root, TAG) == GOLDEN_MANIFEST
    assert not (root / MANIFEST_NAME).exists()
    _write_manifest(root)
    (root / MANIFEST_NAME).chmod(stat.S_IRUSR | stat.S_IWUSR)

    with pytest.raises(DataValidationError, match="mode"):
        build_runtime_identity(root, TAG)
