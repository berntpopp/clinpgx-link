"""Verify the immutable fleet schema pin and optionally its exact router checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCHEMA = "data-release-manifest.schema.json"
SOURCE_PATH = "genefoundry_router/data/" + SCHEMA


def verify(vendor: Path, router: Path | None) -> None:
    pin = json.loads((vendor / "CONTRACT_SHA256").read_text())
    if not isinstance(pin, dict) or set(pin) != {"router_commit", "source_path", "sha256"}:
        raise ValueError("Invalid contract pin fields")
    if (
        not isinstance(pin["router_commit"], str)
        or re.fullmatch(r"[0-9a-f]{40}", pin["router_commit"]) is None
        or not isinstance(pin["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]) is None
        or pin["source_path"] != SOURCE_PATH
    ):
        raise ValueError("Invalid immutable contract pin")
    raw = (vendor / SCHEMA).read_bytes()
    if hashlib.sha256(raw).hexdigest() != pin["sha256"]:
        raise ValueError("Vendored schema digest mismatch")
    if router is not None:
        git = shutil.which("git")
        if git is None:
            raise ValueError("Git unavailable for required parity check")
        commit = subprocess.run(  # noqa: S603 — fixed read-only argv, no shell
            [git, "-C", str(router), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        if commit != pin["router_commit"]:
            raise ValueError("Router checkout revision mismatch")
        if (router / SOURCE_PATH).read_bytes() != raw:
            raise ValueError("Router and vendored schema bytes differ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vendor-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "vendor/genefoundry",
    )
    parser.add_argument("--router-dir", type=Path)
    args = parser.parse_args()
    try:
        verify(args.vendor_dir, args.router_dir)
    except (OSError, ValueError, subprocess.SubprocessError):
        sys.stderr.write("vendor-check: contract verification failed\n")
        return 1
    sys.stdout.write(
        "vendor-check: digest verified"
        + ("; pinned router bytes verified\n" if args.router_dir is not None else "\n")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
