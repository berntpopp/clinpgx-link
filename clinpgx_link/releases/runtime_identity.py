"""Build and verify the canonical fleet runtime data identity v1.

Adapted from GeneFoundry ``clingen-link`` runtime identity code, MIT,
Copyright 2026 Bernt Popp. Runtime hashes use compact, sorted UTF-8 JSON with
no trailing newline.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from clinpgx_link.exceptions import DataValidationError

MANIFEST_NAME = "data-identity-manifest.json"
MAX_IDENTITY_MANIFEST_BYTES = 64 * 1024
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024

_INPUT_NAMES = (
    "clinpgx.sqlite",
    "licenses.json",
    "materialization.json",
    "schema.json",
    "source-manifest.json",
)
_EXPECTED_NAMES = frozenset((*_INPUT_NAMES, MANIFEST_NAME))
_MANIFEST_FIELDS = frozenset({"schema_version", "release_tag", "inputs"})
_ENTRY_FIELDS = frozenset({"path", "size_bytes", "sha256"})
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MUTABLE_TAGS = frozenset({"latest", "main", "master", "head", "stable", "current"})
_EXPECTED_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_FILE_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_UNSAFE_FILE_MODE = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID
_READ_CHUNK_BYTES = 1024 * 1024


def _error(message: str, subtype: str) -> DataValidationError:
    return DataValidationError(message, subtype=subtype)


def canonical_runtime_json(value: object) -> bytes:
    """Return the exact newline-free canonical bytes used by fleet runtime-v1."""
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _error(
            "Runtime identity canonical JSON is invalid", "runtime_identity_invalid"
        ) from exc


def _validate_tag(value: object, *, expected: bool = False) -> str:
    if type(value) is not str or _TAG.fullmatch(value) is None or value.lower() in _MUTABLE_TAGS:
        label = (
            "expected runtime identity is invalid" if expected else "Identity manifest is invalid"
        )
        raise _error(label, "runtime_identity_invalid")
    return value


def _validate_limits(max_file_bytes: object, max_total_bytes: object) -> tuple[int, int]:
    if (
        type(max_file_bytes) is not int
        or max_file_bytes <= 0
        or type(max_total_bytes) is not int
        or max_total_bytes <= 0
    ):
        raise _error("Runtime identity byte limits are invalid", "resource_limit")
    return max_file_bytes, max_total_bytes


def _stat_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _validate_file_stat(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise _error("Runtime identity file is not safe", "runtime_identity_unsafe")
    if info.st_mode & _UNSAFE_FILE_MODE:
        raise _error("Runtime identity file mode is unsafe", "runtime_identity_unsafe")


def _open_root(root: Path) -> int:
    try:
        lexical = os.lstat(root)
        if not stat.S_ISDIR(lexical.st_mode) or stat.S_ISLNK(lexical.st_mode):
            raise _error("Runtime identity root is not safe", "runtime_identity_unsafe")
        if lexical.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise _error("Runtime identity root mode is unsafe", "runtime_identity_unsafe")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(root, flags)
        opened = os.fstat(descriptor)
        if _stat_identity(lexical) != _stat_identity(opened):
            os.close(descriptor)
            raise _error(
                "Runtime identity root changed during verification", "runtime_identity_changed"
            )
        return descriptor
    except DataValidationError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise _error("Runtime identity root is not safe", "runtime_identity_unsafe") from exc


def _inventory(root_fd: int, *, manifest_required: bool) -> dict[str, tuple[int, ...]]:
    observed: dict[str, tuple[int, ...]] = {}
    try:
        with os.scandir(root_fd) as entries:
            for entry in entries:
                if entry.name not in _EXPECTED_NAMES:
                    raise _error("Runtime identity root inventory is invalid", "runtime_inventory")
                info = entry.stat(follow_symlinks=False)
                _validate_file_stat(info)
                observed[entry.name] = _stat_identity(info)
    except DataValidationError:
        raise
    except OSError as exc:
        raise _error("Runtime identity root inventory is unavailable", "runtime_inventory") from exc
    required = set(_INPUT_NAMES)
    if manifest_required:
        required.add(MANIFEST_NAME)
    if not required.issubset(observed) or set(observed) - _EXPECTED_NAMES:
        raise _error("Runtime identity root inventory is invalid", "runtime_inventory")
    return observed


def _read_regular_file(
    root_fd: int,
    name: str,
    *,
    max_bytes: int,
    digest: bool,
) -> tuple[bytes | None, int, str | None]:
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, dir_fd=root_fd)
        before = os.fstat(descriptor)
        _validate_file_stat(before)
        if before.st_size > max_bytes:
            raise _error("Runtime identity input exceeds its byte limit", "resource_limit")
        hasher = hashlib.sha256() if digest else None
        body = bytearray() if not digest else None
        size = 0
        while True:
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, max_bytes + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise _error("Runtime identity input exceeds its byte limit", "resource_limit")
            if hasher is not None:
                hasher.update(chunk)
            else:
                assert body is not None
                body.extend(chunk)
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after) or size != after.st_size:
            raise _error(
                "Runtime identity input changed during verification", "runtime_identity_changed"
            )
        return (
            bytes(body) if body is not None else None,
            size,
            hasher.hexdigest() if hasher is not None else None,
        )
    except DataValidationError:
        raise
    except OSError as exc:
        raise _error("Runtime identity file is not safe", "runtime_identity_unsafe") from exc
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)


def _read_manifest_bytes(root_fd: int) -> bytes:
    body, _, _ = _read_regular_file(
        root_fd, MANIFEST_NAME, max_bytes=MAX_IDENTITY_MANIFEST_BYTES, digest=False
    )
    assert body is not None
    return body


def _duplicate_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise ValueError("nonfinite")


def _load_manifest(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_duplicate_key, parse_constant=_nonfinite
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _error("Identity manifest is invalid", "runtime_manifest_invalid") from exc
    if type(value) is not dict or set(value) != _MANIFEST_FIELDS:
        raise _error("Identity manifest is invalid", "runtime_manifest_invalid")
    manifest = cast(dict[str, object], value)
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise _error("Identity manifest is invalid", "runtime_manifest_invalid")
    release_tag = _validate_tag(manifest["release_tag"])
    inputs = manifest["inputs"]
    if type(inputs) is not list or len(inputs) != len(_INPUT_NAMES):
        raise _error("Identity manifest inventory is invalid", "runtime_manifest_invalid")
    validated: list[dict[str, object]] = []
    for index, item in enumerate(inputs):
        if type(item) is not dict or set(item) != _ENTRY_FIELDS:
            raise _error("Identity manifest entry is invalid", "runtime_manifest_invalid")
        entry = cast(dict[str, object], item)
        if (
            entry["path"] != _INPUT_NAMES[index]
            or type(entry["size_bytes"]) is not int
            or entry["size_bytes"] < 0
            or type(entry["sha256"]) is not str
            or _FILE_DIGEST.fullmatch(entry["sha256"]) is None
        ):
            raise _error("Identity manifest entry is invalid", "runtime_manifest_invalid")
        validated.append(entry)
    return {"schema_version": 1, "release_tag": release_tag, "inputs": validated}


def _build_entries(
    root_fd: int, *, max_file_bytes: int, max_total_bytes: int
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    total = 0
    for name in _INPUT_NAMES:
        remaining = max_total_bytes - total
        if remaining <= 0:
            raise _error("Runtime identity aggregate exceeds its byte limit", "resource_limit")
        _, size, digest = _read_regular_file(
            root_fd, name, max_bytes=min(max_file_bytes, remaining), digest=True
        )
        total += size
        assert digest is not None
        entries.append({"path": name, "size_bytes": size, "sha256": digest})
    return entries


def _with_root(root: Path, operation: Callable[[int], dict[str, object]]) -> dict[str, object]:
    root_fd = _open_root(root)
    try:
        return operation(root_fd)
    finally:
        with suppress(OSError):
            os.close(root_fd)


def build_runtime_identity(
    root: Path,
    release_tag: str,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> dict[str, object]:
    """Build a read-only runtime-v1 manifest for the exact installed-file inventory.

    The sidecar may be absent. If present, it is validated as an immutable safe
    regular file, but this operation never writes or trusts it.
    """
    tag = _validate_tag(release_tag)
    file_limit, total_limit = _validate_limits(max_file_bytes, max_total_bytes)

    def build(root_fd: int) -> dict[str, object]:
        before = _inventory(root_fd, manifest_required=False)
        result = {
            "schema_version": 1,
            "release_tag": tag,
            "inputs": _build_entries(
                root_fd, max_file_bytes=file_limit, max_total_bytes=total_limit
            ),
        }
        if _inventory(root_fd, manifest_required=False) != before:
            raise _error(
                "Runtime identity root changed during verification", "runtime_identity_changed"
            )
        return result

    return _with_root(root, build)


def verify_runtime_identity(
    root: Path,
    *,
    expected_digest: str,
    expected_release_tag: str,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> dict[str, str]:
    """Rehash and verify an independently pinned immutable runtime identity.

    Two descriptor-based content passes bracket inventory validation and manifest
    rereading. This detects observed concurrent mutation on a best-effort basis;
    portable filesystems do not provide an atomic directory/content snapshot, so
    mutation after the final checks is outside the runtime-v1 guarantee.
    """
    if type(expected_digest) is not str or _EXPECTED_DIGEST.fullmatch(expected_digest) is None:
        raise _error("expected runtime identity is invalid", "runtime_identity_invalid")
    expected_tag = _validate_tag(expected_release_tag, expected=True)
    file_limit, total_limit = _validate_limits(max_file_bytes, max_total_bytes)

    def verify(root_fd: int) -> dict[str, object]:
        inventory = _inventory(root_fd, manifest_required=True)
        raw = _read_manifest_bytes(root_fd)
        manifest = _load_manifest(raw)
        tag = cast(str, manifest["release_tag"])
        if not hmac.compare_digest(tag, expected_tag):
            raise _error("Runtime identity release tag mismatch", "runtime_identity_mismatch")
        entries = cast(list[dict[str, object]], manifest["inputs"])
        for _ in range(2):
            actual = _build_entries(root_fd, max_file_bytes=file_limit, max_total_bytes=total_limit)
            if actual != entries:
                raise _error("Runtime identity file mismatch", "runtime_identity_mismatch")
            if _inventory(root_fd, manifest_required=True) != inventory:
                raise _error(
                    "Runtime identity root changed during verification", "runtime_identity_changed"
                )
        if _read_manifest_bytes(root_fd) != raw:
            raise _error(
                "Runtime identity manifest changed during verification", "runtime_identity_changed"
            )
        digest = f"sha256:{hashlib.sha256(canonical_runtime_json(manifest)).hexdigest()}"
        if not hmac.compare_digest(digest, expected_digest):
            raise _error("Runtime identity digest mismatch", "runtime_identity_mismatch")
        return {"release_tag": tag, "digest": digest}

    result = _with_root(root, verify)
    return cast(dict[str, str], result)


__all__ = [
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "MANIFEST_NAME",
    "MAX_IDENTITY_MANIFEST_BYTES",
    "build_runtime_identity",
    "canonical_runtime_json",
    "verify_runtime_identity",
]
