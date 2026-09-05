"""Exercise the immutable fleet-conformance vendor gate as a subprocess."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts/check_conformance_vendor.py"
PAIRS = (
    ("docs/conformance/__init__.py", "tests/conformance/__init__.py"),
    ("docs/conformance/conformance.py", "tests/conformance/conformance.py"),
    ("docs/conformance/behaviour.py", "tests/conformance/behaviour.py"),
    ("docs/conformance/test_transport_v1.py", "tests/conformance/test_transport_v1.py"),
    ("docs/conformance/test_behaviour_v1.py", "tests/conformance/test_behaviour_v1.py"),
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def _write_pin(vendor: Path, pin: dict[str, Any]) -> None:
    (vendor / "vendor/genefoundry/CONFORMANCE_SHA256").write_text(_canonical(pin))


def _run(vendor: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — exercise a fixed local script without a shell
        [sys.executable, str(SCRIPT), "--project-root", str(vendor), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _vendor(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "project"
    entries = []
    for index, (source, destination) in enumerate(PAIRS):
        raw = f"# captured probe {index}\n".encode()
        target = root / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        entries.append(
            {
                "destination_path": destination,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "source_path": source,
            }
        )
    pin = {"files": entries, "router_commit": "a" * 40}
    (root / "vendor/genefoundry").mkdir(parents=True)
    _write_pin(root, pin)
    return root, pin


@pytest.mark.parametrize("mutation", ["modified", "missing"])
def test_conformance_vendor_gate_accepts_capture_and_rejects_changed_bytes(
    tmp_path: Path, mutation: str
) -> None:
    root, _ = _vendor(tmp_path)
    assert _run(root).returncode == 0
    target = root / PAIRS[2][1]
    if mutation == "modified":
        target.write_bytes(b"changed\n")
    else:
        target.unlink()
    assert _run(root).returncode == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda pin: pin.update(extra="field"),
        lambda pin: pin.update(router_commit="main"),
        lambda pin: pin.update(files="not-a-list"),
    ],
)
def test_conformance_vendor_gate_rejects_invalid_pin_shape(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> None:
    root, pin = _vendor(tmp_path)
    mutate(pin)
    _write_pin(root, pin)
    assert _run(root).returncode == 1


def test_conformance_vendor_gate_requires_canonical_json_bytes(tmp_path: Path) -> None:
    root, pin = _vendor(tmp_path)
    (root / "vendor/genefoundry/CONFORMANCE_SHA256").write_text(json.dumps(pin, indent=2))
    assert _run(root).returncode == 1


def test_conformance_vendor_gate_rejects_oversized_capture(tmp_path: Path) -> None:
    root, pin = _vendor(tmp_path)
    raw = b"x" * (1024 * 1024 + 1)
    (root / PAIRS[1][1]).write_bytes(raw)
    files = pin["files"]
    assert isinstance(files, list)
    files[1]["sha256"] = hashlib.sha256(raw).hexdigest()
    _write_pin(root, pin)
    assert _run(root).returncode == 1


@pytest.mark.parametrize("mutation", ["unsafe", "unknown", "duplicate"])
def test_conformance_vendor_gate_rejects_noncanonical_inventory(
    tmp_path: Path, mutation: str
) -> None:
    root, pin = _vendor(tmp_path)
    files = pin["files"]
    assert isinstance(files, list)
    if mutation == "unsafe":
        files[0]["destination_path"] = "../escape.py"
    elif mutation == "unknown":
        files[0]["source_path"] = "docs/conformance/unknown.py"
    else:
        files.append(dict(files[0]))
    _write_pin(root, pin)
    assert _run(root).returncode == 1


def test_requested_router_comparison_cannot_skip_missing_checkout(tmp_path: Path) -> None:
    root, _ = _vendor(tmp_path)
    result = _run(root, "--router-dir", str(tmp_path / "missing-router"))
    assert result.returncode == 1


def test_router_gate_checks_pinned_head_and_every_source_byte(tmp_path: Path) -> None:
    root, pin = _vendor(tmp_path)
    router = tmp_path / "router"
    router.mkdir()
    git = shutil.which("git")
    assert git is not None

    def run_git(*args: str) -> str:
        return subprocess.run(  # noqa: S603 — fixed temporary repository and argv
            [
                git,
                "-C",
                str(router),
                "-c",
                "user.name=Conformance Test",
                "-c",
                "user.email=conformance@example.test",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    for source, destination in PAIRS:
        target = router / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / destination).read_bytes())
    run_git("init")
    run_git("add", "docs/conformance")
    run_git("commit", "-m", "fixture")
    pin["router_commit"] = run_git("rev-parse", "HEAD")
    _write_pin(root, pin)

    assert _run(root, "--router-dir", str(router)).returncode == 0
    source = router / PAIRS[1][0]
    original = source.read_bytes()
    source.write_bytes(b"changed source\n")
    assert _run(root, "--router-dir", str(router)).returncode == 1
    source.write_bytes(original)
    run_git("commit", "--allow-empty", "-m", "different revision")
    assert _run(root, "--router-dir", str(router)).returncode == 1
