"""Offline operator installation never depends on server startup settings."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from tests.unit.test_materialize import _private, _release

runner = CliRunner()


def _input_args(release: object, root: Path, directory: Path) -> list[str]:
    item = release.release_input  # type: ignore[attr-defined]
    manifest = directory / "manifest.json"
    manifest.write_bytes(item.manifest_bytes)
    return [
        "data",
        "install",
        "--manifest",
        str(manifest),
        "--manifest-sha256",
        item.expected_manifest_sha256,
        "--artifact",
        str(item.artifact_path),
        "--data-root",
        str(root),
    ]


def test_offline_cli_import_does_not_require_production_server_settings(tmp_path: Path) -> None:
    env = {**os.environ, "CLINPGX_RUNTIME_MODE": "production"}
    env.pop("CLINPGX_EXPECTED_SNAPSHOT", None)
    env.pop("CLINPGX_EXPECTED_RELEASE_TAG", None)
    env.pop("CLINPGX_EXPECTED_RUNTIME_DIGEST", None)
    result = subprocess.run(
        [sys.executable, "-c", "from clinpgx_link.cli import app; print('imported')"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "imported"


def test_offline_install_checks_pinned_manifest_and_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from clinpgx_link.cli import app

    release = _release(tmp_path, "first")
    root = _private(tmp_path / "installed")
    args = _input_args(release, root, tmp_path)
    monkeypatch.setenv("CLINPGX_RUNTIME_MODE", "production")
    monkeypatch.delenv("CLINPGX_EXPECTED_SNAPSHOT", raising=False)
    monkeypatch.setattr("clinpgx_link.operator_install.application_version", lambda: "1.0.0")

    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.stdout
    payload = json.loads(first.stdout)
    assert payload["release_tag"] == release.tag
    assert payload["artifact_sha256"] == release.artifact_digest
    assert "path" not in payload
    assert runner.invoke(app, args).exit_code == 0


def test_offline_install_rejects_wrong_digest_and_hides_paths(tmp_path: Path, monkeypatch) -> None:
    from clinpgx_link.cli import app

    release = _release(tmp_path, "first")
    root = _private(tmp_path / "installed")
    args = _input_args(release, root, tmp_path)
    args[args.index("--manifest-sha256") + 1] = "0" * 64
    monkeypatch.setattr("clinpgx_link.operator_install.application_version", lambda: "1.0.0")
    result = runner.invoke(app, args)
    assert result.exit_code != 0
    assert str(tmp_path) not in result.stdout
    assert not (root / "current").exists()


def test_offline_install_rejects_incompatible_application_and_schema(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from clinpgx_link.cli import app

    release = _release(tmp_path, "first")
    root = _private(tmp_path / "installed")
    args = _input_args(release, root, tmp_path)
    monkeypatch.setattr("clinpgx_link.operator_install.application_version", lambda: "0.1.2")
    assert runner.invoke(app, args).exit_code != 0
    monkeypatch.setattr("clinpgx_link.operator_install.application_version", lambda: "1.0.0")
    manifest = tmp_path / "manifest.json"
    value = json.loads(manifest.read_bytes())
    value["schema"] = {"minimum": "2.0.0", "maximum": "2.0.0", "actual": "2.0.0"}
    raw = json.dumps(value, separators=(",", ":")).encode()
    manifest.write_bytes(raw)
    args[args.index("--manifest-sha256") + 1] = hashlib.sha256(raw).hexdigest()
    assert runner.invoke(app, args).exit_code != 0
    assert not (root / "current").exists()


def test_offline_install_rejects_fifo_without_blocking(tmp_path: Path, monkeypatch) -> None:
    from clinpgx_link.cli import app

    release = _release(tmp_path, "first")
    root = _private(tmp_path / "installed")
    args = _input_args(release, root, tmp_path)
    fifo = tmp_path / "blocking-manifest"
    os.mkfifo(fifo)
    args[args.index("--manifest") + 1] = str(fifo)
    monkeypatch.setattr("clinpgx_link.operator_install.application_version", lambda: "1.0.0")
    result = runner.invoke(app, args)
    assert result.exit_code != 0
    assert str(fifo) not in result.stdout
    assert not (root / "current").exists()


def test_offline_install_requires_previous_pinned_release_for_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from clinpgx_link.cli import app

    first = _release(tmp_path, "first")
    second = _release(tmp_path, "second", previous=f"sha256:{first.artifact_digest}")
    root = _private(tmp_path / "installed")
    monkeypatch.setattr("clinpgx_link.operator_install.application_version", lambda: "1.0.0")
    assert runner.invoke(app, _input_args(first, root, tmp_path)).exit_code == 0
    second_args = _input_args(second, root, tmp_path)
    assert runner.invoke(app, second_args).exit_code != 0
    first_item = first.release_input  # type: ignore[attr-defined]
    previous = tmp_path / "previous-manifest.json"
    previous.write_bytes(first_item.manifest_bytes)
    second_args += [
        "--previous-manifest",
        str(previous),
        "--previous-manifest-sha256",
        first_item.expected_manifest_sha256,
    ]
    result = runner.invoke(app, second_args)
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["release_tag"] == second.tag


def test_offline_replacement_can_stage_predecessor_in_new_volume(
    tmp_path: Path, monkeypatch
) -> None:
    from clinpgx_link.cli import app

    first = _release(tmp_path, "first")
    second = _release(tmp_path, "second", previous=f"sha256:{first.artifact_digest}")
    new_root = _private(tmp_path / "new-volume")
    monkeypatch.setattr("clinpgx_link.operator_install.application_version", lambda: "1.0.0")
    args = _input_args(second, new_root, tmp_path)
    first_item = first.release_input  # type: ignore[attr-defined]
    previous = tmp_path / "previous-manifest.json"
    previous.write_bytes(first_item.manifest_bytes)
    args += [
        "--previous-manifest",
        str(previous),
        "--previous-manifest-sha256",
        first_item.expected_manifest_sha256,
        "--previous-artifact",
        str(first_item.artifact_path),
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["release_tag"] == second.tag
    assert (new_root / "versions" / first.artifact_digest).is_dir()
    assert (new_root / "versions" / second.artifact_digest).is_dir()
