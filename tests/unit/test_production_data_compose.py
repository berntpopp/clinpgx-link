"""The deployed NPM stack is bound to one offline-installed ClinPGx release."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_FILES = [
    "-f",
    "docker/docker-compose.npm.yml",
    "-f",
    "docker/docker-compose.npm.data.yml",
]
_RELEASE_FILES = ["-f", "docker/docker-compose.release.yml"]
_EXPECTED_ENV = {
    "CLINPGX_EXPECTED_SNAPSHOT": "sha256:cbc4bba16380e8badfb05eb402bfbd47e631aba74a83f83dc239d75b75113e3b",
    "CLINPGX_EXPECTED_RELEASE_TAG": "data-clinpgx-core-cfde21bfec473b35",
    "CLINPGX_EXPECTED_RUNTIME_DIGEST": "sha256:ef590d6eba25423eb562405edfa7ac41862172bc689f496c2ff5346ddd3958ca",
    "CLINPGX_RELEASE_MANIFEST_SHA256": "80e22f9ace76ff136129c8eb5cc67fb1454c291ee2365d7e797e22d2da2845e7",
    "CLINPGX_RELEASE_ARTIFACT_SHA256": "481f30612a1711683d52faea535a29327a88374f12b907d4c942cbefd2c8246b",
}


def _render(env: dict[str, str], files: list[str] = _FILES) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker")
    assert docker is not None
    return subprocess.run(  # noqa: S603 - fixed read-only Compose config command
        [docker, "compose", *files, "config", "--format", "json"],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def _pins() -> dict[str, str]:
    return {
        "CLINPGX_LINK_IMAGE": "ghcr.io/berntpopp/clinpgx-link@sha256:" + "c" * 64,
        "CLINPGX_DATA_VOLUME": "clinpgx_data_test_fixture",
        "CLINPGX_RELEASE_INPUT_DIR": str(_ROOT / "synthetic-clinpgx-release"),
        **_EXPECTED_ENV,
    }


def _smoke_environment(release: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for assignment in release["smoke_environment"]:  # type: ignore[index]
        key, value = assignment.split("=", 1)
        values[key] = value
    return values


def test_deployed_stack_requires_every_immutable_runtime_and_asset_pin() -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("CLINPGX_")}
    env.update(_pins())
    required_smoke_environment = {
        "CLINPGX_LINK_IMAGE",
        "CLINPGX_RELEASE_INPUT_DIR",
        *_EXPECTED_ENV,
    }
    for missing in required_smoke_environment:
        candidate = dict(env)
        candidate.pop(missing)
        result = _render(candidate, _RELEASE_FILES)
        assert result.returncode != 0, missing


def test_release_smoke_compose_uses_same_image_and_offline_seeded_init() -> None:
    env = {**os.environ, **_pins()}
    result = _render(env, _RELEASE_FILES)
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    app = rendered["services"]["clinpgx-link"]
    initializer = rendered["services"]["clinpgx-data-init"]
    assert app["image"] == initializer["image"] == env["CLINPGX_LINK_IMAGE"]
    assert app.get("ports") is None
    assert app["expose"] == ["8000"]
    assert initializer["network_mode"] == "none"
    assert initializer.get("ports") is None
    assert initializer["entrypoint"] == ["clinpgx-link", "data", "install"]
    assert initializer["command"] == [
        "--manifest",
        "/release-input/data-release-manifest.json",
        "--manifest-sha256",
        env["CLINPGX_RELEASE_MANIFEST_SHA256"],
        "--artifact",
        "/release-input/clinpgx-core.tar.zst",
        "--data-root",
        "/data",
    ]
    assert app["depends_on"]["clinpgx-data-init"]["condition"] == "service_completed_successfully"
    assert rendered["volumes"]["clinpgx_data"] == {"name": "clinpgx-link_clinpgx_data"}


def test_release_metadata_adopts_exact_external_runtime_identity() -> None:
    release: dict[str, Any] = json.loads((_ROOT / "container-release.json").read_text())
    data = release["data"]
    assert data["mode"] == "external-reference"
    assert data["release_tag"] == _EXPECTED_ENV["CLINPGX_EXPECTED_RELEASE_TAG"]
    assert data["digest"] == _EXPECTED_ENV["CLINPGX_EXPECTED_RUNTIME_DIGEST"]
    assert data["schema_compatibility"] == ["1.0.0"]
    assert release["definitions"]["contract"] == "data-bound"
    assert release["data_identity_contract"] == "runtime-v1"
    assert release["smoke"]["profile"] == "immutable-bundle"
    smoke = _smoke_environment(release)
    for name in (
        "CLINPGX_EXPECTED_SNAPSHOT",
        "CLINPGX_EXPECTED_RELEASE_TAG",
        "CLINPGX_EXPECTED_RUNTIME_DIGEST",
        "CLINPGX_RELEASE_MANIFEST_SHA256",
        "CLINPGX_RELEASE_ARTIFACT_SHA256",
    ):
        assert smoke[name] == _EXPECTED_ENV[name]
    assert release["preparation"] == "docker/ci-prepare-smoke.sh"


def test_deployed_stack_is_hardened_and_installs_before_read_only_app() -> None:
    env = {**os.environ, **_pins()}
    result = _render(env)
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    app = rendered["services"]["clinpgx_link"]
    initializer = rendered["services"]["clinpgx_data_init"]

    assert app["image"] == initializer["image"] == env["CLINPGX_LINK_IMAGE"]
    assert app["user"] == initializer["user"] == "999:999"
    assert app["read_only"] is initializer["read_only"] is True
    for service in (app, initializer):
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["deploy"]["resources"]["limits"]["pids"] == 256
    assert app["environment"]["CLINPGX_RUNTIME_MODE"] == "production"
    for name in (
        "CLINPGX_EXPECTED_SNAPSHOT",
        "CLINPGX_EXPECTED_RELEASE_TAG",
        "CLINPGX_EXPECTED_RUNTIME_DIGEST",
    ):
        assert app["environment"][name] == env[name]
    assert "CLINPGX_RELEASE_MANIFEST_SHA256" not in app["environment"]
    assert "CLINPGX_RELEASE_ARTIFACT_SHA256" not in app["environment"]
    for name in _EXPECTED_ENV:
        assert initializer["environment"][name] == env[name]

    assert app.get("ports") is None
    assert app["expose"] == ["8000"]
    assert initializer.get("ports") is None
    assert initializer.get("expose") is None
    assert initializer["network_mode"] == "none"
    assert initializer["restart"] == "no"
    assert app["depends_on"]["clinpgx_data_init"]["condition"] == "service_completed_successfully"

    app_data = next(mount for mount in app["volumes"] if mount["target"] == "/data")
    init_data = next(mount for mount in initializer["volumes"] if mount["target"] == "/data")
    seed = next(mount for mount in initializer["volumes"] if mount["target"] == "/release-input")
    assert app_data["source"] == init_data["source"] == "clinpgx_data"
    assert app_data["read_only"] is True
    assert init_data.get("read_only", False) is False
    assert seed["source"] == "/srv/genefoundry/clinpgx-seed"
    assert seed["read_only"] is True
    assert "create_host_path: false" in (_ROOT / "docker/docker-compose.npm.data.yml").read_text()
    assert rendered["volumes"]["clinpgx_data"] == {
        "name": env["CLINPGX_DATA_VOLUME"],
        "external": True,
    }


def test_release_contract_matches_the_actual_deployed_seed_and_sidecar() -> None:
    release: dict[str, Any] = json.loads((_ROOT / "container-release.json").read_text())
    service = release["service"]
    assert service["compose_files"] == ["docker/docker-compose.release.yml"]
    assert service["deployed_compose_files"] == [
        "docker/docker-compose.npm.yml",
        "docker/docker-compose.npm.data.yml",
    ]
    assert service["deployed_seed_binds"] == ["/release-input"]
    assert service["deployed_sidecars"] == []
    assert service["auxiliary"] == [
        {
            "name": "clinpgx-data-init",
            "role": "init",
            "egress": "denied",
            "writable_targets": ["/data", "/tmp"],  # noqa: S108 - only the bounded container tmpfs is writable
            "read_only_targets": ["/release-input"],
        }
    ]
    assert service["deployed_sidecars"] == []


def test_release_compose_files_still_omit_numeric_deploy_user() -> None:
    release: dict[str, Any] = json.loads((_ROOT / "container-release.json").read_text())
    for filename in release["service"]["compose_files"]:
        content = (_ROOT / filename).read_text()
        assert "user:" not in content
