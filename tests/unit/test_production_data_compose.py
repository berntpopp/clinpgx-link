"""The unpublished production-data overlay renders only with explicit pins."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_FILES = [
    "-f",
    "docker/docker-compose.npm.yml",
    "-f",
    "docker/docker-compose.npm.data-draft.yml",
]


def _render(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker")
    assert docker is not None
    return subprocess.run(  # noqa: S603 - fixed read-only Compose config command
        [docker, "compose", *_FILES, "config", "--format", "json"],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def _pins() -> dict[str, str]:
    return {
        "CLINPGX_LINK_IMAGE": "ghcr.io/berntpopp/clinpgx-link@sha256:" + "a" * 64,
        "CLINPGX_DATA_VOLUME": "clinpgx-candidate-synthetic",
        "CLINPGX_RELEASE_INPUT_DIR": str(_ROOT / "synthetic-clinpgx-release"),
        "CLINPGX_RELEASE_MANIFEST_SHA256": "b" * 64,
        "CLINPGX_EXPECTED_SNAPSHOT": "sha256:" + "c" * 64,
        "CLINPGX_EXPECTED_RELEASE_TAG": "data-clinpgx-synthetic",
        "CLINPGX_EXPECTED_RUNTIME_DIGEST": "sha256:" + "d" * 64,
    }


def test_draft_overlay_fails_to_render_without_all_public_pins() -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("CLINPGX_")}
    env.update(_pins())
    for missing in _pins():
        candidate = dict(env)
        candidate.pop(missing)
        result = _render(candidate)
        assert result.returncode != 0, missing


def test_draft_overlay_uses_one_image_and_isolated_data_roles() -> None:
    env = {**os.environ, **_pins()}
    result = _render(env)
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    app = rendered["services"]["clinpgx_link"]
    initializer = rendered["services"]["clinpgx_data_init"]
    assert app["image"] == initializer["image"] == env["CLINPGX_LINK_IMAGE"]
    assert app["user"] == initializer["user"] == "999:999"
    assert app["read_only"] is initializer["read_only"] is True
    assert app["cap_drop"] == initializer["cap_drop"] == ["ALL"]
    assert app["security_opt"] == initializer["security_opt"] == ["no-new-privileges:true"]
    assert app["environment"]["CLINPGX_RUNTIME_MODE"] == "production"
    assert app["environment"]["CLINPGX_EXPECTED_RELEASE_TAG"] == env["CLINPGX_EXPECTED_RELEASE_TAG"]
    app_data = next(m for m in app["volumes"] if m["target"] == "/data")
    init_data = next(m for m in initializer["volumes"] if m["target"] == "/data")
    assert app_data["source"] == init_data["source"] == "clinpgx_data"
    assert app_data["read_only"] is True
    assert init_data.get("read_only", False) is False
    assert rendered["volumes"]["clinpgx_data"] == {
        "name": env["CLINPGX_DATA_VOLUME"],
        "external": True,
    }
    assert initializer.get("ports") is None
    assert initializer["network_mode"] == "none"
    assert initializer["restart"] == "no"
    assert app["depends_on"]["clinpgx_data_init"]["condition"] == "service_completed_successfully"


def test_data_overlay_is_not_a_published_deployment_contract() -> None:
    published = json.loads((_ROOT / "container-release.json").read_text())
    assert (
        "docker/docker-compose.npm.data-draft.yml"
        not in published["service"]["deployed_compose_files"]
    )
    assert published["data"]["mode"] == "none"
