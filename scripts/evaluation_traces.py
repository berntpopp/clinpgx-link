"""Normalize retained Claude and Codex evaluation traces into strict evidence."""

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from scripts.evaluation_contracts import (
    BoundedString,
    Consumer,
    EffortUnavailableReason,
    FiniteNonNegativeFloat,
    FinitePositiveFloat,
    GitSha,
    Measurements,
    Sha256,
    StrictModel,
    StrictNonNegativeInt,
    StrictPositiveInt,
)

MAX_SUMMARY_BYTES = 16 * 1024 * 1024
MAX_TRACE_BYTES = 32 * 1024 * 1024
JsonDict = dict[str, Any]
PublicErrorCode = Literal[
    "invalid_input",
    "not_found",
    "ambiguous_query",
    "upstream_unavailable",
    "rate_limited",
    "internal",
]
CallOutcome = Literal["success", "mcp_error", "transport_failure", "incomplete"]
WireMirror = Literal["verified", "not_observed", "invalid"]
OrdinalTuple = Annotated[tuple[StrictNonNegativeInt, ...], Field(max_length=4096)]
ReasonTuple = Annotated[tuple[BoundedString, ...], Field(max_length=256)]


class AdapterInputError(ValueError):
    """Safe fixed-reason failure while opening or parsing a retained artifact."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class RunExpectation(StrictModel):
    """Controller-issued pins, independent of the retained runner summary."""

    schema_version: Literal[1] = 1
    consumer: Consumer
    candidate_sha: GitSha
    expected_model: BoundedString
    requested_model: BoundedString
    requested_effort: BoundedString | None
    prompt_sha256: Sha256
    summary_sha256: Sha256
    trace_sha256: Sha256
    hard_call_limit: StrictPositiveInt
    deadline_seconds: FinitePositiveFloat
    trace_limit_bytes: Annotated[StrictPositiveInt, Field(le=MAX_TRACE_BYTES)]


class NormalizedCall(StrictModel):
    index: StrictNonNegativeInt
    call_id: BoundedString
    tool: BoundedString
    arguments: JsonDict
    envelope: JsonDict | None
    outcome: CallOutcome
    error_code: PublicErrorCode | None
    boundary_elapsed_ms: FiniteNonNegativeFloat | None
    client_started_elapsed_ms: FiniteNonNegativeFloat | None
    client_completed_elapsed_ms: FiniteNonNegativeFloat | None
    raw_event_ordinals: OrdinalTuple
    wire_mirror: WireMirror

    @model_validator(mode="after")
    def finite_json_fields(self) -> "NormalizedCall":
        _finite_json(self.arguments)
        if self.envelope is not None:
            _finite_json(self.envelope)
        if (self.outcome == "mcp_error") != (self.error_code is not None):
            raise ValueError("error code must match MCP error outcome")
        return self


class NormalizedRun(StrictModel):
    schema_version: Literal[1] = 1
    consumer: Consumer
    candidate_sha: GitSha
    prompt_sha256: Sha256
    summary_sha256: Sha256
    trace_sha256: Sha256
    trace_bytes: StrictNonNegativeInt
    requested_model: BoundedString
    expected_model: BoundedString
    observed_model: BoundedString | None
    requested_effort: BoundedString | None
    observed_effort: BoundedString | None
    effort_unavailable_reason: EffortUnavailableReason | None
    model_rerouted: bool
    transport_passed: bool
    capture_complete: bool
    lifecycle_complete: bool
    hard_call_limit: StrictPositiveInt
    deadline_seconds: FinitePositiveFloat
    trace_limit_bytes: Annotated[StrictPositiveInt, Field(le=MAX_TRACE_BYTES)]
    duration_seconds: FiniteNonNegativeFloat
    calls: tuple[NormalizedCall, ...]
    final_answer: str | None
    measurements: Measurements
    failures: ReasonTuple
    limitations: ReasonTuple
    client_version: BoundedString | None = None
    raw_usage: JsonDict | None = None

    @model_validator(mode="after")
    def finite_raw_usage(self) -> "NormalizedRun":
        if self.raw_usage is not None:
            _finite_json(self.raw_usage)
        effort_unavailable = self.requested_effort is None or self.observed_effort is None
        if effort_unavailable != (self.effort_unavailable_reason is not None):
            raise ValueError("effort availability reason does not match trace evidence")
        return self


def _finite_json(value: object) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("value must be finite JSON")
        return
    if isinstance(value, list):
        for item in value:
            _finite_json(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _finite_json(item)
        return
    raise ValueError("value must be finite JSON")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise ValueError("nonfinite number")


def parse_json(raw: bytes) -> object:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise AdapterInputError("malformed_artifact") from exc


def parse_json_line(raw: bytes) -> dict[str, Any]:
    value = parse_json(raw)
    if not isinstance(value, dict):
        raise AdapterInputError("malformed_artifact")
    return value


def add_failure(failures: list[str], reason: str) -> None:
    if reason not in failures:
        failures.append(reason)


def read_artifacts(
    directory: Path, trace_name: str, expectation: RunExpectation
) -> tuple[bytes, bytes]:
    """Open the fixed artifact pair once through an owned private directory."""
    if (
        not directory.is_absolute()
        or ".." in directory.parts
        or directory in {Path.home(), Path(__file__).resolve().parents[1]}
    ):
        raise AdapterInputError("unsafe_io")
    try:
        details = directory.lstat()
        if directory.resolve(strict=True) != directory:
            raise AdapterInputError("unsafe_io")
    except AdapterInputError:
        raise
    except OSError as exc:
        raise AdapterInputError("unsafe_io") from exc
    if (
        not stat.S_ISDIR(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise AdapterInputError("unsafe_io")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(directory, flags)
    except OSError as exc:
        raise AdapterInputError("unsafe_io") from exc
    try:
        opened = os.fstat(directory_fd)
        if (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino):
            raise AdapterInputError("unsafe_io")
        summary = _read_one(directory_fd, "summary.json", MAX_SUMMARY_BYTES)
        trace = _read_one(directory_fd, trace_name, expectation.trace_limit_bytes)
    finally:
        os.close(directory_fd)
    if hashlib.sha256(summary).hexdigest() != expectation.summary_sha256:
        raise AdapterInputError("hash_mismatch")
    if hashlib.sha256(trace).hexdigest() != expectation.trace_sha256:
        raise AdapterInputError("hash_mismatch")
    return summary, trace


def _read_one(directory_fd: int, name: str, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
        with os.fdopen(descriptor, "rb", buffering=0) as handle:
            before = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or before.st_size > limit
            ):
                raise AdapterInputError("unsafe_io")
            raw = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
    except AdapterInputError:
        raise
    except OSError as exc:
        raise AdapterInputError("unsafe_io") from exc
    stable = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if not stable or len(raw) != before.st_size or len(raw) > limit:
        raise AdapterInputError("unsafe_io")
    return raw


def normalize_run(run_directory: Path, *, expected: RunExpectation) -> NormalizedRun:
    """Dispatch a retained run to the adapter selected by its controller pin."""
    if expected.consumer == "opus":
        from scripts.evaluation_trace_claude import normalize_claude_run

        return normalize_claude_run(run_directory, expected)
    from scripts.evaluation_trace_codex import normalize_codex_run

    return normalize_codex_run(run_directory, expected)


__all__ = [
    "AdapterInputError",
    "NormalizedCall",
    "NormalizedRun",
    "RunExpectation",
    "normalize_run",
]
