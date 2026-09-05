"""Authenticated immutable release staging, installation, and offline rollback."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import secrets
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases import bundle_io
from clinpgx_link.releases.bundle import BundleLimits, verify_and_extract_bundle
from clinpgx_link.releases.manifest import (
    CompatibilityRange,
    DataReleaseManifest,
    validate_manifest,
)
from clinpgx_link.releases.materialization import (
    Materialization,
    parse_materialization,
    read_immutable,
    validate_semantics,
)
from clinpgx_link.releases.materialization import (
    canonical_bytes as materialization_bytes,
)
from clinpgx_link.releases.runtime_identity import (
    build_runtime_identity,
    canonical_runtime_json,
    verify_runtime_identity,
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GENERATION_NAMES = frozenset(
    {
        "clinpgx.sqlite",
        "schema.json",
        "source-manifest.json",
        "licenses.json",
        "materialization.json",
        "data-identity-manifest.json",
    }
)


@dataclass(frozen=True)
class ReleaseInput:
    manifest_bytes: bytes
    expected_manifest_sha256: str
    artifact_path: Path | None


@dataclass(frozen=True)
class MaterializationReceipt:
    path: Path
    artifact_sha256: str
    release_tag: str
    source_set_identity: str
    snapshot_id: str
    expanded_tree_sha256: str
    runtime_identity_sha256: str


def _private_root(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_absolute():
        raise DataValidationError("Data root must be an absolute private path")
    if not path.exists():
        path.mkdir(mode=0o700)
    info = path.lstat()
    if (
        path.resolve(strict=True) != path
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o022
    ):
        raise DataValidationError("Data root must be a private same-owner real directory")


def _versions(path: Path) -> Path:
    versions = path / "versions"
    if not versions.exists():
        versions.mkdir(mode=0o700)
    descriptor = bundle_io.open_private_directory(versions)
    os.close(descriptor)
    return versions


def _admit_layout(root: Path, versions: Path) -> None:
    if set(os.listdir(root)) - {".materialize.lock", "versions", "current"}:
        raise DataValidationError("Data root inventory is invalid")
    for entry in versions.iterdir():
        if _DIGEST.fullmatch(entry.name) is None:
            raise DataValidationError("Versions inventory is invalid")
        descriptor = bundle_io.open_private_directory(entry)
        os.close(descriptor)


@contextmanager
def _lock(root: Path, timeout: float) -> Iterator[None]:
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
        or not float(timeout) < float("inf")
    ):
        raise DataValidationError("Lock timeout must be finite and positive")
    descriptor = os.open(
        root / ".materialize.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600
    )
    try:
        info = os.fstat(descriptor)
        named = os.lstat(root / ".materialize.lock")
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise DataValidationError("Materialization lock is unsafe")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise DataValidationError(
                        "Materialization lock timed out", subtype="lock_timeout"
                    ) from None
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        with suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _authenticate(
    item: ReleaseInput, application: str, supported: CompatibilityRange
) -> DataReleaseManifest:
    if (
        not isinstance(item, ReleaseInput)
        or type(item.manifest_bytes) is not bytes
        or not isinstance(supported, CompatibilityRange)
    ):
        raise DataValidationError("Release input is invalid")
    manifest = validate_manifest(item.manifest_bytes, item.expected_manifest_sha256)
    try:
        application_ok = manifest.application_compatibility.contains(application)
        schema_ok = supported.contains(manifest.schema_identity.actual)
    except (TypeError, ValueError) as exc:
        raise DataValidationError("Deployment compatibility input is invalid") from exc
    if not application_ok or not schema_ok:
        raise DataValidationError("Release is incompatible with this application")
    return manifest


def _write(root: Path, name: str, raw: bytes) -> None:
    root_fd = bundle_io.open_private_directory(root)
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=root_fd,
        )
        view = memoryview(raw)
        while view:
            count = os.write(descriptor, view)
            view = view[count:]
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
        os.fsync(root_fd)
    except OSError as exc:
        raise DataValidationError("Materialization sidecar cannot be written") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(root_fd)


def _tree_from_runtime(identity: dict[str, object]) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    inputs = cast(list[dict[str, object]], identity["inputs"])
    for item in inputs:
        if item["path"] == "materialization.json":
            continue
        path = cast(str, item["path"])
        size = cast(int, item["size_bytes"])
        sha256 = cast(str, item["sha256"])
        total += size
        digest.update(f"{path}\0{0o444:04o}\0{size}\0{sha256}\n".encode())
    return digest.hexdigest(), total


def _sidecar(item: ReleaseInput, manifest: DataReleaseManifest, semantic) -> Materialization:  # type: ignore[no-untyped-def]
    try:
        text = item.manifest_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise DataValidationError("Outer manifest is not UTF-8") from exc
    return Materialization(
        schema_version=1,
        outer_manifest_text=text,
        outer_manifest_sha256=item.expected_manifest_sha256,
        artifact_sha256=manifest.artifact.sha256,
        expanded_tree_sha256=manifest.artifact.expanded_tree_sha256,
        expanded_size=manifest.artifact.expanded_size,
        member_count=manifest.artifact.member_count,
        database_schema_version=semantic.schema.database_schema_version,
        schema_minimum=manifest.schema_identity.minimum,
        schema_maximum=manifest.schema_identity.maximum,
        release_tag=manifest.dataset.release,
        previous_known_good_digest=manifest.previous_known_good_digest,
        source_set_identity=semantic.source.source_set_identity,
        snapshot_id=semantic.snapshot_id,
    )


def _verify_generation(
    path: Path, item: ReleaseInput, manifest: DataReleaseManifest, limits: BundleLimits
) -> MaterializationReceipt:
    if set(os.listdir(path)) != _GENERATION_NAMES:
        raise DataValidationError("Retained generation inventory is invalid")
    semantic = validate_semantics(path, manifest)
    expected_sidecar = _sidecar(item, manifest, semantic)
    raw_sidecar = read_immutable(path, "materialization.json", 2 * 1024 * 1024)
    if parse_materialization(raw_sidecar) != expected_sidecar:
        raise DataValidationError("Materialization sidecar does not match trusted input")
    runtime = build_runtime_identity(
        path,
        manifest.dataset.release,
        max_file_bytes=limits.max_member_bytes,
        max_total_bytes=limits.max_expanded_bytes + 2 * 1024 * 1024,
    )
    tree, expanded = _tree_from_runtime(runtime)
    if (tree, expanded) != (
        manifest.artifact.expanded_tree_sha256,
        manifest.artifact.expanded_size,
    ):
        raise DataValidationError("Retained expanded tree does not match trusted input")
    raw_runtime = canonical_runtime_json(runtime)
    if read_immutable(path, "data-identity-manifest.json", 64 * 1024) != raw_runtime:
        raise DataValidationError("Runtime identity manifest is not canonical")
    runtime_digest = f"sha256:{hashlib.sha256(raw_runtime).hexdigest()}"
    verify_runtime_identity(
        path,
        expected_digest=runtime_digest,
        expected_release_tag=manifest.dataset.release,
        max_file_bytes=limits.max_member_bytes,
        max_total_bytes=limits.max_expanded_bytes + 2 * 1024 * 1024,
    )
    return MaterializationReceipt(
        path,
        manifest.artifact.sha256,
        manifest.dataset.release,
        semantic.source.source_set_identity,
        semantic.snapshot_id,
        tree,
        runtime_digest,
    )


def _stage_locked(
    item: ReleaseInput, manifest: DataReleaseManifest, root: Path, limits: BundleLimits
) -> MaterializationReceipt:
    versions = _versions(root)
    target = versions / manifest.artifact.sha256
    for candidate in versions.iterdir():
        if candidate.name == manifest.artifact.sha256 or _DIGEST.fullmatch(candidate.name) is None:
            continue
        sidecar = parse_materialization(
            read_immutable(candidate, "materialization.json", 2 * 1024 * 1024)
        )
        if sidecar.release_tag == manifest.dataset.release:
            raise DataValidationError(
                "Release tag is paired with different artifact bytes", subtype="release_collision"
            )
    if target.exists() and not target.is_symlink():
        return _verify_generation(target, item, manifest, limits)
    if target.is_symlink():
        raise DataValidationError("Retained generation path is unsafe")
    if item.artifact_path is None:
        raise DataValidationError("Required retained generation is absent")
    scratch = versions / f".{manifest.artifact.sha256}.{secrets.token_hex(8)}.partial"
    try:
        verify_and_extract_bundle(item.artifact_path, scratch, manifest.artifact, limits=limits)
        semantic = validate_semantics(scratch, manifest)
        sidecar = _sidecar(item, manifest, semantic)
        raw_sidecar = materialization_bytes(sidecar)
        if len(raw_sidecar) > 2 * 1024 * 1024:
            raise DataValidationError("Materialization sidecar exceeds its byte limit")
        _write(scratch, "materialization.json", raw_sidecar)
        runtime = build_runtime_identity(
            scratch,
            manifest.dataset.release,
            max_file_bytes=limits.max_member_bytes,
            max_total_bytes=limits.max_expanded_bytes + 2 * 1024 * 1024,
        )
        _write(scratch, "data-identity-manifest.json", canonical_runtime_json(runtime))
        _verify_generation(scratch, item, manifest, limits)
        parent_fd = bundle_io.open_private_directory(versions)
        try:
            bundle_io.rename_noreplace(parent_fd, scratch.name, target.name)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return _verify_generation(target, item, manifest, limits)
    finally:
        if scratch.exists():
            parent_fd = bundle_io.open_private_directory(versions)
            try:
                with suppress(DataValidationError):
                    bundle_io.remove_directory(parent_fd, scratch.name)
            finally:
                os.close(parent_fd)


def _select(root: Path, receipt: MaterializationReceipt) -> None:
    root_fd = bundle_io.open_private_directory(root)
    old: str | None = None
    try:
        try:
            info = os.stat("current", dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info is not None:
            if not stat.S_ISLNK(info.st_mode):
                raise DataValidationError("Current selection is not a canonical symlink")
            old = os.readlink("current", dir_fd=root_fd)
            if re.fullmatch(r"versions/[0-9a-f]{64}", old) is None:
                raise DataValidationError("Current selection is not canonical")
            admitted = bundle_io.open_private_directory(root / old)
            os.close(admitted)
        temporary = f".current.{secrets.token_hex(12)}.partial"
        os.symlink(f"versions/{receipt.artifact_sha256}", temporary, dir_fd=root_fd)
        try:
            os.replace(temporary, "current", src_dir_fd=root_fd, dst_dir_fd=root_fd)
            try:
                os.fsync(root_fd)
            except OSError as commit_error:
                recovery = f".current.recovery.{secrets.token_hex(12)}"
                try:
                    if old is None:
                        os.unlink("current", dir_fd=root_fd)
                    else:
                        os.symlink(old, recovery, dir_fd=root_fd)
                        os.replace(recovery, "current", src_dir_fd=root_fd, dst_dir_fd=root_fd)
                    os.fsync(root_fd)
                except OSError as recovery_error:
                    raise DataValidationError(
                        "Selection commit and recovery failed",
                        subtype="selection_recovery_failed",
                    ) from recovery_error
                raise DataValidationError(
                    "Selection commit failed; prior selection restored",
                    subtype="selection_commit_failed",
                ) from commit_error
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=root_fd)
    finally:
        os.close(root_fd)


def stage_release(
    item: ReleaseInput,
    *,
    data_root: Path,
    application_version: str,
    supported_schema: CompatibilityRange,
    limits: BundleLimits,
    lock_timeout_seconds: float = 30.0,
) -> MaterializationReceipt:
    manifest = _authenticate(item, application_version, supported_schema)
    _private_root(data_root)
    with _lock(data_root, lock_timeout_seconds):
        _admit_layout(data_root, _versions(data_root))
        return _stage_locked(item, manifest, data_root, limits)


def install_release(
    candidate: ReleaseInput,
    previous: ReleaseInput | None = None,
    *,
    data_root: Path,
    application_version: str,
    supported_schema: CompatibilityRange,
    limits: BundleLimits,
    lock_timeout_seconds: float = 30.0,
) -> MaterializationReceipt:
    candidate_manifest = _authenticate(candidate, application_version, supported_schema)
    _private_root(data_root)
    with _lock(data_root, lock_timeout_seconds):
        versions = _versions(data_root)
        _admit_layout(data_root, versions)
        bootstrap = (
            not any(versions.iterdir())
            and not (data_root / "current").exists()
            and not (data_root / "current").is_symlink()
            and set(os.listdir(data_root)) <= {".materialize.lock", "versions"}
        )
        current = data_root / "current"
        target = versions / candidate_manifest.artifact.sha256
        if (
            not bootstrap
            and previous is None
            and target.is_dir()
            and current.is_symlink()
            and os.readlink(current) == f"versions/{candidate_manifest.artifact.sha256}"
        ):
            return _verify_generation(target, candidate, candidate_manifest, limits)
        if bootstrap and previous is None:
            if (
                candidate_manifest.previous_known_good_digest
                != f"sha256:{candidate_manifest.artifact.sha256}"
            ):
                raise DataValidationError("Bootstrap predecessor is invalid")
        else:
            if previous is None:
                raise DataValidationError("An independently pinned direct predecessor is required")
            previous_manifest = _authenticate(previous, application_version, supported_schema)
            if (
                candidate_manifest.previous_known_good_digest
                != f"sha256:{previous_manifest.artifact.sha256}"
            ):
                raise DataValidationError("Candidate predecessor does not match")
            predecessor = _stage_locked(previous, previous_manifest, data_root, limits)
            _verify_generation(predecessor.path, previous, previous_manifest, limits)
        receipt = _stage_locked(candidate, candidate_manifest, data_root, limits)
        _verify_generation(receipt.path, candidate, candidate_manifest, limits)
        _select(data_root, receipt)
        return receipt


def rollback_release(
    target: ReleaseInput,
    *,
    data_root: Path,
    application_version: str,
    supported_schema: CompatibilityRange,
    limits: BundleLimits,
    lock_timeout_seconds: float = 30.0,
) -> MaterializationReceipt:
    if target.artifact_path is not None:
        raise DataValidationError("Rollback requires an already-retained generation")
    manifest = _authenticate(target, application_version, supported_schema)
    _private_root(data_root)
    with _lock(data_root, lock_timeout_seconds):
        _admit_layout(data_root, _versions(data_root))
        receipt = _stage_locked(target, manifest, data_root, limits)
        _select(data_root, receipt)
        return receipt


__all__ = [
    "MaterializationReceipt",
    "ReleaseInput",
    "install_release",
    "rollback_release",
    "stage_release",
]
