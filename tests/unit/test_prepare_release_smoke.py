"""The release smoke downloads only the fixed, hash-pinned external data bundle."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parents[2] / "docker" / "prepare_release_smoke.py"
_SPEC = importlib.util.spec_from_file_location("prepare_release_smoke", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
prepare_smoke: ModuleType = _MODULE


class _Response(io.BytesIO):
    headers: dict[str, str] = {}

    def __init__(self, body: bytes, final_url: str = "https://github.com/assets") -> None:
        super().__init__(body)
        self._final_url = final_url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def geturl(self) -> str:
        return self._final_url


def _fixture_manifest(artifact: bytes) -> bytes:
    value = {
        "schema_version": 1,
        "dataset": {
            "release": "test-clinpgx-data",
            "source": {"sha256": "1" * 64},
        },
        "artifact": {
            "filename": "clinpgx-core.tar.zst",
            "sha256": hashlib.sha256(artifact).hexdigest(),
            "compressed_size": len(artifact),
            "max_compressed_size": 100,
            "expanded_tree_sha256": "2" * 64,
            "expanded_size": 8,
            "max_expanded_size": 2_147_483_648,
            "member_count": 4,
            "max_members": 4,
        },
        "schema": {"actual": "1.0.0", "minimum": "1.0.0", "maximum": "1.0.0"},
        "application_compatibility": {"minimum": "0.1.3", "maximum": "0.1.3"},
        "record_counts": {"record": 7},
        "license": {"redistribution_allowed": True},
    }
    return json.dumps(value, separators=(",", ":")).encode()


def _configure_fixture(monkeypatch: pytest.MonkeyPatch, artifact: bytes) -> bytes:
    manifest = _fixture_manifest(artifact)
    monkeypatch.setattr(prepare_smoke, "RELEASE_TAG", "test-clinpgx-data")
    monkeypatch.setattr(prepare_smoke, "BASE_URL", "https://github.com/test-clinpgx-data")
    monkeypatch.setattr(prepare_smoke, "MANIFEST_SHA256", hashlib.sha256(manifest).hexdigest())
    monkeypatch.setattr(prepare_smoke, "ARTIFACT_SHA256", hashlib.sha256(artifact).hexdigest())
    monkeypatch.setattr(prepare_smoke, "ARTIFACT_SIZE", len(artifact))
    monkeypatch.setattr(prepare_smoke, "MAX_ARTIFACT_SIZE", 100)
    monkeypatch.setattr(prepare_smoke, "SOURCE_MANIFEST_SHA256", "1" * 64)
    monkeypatch.setattr(prepare_smoke, "EXPANDED_TREE_SHA256", "2" * 64)
    monkeypatch.setattr(prepare_smoke, "EXPANDED_SIZE", 8)
    monkeypatch.setattr(prepare_smoke, "RECORD_COUNT", 7)
    return manifest


def test_smoke_prep_materializes_exact_bundle_and_emits_input_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = b"approved bytes"
    manifest = _configure_fixture(monkeypatch, artifact)
    assets = {"data-release-manifest.json": manifest, "clinpgx-core.tar.zst": artifact}
    monkeypatch.setattr(
        prepare_smoke,
        "urlopen",
        lambda request, timeout: _Response(assets[request.full_url.rsplit("/", 1)[-1]]),
    )
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    env_file = tmp_path / "smoke.env"
    env_file.touch()

    result = prepare_smoke.prepare(fixture_dir, env_file)

    assert (result / "data-release-manifest.json").read_bytes() == manifest
    assert (result / "clinpgx-core.tar.zst").read_bytes() == artifact
    assert (result / "clinpgx-core.tar.zst").stat().st_mode & 0o777 == 0o444
    assert env_file.read_text() == (
        "CLINPGX_EXPECTED_SNAPSHOT=sha256:cbc4bba16380e8badfb05eb402bfbd47e631aba74a83f83dc239d75b75113e3b\n"
        "CLINPGX_EXPECTED_RELEASE_TAG=test-clinpgx-data\n"
        "CLINPGX_EXPECTED_RUNTIME_DIGEST=sha256:ef590d6eba25423eb562405edfa7ac41862172bc689f496c2ff5346ddd3958ca\n"
        f"CLINPGX_RELEASE_MANIFEST_SHA256={hashlib.sha256(manifest).hexdigest()}\n"
        f"CLINPGX_RELEASE_ARTIFACT_SHA256={hashlib.sha256(artifact).hexdigest()}\n"
        f"CLINPGX_RELEASE_INPUT_DIR={result}\n"
    )


def test_smoke_prep_rejects_untrusted_redirect_and_asset_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = b"approved bytes"
    manifest = _configure_fixture(monkeypatch, artifact)
    manifest_response = _Response(manifest)
    bad_redirect = _Response(artifact, "https://attacker.invalid/data")
    monkeypatch.setattr(
        prepare_smoke,
        "urlopen",
        lambda request, timeout: (
            manifest_response
            if request.full_url.endswith("data-release-manifest.json")
            else bad_redirect
        ),
    )
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    env_file = tmp_path / "smoke.env"
    env_file.touch()

    with pytest.raises(prepare_smoke.SmokePreparationError, match="redirected"):
        prepare_smoke.prepare(fixture_dir, env_file)
    assert not env_file.read_text()
    assert not list(fixture_dir.iterdir())

    bad_asset = _Response(b"different bytes")
    monkeypatch.setattr(prepare_smoke, "urlopen", lambda request, timeout: bad_asset)
    with pytest.raises(prepare_smoke.SmokePreparationError, match="digest"):
        prepare_smoke.prepare(fixture_dir, env_file)
    assert not env_file.read_text()
    assert not list(fixture_dir.iterdir())
