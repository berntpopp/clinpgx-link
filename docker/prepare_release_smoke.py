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

RELEASE_TAG = "data-clinpgx-core-cfde21bfec473b35"
BASE_URL = f"https://github.com/berntpopp/clinpgx-link/releases/download/{RELEASE_TAG}"
EXPECTED_SNAPSHOT = "sha256:cbc4bba16380e8badfb05eb402bfbd47e631aba74a83f83dc239d75b75113e3b"
EXPECTED_RUNTIME_DIGEST = "sha256:ef590d6eba25423eb562405edfa7ac41862172bc689f496c2ff5346ddd3958ca"
MANIFEST_SHA256 = "80e22f9ace76ff136129c8eb5cc67fb1454c291ee2365d7e797e22d2da2845e7"
ARTIFACT_SHA256 = "481f30612a1711683d52faea535a29327a88374f12b907d4c942cbefd2c8246b"
ARTIFACT_SIZE = 198_532_972
MAX_ARTIFACT_SIZE = 268_435_456
SOURCE_MANIFEST_SHA256 = "94d032c42d43bbc742f4278a5a524b3026515e749d9d169d45387b9d7721ddf1"
EXPANDED_TREE_SHA256 = "9a15a4fb30d54301472576b11aca9eb874dfdb2cf309c5c049b90c321533a186"
EXPANDED_SIZE = 1_045_391_338
RECORD_COUNT = 453_102
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
        or not (compatibility.get("minimum") == compatibility.get("maximum") == "0.1.3")
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
        temporary.chmod(0o755)
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
