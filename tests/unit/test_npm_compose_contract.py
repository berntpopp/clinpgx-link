import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("runtime_mode_source", ["ambient", "env_file"])
def test_npm_deployment_forces_production_runtime_mode(
    tmp_path: Path, runtime_mode_source: str
) -> None:
    project_root = Path(__file__).parents[2]
    compose_file = project_root / "docker" / "docker-compose.npm.yml"
    image = "ghcr.io/berntpopp/clinpgx-link@sha256:" + "0" * 64
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(project_root),
        "-f",
        str(compose_file),
    ]
    environment = os.environ.copy()
    environment["CLINPGX_LINK_IMAGE"] = image

    if runtime_mode_source == "ambient":
        environment["CLINPGX_RUNTIME_MODE"] = "development"
    else:
        environment.pop("CLINPGX_RUNTIME_MODE", None)
        env_file = tmp_path / ".env"
        env_file.write_text(
            f"CLINPGX_RUNTIME_MODE=development\nCLINPGX_LINK_IMAGE={image}\n",
            encoding="utf-8",
        )
        command.extend(["--env-file", str(env_file)])

    result = subprocess.run(  # noqa: S603 - the command is a fixed local Compose config check.
        [*command, "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    rendered = json.loads(result.stdout)

    assert (
        rendered["services"]["clinpgx_link"]["environment"]["CLINPGX_RUNTIME_MODE"] == "production"
    )
