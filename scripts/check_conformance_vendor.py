#!/usr/bin/env python3
"""Verify immutable fleet probes and optionally their exact router checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

PIN_PATH = Path("vendor/genefoundry/CONFORMANCE_SHA256")
FILES = (
    ("docs/conformance/__init__.py", "tests/conformance/__init__.py"),
    ("docs/conformance/conformance.py", "tests/conformance/conformance.py"),
    ("docs/conformance/behaviour.py", "tests/conformance/behaviour.py"),
    ("docs/conformance/test_transport_v1.py", "tests/conformance/test_transport_v1.py"),
    ("docs/conformance/test_behaviour_v1.py", "tests/conformance/test_behaviour_v1.py"),
)
MAX_PIN_BYTES = 32 * 1024
MAX_FILE_BYTES = 1024 * 1024


def _read_bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as handle:
        raw = handle.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Vendored input exceeds its fixed size bound")
    return raw


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _load_pin(project_root: Path) -> dict[str, Any]:
    raw = _read_bounded(project_root / PIN_PATH, MAX_PIN_BYTES)
    try:
        pin = json.loads(raw)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid conformance pin JSON") from exc
    if not isinstance(pin, dict) or _canonical(pin) != raw:
        raise ValueError("Conformance pin is not strict canonical JSON")
    return pin


def verify(project_root: Path, router: Path | None) -> None:
    pin = _load_pin(project_root)
    if set(pin) != {"files", "router_commit"}:
        raise ValueError("Invalid conformance pin fields")
    commit = pin["router_commit"]
    entries = pin["files"]
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("Invalid immutable router revision")
    if not isinstance(entries, list) or len(entries) != len(FILES):
        raise ValueError("Invalid conformance file inventory")

    manifest_pairs: list[tuple[str, str]] = []
    validated: list[tuple[str, str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "destination_path",
            "sha256",
            "source_path",
        }:
            raise ValueError("Invalid conformance file entry")
        source = entry["source_path"]
        destination = entry["destination_path"]
        digest = entry["sha256"]
        if (
            not isinstance(source, str)
            or not isinstance(destination, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ValueError("Invalid conformance file pin")
        manifest_pairs.append((source, destination))
        validated.append((source, destination, digest))

    if tuple(manifest_pairs) != FILES:
        raise ValueError("Unknown, unsafe, reordered, or duplicate conformance path")

    captured: dict[str, bytes] = {}
    for _source, destination, digest in validated:
        raw = _read_bounded(project_root / destination, MAX_FILE_BYTES)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Vendored conformance digest mismatch")
        captured[destination] = raw

    if router is not None:
        git = shutil.which("git")
        if git is None:
            raise ValueError("Git unavailable for required parity check")
        actual_commit = subprocess.run(  # noqa: S603 — fixed read-only argv, no shell
            [git, "-C", str(router), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        if actual_commit != commit:
            raise ValueError("Router checkout revision mismatch")
        for source, destination in FILES:
            if _read_bounded(router / source, MAX_FILE_BYTES) != captured[destination]:
                raise ValueError("Router and vendored conformance bytes differ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--router-dir", type=Path)
    args = parser.parse_args()
    try:
        verify(args.project_root, args.router_dir)
    except (OSError, ValueError, subprocess.SubprocessError):
        sys.stderr.write("vendor-check: conformance verification failed\n")
        return 1
    sys.stdout.write(
        "vendor-check: conformance digests verified"
        + ("; pinned router bytes verified\n" if args.router_dir is not None else "\n")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
