from __future__ import annotations

from pathlib import Path

import pytest

from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases.runtime_identity import build_runtime_identity, canonical_runtime_json


def _installed_generation(root: Path) -> tuple[Path, str, str]:
    tag = "data-clinpgx-core-20261004"
    artifact = "a" * 64
    versions = root / "versions"
    generation = versions / artifact
    generation.mkdir(parents=True)
    files = {
        "clinpgx.sqlite": b"database-placeholder",
        "licenses.json": b"{}",
        "materialization.json": b"{}",
        "schema.json": b"{}",
        "source-manifest.json": b"{}",
    }
    for name, value in files.items():
        path = generation / name
        path.write_bytes(value)
        path.chmod(0o444)
    generation.chmod(0o700)
    manifest = build_runtime_identity(generation, tag)
    digest = "sha256:" + __import__("hashlib").sha256(canonical_runtime_json(manifest)).hexdigest()
    sidecar = generation / "data-identity-manifest.json"
    sidecar.write_bytes(canonical_runtime_json(manifest))
    sidecar.chmod(0o444)
    generation.chmod(0o555)
    root.mkdir(exist_ok=True)
    (root / "current").symlink_to(f"versions/{artifact}")
    return generation, tag, digest


def test_runtime_guard_pins_one_generation_and_rechecks_only_metadata(tmp_path: Path):
    from clinpgx_link.runtime_data_guard import RuntimeDataGuard

    root = tmp_path / "data"
    generation, tag, digest = _installed_generation(root)
    guard = RuntimeDataGuard.start(root, expected_release_tag=tag, expected_digest=digest)

    assert guard.generation == generation
    assert guard.database_path == generation / "clinpgx.sqlite"
    assert guard.is_intact() is True
    next_generation = root / "versions" / ("b" * 64)
    next_generation.mkdir()
    next_generation.chmod(0o555)
    (root / "current").unlink()
    (root / "current").symlink_to(f"versions/{next_generation.name}")
    assert guard.is_intact() is True  # the opened process remains bound to its generation


def test_runtime_guard_refuses_bad_current_pointer_or_identity(tmp_path: Path):
    from clinpgx_link.runtime_data_guard import RuntimeDataGuard

    root = tmp_path / "data"
    generation, tag, digest = _installed_generation(root)
    (root / "current").unlink()
    (root / "current").symlink_to("../outside")
    with pytest.raises(DataValidationError):
        RuntimeDataGuard.start(root, expected_release_tag=tag, expected_digest=digest)
    (root / "current").unlink()
    (root / "current").symlink_to(f"versions/{generation.name}")
    with pytest.raises(DataValidationError):
        RuntimeDataGuard.start(root, expected_release_tag=tag, expected_digest="sha256:" + "0" * 64)


def test_runtime_guard_refuses_versions_symlink_replacement(tmp_path: Path):
    from clinpgx_link.runtime_data_guard import RuntimeDataGuard

    root = tmp_path / "data"
    generation, tag, digest = _installed_generation(root)
    guard = RuntimeDataGuard.start(root, expected_release_tag=tag, expected_digest=digest)
    versions = root / "versions"
    moved = root / "versions-saved"
    versions.rename(moved)
    versions.symlink_to(moved.name)
    assert guard.is_intact() is False
    assert (moved / generation.name).is_dir()


def test_runtime_guard_rejects_change_between_verify_and_fingerprint(tmp_path: Path, monkeypatch):
    from clinpgx_link import runtime_data_guard

    root = tmp_path / "data"
    generation, tag, digest = _installed_generation(root)
    verify = runtime_data_guard.verify_runtime_identity

    def mutate_after_verify(*args, **kwargs):
        result = verify(*args, **kwargs)
        schema = generation / "schema.json"
        schema.chmod(0o644)
        schema.write_text("race", encoding="utf-8")
        schema.chmod(0o444)
        return result

    monkeypatch.setattr(runtime_data_guard, "verify_runtime_identity", mutate_after_verify)
    with pytest.raises(DataValidationError, match="changed during admission"):
        runtime_data_guard.RuntimeDataGuard.start(
            root, expected_release_tag=tag, expected_digest=digest
        )


def test_runtime_guard_detects_bounded_stat_change_without_rehashing(tmp_path: Path, monkeypatch):
    from clinpgx_link.runtime_data_guard import RuntimeDataGuard

    root = tmp_path / "data"
    generation, tag, digest = _installed_generation(root)
    guard = RuntimeDataGuard.start(root, expected_release_tag=tag, expected_digest=digest)
    changed = generation / "schema.json"
    changed.chmod(0o644)
    changed.write_text("changed", encoding="utf-8")
    changed.chmod(0o444)
    assert guard.is_intact() is False
