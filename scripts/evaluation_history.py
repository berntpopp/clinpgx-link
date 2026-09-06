"""Private append-only storage for normalized evaluation-attempt evidence."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from scripts.evaluation_contracts import AttemptEvidence, canonical_bytes

MAX_ATTEMPT_BYTES = 1024 * 1024
MAX_HISTORY_ATTEMPTS = 10_000
_FILENAME = re.compile(r"([A-Za-z0-9_.-]{1,80})\.json\Z")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class EvaluationHistoryError(ValueError):
    """A safe validation or resource-bound failure for private attempt history."""


def _validate_specific_path(directory: Path) -> None:
    if (
        not directory.is_absolute()
        or ".." in directory.parts
        or directory in {Path.home(), _REPOSITORY_ROOT}
    ):
        raise EvaluationHistoryError("attempt history path must be absolute and specific")
    try:
        if directory.resolve(strict=False) != directory:
            raise EvaluationHistoryError("attempt history path must not traverse links")
    except OSError as exc:
        raise EvaluationHistoryError("attempt history path cannot be resolved") from exc


def _open_private_directory(directory: Path, *, create: bool) -> int:
    _validate_specific_path(directory)
    if create:
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise EvaluationHistoryError("attempt history directory cannot be created") from exc
    try:
        details = directory.lstat()
    except OSError as exc:
        raise EvaluationHistoryError("attempt history directory is unavailable") from exc
    if (
        not stat.S_ISDIR(details.st_mode)
        or directory.is_symlink()
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise EvaluationHistoryError("attempt history directory must be owned and mode 0700")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError as exc:
        raise EvaluationHistoryError("attempt history directory cannot be opened safely") from exc
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino):
        os.close(descriptor)
        raise EvaluationHistoryError("attempt history directory changed during validation")
    return descriptor


def _entries(directory_fd: int) -> list[str]:
    try:
        names = os.listdir(directory_fd)
    except OSError as exc:
        raise EvaluationHistoryError("attempt history directory cannot be listed") from exc
    if len(names) > MAX_HISTORY_ATTEMPTS:
        raise EvaluationHistoryError("attempt history exceeds the artifact count bound")
    for name in names:
        if _FILENAME.fullmatch(name) is None:
            raise EvaluationHistoryError("attempt history contains an invalid artifact name")
        try:
            details = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise EvaluationHistoryError("attempt artifact cannot be inspected") from exc
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) != 0o600
        ):
            raise EvaluationHistoryError("attempt artifact must be an owned mode-0600 regular file")
    return sorted(names)


def append_attempt(directory: Path, evidence: AttemptEvidence) -> Path:
    """Exclusively persist one complete canonical artifact; collisions never overwrite."""
    descriptor = _open_private_directory(directory, create=True)
    try:
        entries = _entries(descriptor)
        if len(entries) >= MAX_HISTORY_ATTEMPTS:
            raise EvaluationHistoryError("attempt history has reached the artifact count bound")
        raw = canonical_bytes(evidence)
        if len(raw) > MAX_ATTEMPT_BYTES:
            raise EvaluationHistoryError("attempt artifact exceeds the byte bound")
        filename = f"{evidence.attempt_id}.json"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        artifact_fd = os.open(filename, flags, 0o600, dir_fd=descriptor)
        with os.fdopen(artifact_fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    return directory / filename


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _invalid_number(_value: str) -> None:
    raise ValueError("nonfinite JSON number")


def _read_artifact(directory_fd: int, filename: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        artifact_fd = os.open(filename, flags, dir_fd=directory_fd)
        with os.fdopen(artifact_fd, "rb", buffering=0) as handle:
            details = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_nlink != 1
                or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) != 0o600
                or details.st_size > MAX_ATTEMPT_BYTES
            ):
                raise EvaluationHistoryError("attempt artifact exceeds its regular-file bound")
            raw = handle.read(MAX_ATTEMPT_BYTES + 1)
    except EvaluationHistoryError:
        raise
    except OSError as exc:
        raise EvaluationHistoryError("attempt artifact cannot be opened safely") from exc
    if len(raw) > MAX_ATTEMPT_BYTES:
        raise EvaluationHistoryError("attempt artifact exceeds the byte bound")
    return raw


def read_attempts(directory: Path) -> tuple[AttemptEvidence, ...]:
    """Validate and return all normalized artifacts ordered only by attempt identity."""
    descriptor = _open_private_directory(directory, create=False)
    try:
        names = _entries(descriptor)
        attempts: list[AttemptEvidence] = []
        identities: set[str] = set()
        for filename in names:
            raw = _read_artifact(descriptor, filename)
            try:
                value = json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=_unique_object,
                    parse_constant=_invalid_number,
                )
                if canonical_bytes(value) != raw:
                    raise ValueError("artifact is not canonical JSON")
                evidence = AttemptEvidence.model_validate(value)
                if canonical_bytes(evidence) != raw:
                    raise ValueError("artifact changes under contract validation")
            except (UnicodeError, ValueError, TypeError, RecursionError, ValidationError) as exc:
                raise EvaluationHistoryError("attempt artifact is malformed") from exc
            match = _FILENAME.fullmatch(filename)
            if match is None or match.group(1) != evidence.attempt_id:
                raise EvaluationHistoryError("attempt artifact name does not match its payload")
            if evidence.attempt_id in identities:
                raise EvaluationHistoryError("attempt history contains a duplicate identity")
            identities.add(evidence.attempt_id)
            attempts.append(evidence)
    finally:
        os.close(descriptor)
    return tuple(attempts)


__all__ = ["EvaluationHistoryError", "append_attempt", "read_attempts"]
