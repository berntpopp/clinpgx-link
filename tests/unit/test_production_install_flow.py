"""Offline verified installation must feed the actual HTTP production boundary."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit import test_materialize

_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
    "host": "testserver",
}


def _catalog_database(path: Path, **kwargs: object) -> None:
    """Extend the tiny release fixture with the actual catalog read contract."""
    _ORIGINAL_DATABASE(path, **kwargs)  # type: ignore[arg-type]
    path.chmod(0o600)
    with sqlite3.connect(path) as connection:
        for name in (
            "file_name",
            "source_url",
            "retrieved_at",
            "published_at",
            "media_type",
            "tier",
            "etag",
            "last_modified",
            "version_id",
            "limitations_json",
            "warnings_json",
        ):
            connection.execute(f"ALTER TABLE dataset ADD COLUMN {name} TEXT")
        connection.execute(
            "UPDATE dataset SET file_name='genes.zip', source_url='https://example.test/genes', "
            "retrieved_at='2026-10-04T00:00:00Z', media_type='application/zip', "
            "tier='core', limitations_json='[]', warnings_json='[]'"
        )
        connection.execute("ALTER TABLE record ADD COLUMN record_id TEXT")
        connection.execute("UPDATE record SET record_id='synthetic-record-1'")
    path.chmod(0o444)


_ORIGINAL_DATABASE = test_materialize._database


def _call(client: TestClient) -> dict:
    response = client.post(
        "/mcp",
        headers=_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_datasets", "arguments": {}},
        },
    )
    assert response.status_code == 200
    return response.json()["result"]


@pytest.mark.parametrize("running_version", [None, "9.0.0"])
def test_cli_install_serves_pinned_catalog_then_rejects_generation_drift(
    tmp_path: Path,
    monkeypatch,
    running_version: str | None,
) -> None:
    from clinpgx_link.cli import app
    from clinpgx_link.config import Settings
    from clinpgx_link.server_manager import create_app

    monkeypatch.setattr(test_materialize, "_database", _catalog_database)
    release = test_materialize._release(tmp_path, "full-flow", application_minimum="0.1.0")
    item = release.release_input
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(item.manifest_bytes)
    root = test_materialize._private(tmp_path / "data")
    installed = CliRunner().invoke(
        app,
        [
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
        ],
    )
    assert installed.exit_code == 0, installed.stdout
    receipt = json.loads(installed.stdout)
    generation = (root / "current").resolve(strict=True)
    with sqlite3.connect(f"file:{generation / 'clinpgx.sqlite'}?mode=ro", uri=True) as db:
        snapshot = db.execute("SELECT value FROM metadata WHERE key='snapshot_id'").fetchone()[0]
    settings = Settings(
        _env_file=None,
        cache_root=tmp_path / "cache",
        data_root=root,
        snapshot_path=root / "current/clinpgx.sqlite",
        allowed_hosts=("testserver",),
        runtime_mode="production",
        expected_snapshot=snapshot,
        expected_release_tag=release.tag,
        expected_runtime_digest=receipt["runtime_digest"],
    )
    if running_version is not None:
        monkeypatch.setattr("clinpgx_link.server_manager.__version__", running_version)
        with pytest.raises(Exception, match="incompatible"):
            with TestClient(create_app(settings)):
                pass
        return
    with TestClient(create_app(settings)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        identity = health.json()["release_identity"]["data_identity"]
        assert (
            identity["expected"]
            == identity["actual"]
            == {
                "release_tag": release.tag,
                "digest": settings.expected_runtime_digest,
            }
        )
        result = _call(client)
        assert result["isError"] is False
        assert result["structuredContent"]["results"][0]["dataset_id"] == "data/genes.zip"
        changed = generation / "licenses.json"
        changed.chmod(0o644)
        changed.write_bytes(b"changed")
        changed.chmod(0o444)
        assert client.get("/health").status_code == 503
        result = _call(client)
        assert result["isError"] is True
        assert result["structuredContent"]["error_code"] == "upstream_unavailable"
