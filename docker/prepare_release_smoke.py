"""Fetch and authenticate the exact public ClinPGx bundle for release smoke tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

RELEASE_TAG = "data-clinpgx-core-d91c0c4082f14706"
BASE_URL = f"https://github.com/berntpopp/clinpgx-link/releases/download/{RELEASE_TAG}"
EXPECTED_SNAPSHOT = "sha256:1567392b98f425e8442fa1cef244f544107b76c166c9e6a29538b1bf4e7d56ac"
EXPECTED_RUNTIME_DIGEST = "sha256:f04d50edcd7c8d0310c5b87c3a177fc1a44da249ae677707d5c77e7275ff8fc4"
MANIFEST_SHA256 = "2db0287d6edf47368fd5833670026cd1264e80f5d23ce82c5dfc262406e5a351"
ARTIFACT_SHA256 = "6b2fdb4a6b4a975de8c7cd58c17a99c5e96c8bc42c088c2393c5b037a6431446"
ARTIFACT_SIZE = 244_176_059
MAX_ARTIFACT_SIZE = 268_435_456
SOURCE_MANIFEST_SHA256 = "88d6d19c4acd88689139b35a09959a154ccca0e4eabf01bc94aabd2260980e4a"
EXPANDED_TREE_SHA256 = "5f025c0c17f2854e74370c3bffb5eacefe95c96e645a70f33e511de113c4a607"
EXPANDED_SIZE = 1_315_932_270
RECORD_COUNT = 624_516
_ALLOWED_REDIRECT_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}


class SmokePreparationError(ValueError):
    """A smoke fixture could not be safely prepared."""


def _download(name: str, target: Path, expected_sha256: str, max_bytes: int) -> None:
    request = Request(f"{BASE_URL}/{name}", headers={"Accept": "application/octet-stream"})  # noqa: S310 - fixed HTTPS host and immutable tag
    try:
        with urlopen(request, timeout=30) as response, target.open("xb") as output:  # noqa: S310 - fixed HTTPS release URL; final redirect host is allowlisted
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname not in _ALLOWED_REDIRECT_HOSTS:
                raise SmokePreparationError("release asset redirected outside the HTTPS allowlist")
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > max_bytes:
                raise SmokePreparationError("release asset exceeds its byte limit")
            digest = hashlib.sha256()
            size = 0
            while chunk := response.read(128 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise SmokePreparationError("release asset exceeds its byte limit")
                digest.update(chunk)
                output.write(chunk)
            if digest.hexdigest() != expected_sha256:
                raise SmokePreparationError("release asset digest does not match the source pin")
    except (OSError, ValueError) as exc:
        target.unlink(missing_ok=True)
        if isinstance(exc, SmokePreparationError):
            raise
        raise SmokePreparationError("release asset could not be downloaded safely") from exc


def _validate_manifest(path: Path) -> None:
    raw = path.read_bytes()
    if len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise SmokePreparationError("release manifest bytes do not match the source pin")
    try:
        manifest = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SmokePreparationError("release manifest is not valid JSON") from exc
    artifact = manifest.get("artifact", {})
    compatibility = manifest.get("application_compatibility", {})
    schema = manifest.get("schema", {})
    source = manifest.get("dataset", {}).get("source", {})
    if (
        manifest.get("schema_version") != 1
        or manifest.get("dataset", {}).get("release") != RELEASE_TAG
        or source.get("sha256") != SOURCE_MANIFEST_SHA256
        or artifact.get("filename") != "clinpgx-core.tar.zst"
        or artifact.get("sha256") != ARTIFACT_SHA256
        or artifact.get("compressed_size") != ARTIFACT_SIZE
        or artifact.get("max_compressed_size") != MAX_ARTIFACT_SIZE
        or artifact.get("expanded_tree_sha256") != EXPANDED_TREE_SHA256
        or artifact.get("expanded_size") != EXPANDED_SIZE
        or artifact.get("max_expanded_size") != 2_147_483_648
        or artifact.get("member_count") != 4
        or artifact.get("max_members") != 4
        or schema.get("actual") != "1.0.0"
        or schema.get("minimum") != "1.0.0"
        or schema.get("maximum") != "1.0.0"
        or not (compatibility.get("minimum") == compatibility.get("maximum") == "0.1.9")
        or manifest.get("record_counts", {}).get("record") != RECORD_COUNT
        or manifest.get("license", {}).get("redistribution_allowed") is not True
    ):
        raise SmokePreparationError("release manifest identity or compatibility is unexpected")


def prepare(fixture_dir: Path, env_file: Path) -> Path:
    """Download the two fixed release assets and emit bounded smoke-only variables."""
    if not fixture_dir.is_dir() or fixture_dir.is_symlink():
        raise SmokePreparationError("smoke fixture directory is not a real directory")
    if env_file.is_symlink() or not env_file.is_file() or env_file.stat().st_size:
        raise SmokePreparationError("smoke environment output must be an empty regular file")
    final_dir = fixture_dir / "clinpgx-release"
    if final_dir.exists() or final_dir.is_symlink():
        raise SmokePreparationError("ClinPGx release fixture already exists")
    temporary = Path(tempfile.mkdtemp(prefix="clinpgx-release-", dir=fixture_dir))
    try:
        _download(
            "data-release-manifest.json",
            temporary / "data-release-manifest.json",
            MANIFEST_SHA256,
            1024 * 1024,
        )
        _validate_manifest(temporary / "data-release-manifest.json")
        _download(
            "clinpgx-core.tar.zst",
            temporary / "clinpgx-core.tar.zst",
            ARTIFACT_SHA256,
            MAX_ARTIFACT_SIZE,
        )
        if (temporary / "clinpgx-core.tar.zst").stat().st_size != ARTIFACT_SIZE:
            raise SmokePreparationError("release artifact size does not match the source pin")
        for path in temporary.iterdir():
            path.chmod(0o444)
        temporary.chmod(0o555)
        temporary.rename(final_dir)
        env_file.write_text(
            f"CLINPGX_EXPECTED_SNAPSHOT={EXPECTED_SNAPSHOT}\n"
            f"CLINPGX_EXPECTED_RELEASE_TAG={RELEASE_TAG}\n"
            f"CLINPGX_EXPECTED_RUNTIME_DIGEST={EXPECTED_RUNTIME_DIGEST}\n"
            f"CLINPGX_RELEASE_MANIFEST_SHA256={MANIFEST_SHA256}\n"
            f"CLINPGX_RELEASE_ARTIFACT_SHA256={ARTIFACT_SHA256}\n"
            f"CLINPGX_RELEASE_INPUT_DIR={final_dir}\n",
            encoding="ascii",
        )
        return final_dir
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> None:
    fixture_dir = Path(os.environ["GF_SMOKE_FIXTURE_DIR"])
    env_file = Path(os.environ["GF_SMOKE_ENV_FILE"])
    prepare(fixture_dir, env_file)


if __name__ == "__main__":
    main()
