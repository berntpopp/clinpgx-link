"""Run the actual vendor gate against changed bytes and missing upstream state."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/check_vendor_contract.py"


def _run(directory, *args):
    return subprocess.run(  # noqa: S603 — exercise fixed script without a shell
        [sys.executable, str(SCRIPT), "--vendor-dir", str(directory), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _vendor(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    raw = b'{"type":"object"}\n'
    (vendor / "data-release-manifest.schema.json").write_bytes(raw)
    (vendor / "CONTRACT_SHA256").write_text(
        json.dumps(
            {
                "router_commit": "a" * 40,
                "source_path": "genefoundry_router/data/data-release-manifest.schema.json",
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    )
    return vendor


def test_vendor_gate_rejects_changed_schema_bytes(tmp_path):
    vendor = _vendor(tmp_path)
    assert _run(vendor).returncode == 0
    (vendor / "data-release-manifest.schema.json").write_bytes(b'{"type":"string"}\n')
    assert _run(vendor).returncode != 0


def test_requested_upstream_comparison_cannot_silently_skip_missing_router(tmp_path):
    vendor = _vendor(tmp_path)
    result = _run(vendor, "--router-dir", str(tmp_path / "absent-router"))
    assert result.returncode != 0


def test_vendor_gate_rejects_mutable_router_pin(tmp_path):
    vendor = _vendor(tmp_path)
    pin = json.loads((vendor / "CONTRACT_SHA256").read_text())
    pin["router_commit"] = "main"
    (vendor / "CONTRACT_SHA256").write_text(json.dumps(pin))
    assert _run(vendor).returncode != 0


def test_upstream_gate_checks_both_commit_and_file_bytes(tmp_path):
    vendor = _vendor(tmp_path)
    router = tmp_path / "router"
    router.mkdir()
    git = shutil.which("git")
    assert git is not None

    def run_git(*args):
        return subprocess.run(  # noqa: S603 — fixed test repository, no shell
            [
                git,
                "-C",
                str(router),
                "-c",
                "user.name=Contract Test",
                "-c",
                "user.email=contract@example.test",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    pin = json.loads((vendor / "CONTRACT_SHA256").read_text())
    source = router / pin["source_path"]
    source.parent.mkdir(parents=True)
    source.write_bytes((vendor / "data-release-manifest.schema.json").read_bytes())
    run_git("init")
    run_git("add", pin["source_path"])
    run_git("commit", "-m", "fixture")
    pin["router_commit"] = run_git("rev-parse", "HEAD")
    (vendor / "CONTRACT_SHA256").write_text(json.dumps(pin))
    assert _run(vendor, "--router-dir", str(router)).returncode == 0
    original = source.read_bytes()
    source.write_bytes(b"changed upstream bytes")
    assert _run(vendor, "--router-dir", str(router)).returncode == 1
    source.write_bytes(original)
    run_git("commit", "--allow-empty", "-m", "different revision")
    assert _run(vendor, "--router-dir", str(router)).returncode == 1
