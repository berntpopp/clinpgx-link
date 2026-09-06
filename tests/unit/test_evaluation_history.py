"""Private append-only normalized attempt-history tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.evaluation_contracts import ASPECTS, AttemptEvidence, canonical_bytes
from scripts.evaluation_history import (
    EvaluationHistoryError,
    append_attempt,
    read_attempts,
)

SHA = "a" * 64
COMMIT = "c" * 40


def _attempt(attempt_id: str, *, transport_passed: bool = True) -> AttemptEvidence:
    scores = dict.fromkeys(ASPECTS, 95)
    return AttemptEvidence.model_validate(
        {
            "attempt_id": attempt_id,
            "campaign_id": "campaign-1",
            "batch_id": "development-1",
            "task_id": "task-01",
            "suite": "frozen12",
            "consumer": "opus",
            "candidate_sha": COMMIT,
            "snapshot_sha256": SHA,
            "config_sha256": SHA,
            "prompt_sha256": SHA,
            "trace_sha256": SHA,
            "source_assertions_sha256": SHA,
            "judge_view_sha256": SHA,
            "judge_report_sha256": SHA,
            "requested_model": "opus",
            "expected_model": "claude-opus-5",
            "observed_model": "claude-opus-5",
            "requested_effort": None,
            "observed_effort": None,
            "effort_unavailable_reason": "not_configured_or_exposed",
            "model_rerouted": False,
            "transport_passed": transport_passed,
            "trace_complete": True,
            "instructed_call_limit": 35,
            "hard_call_limit": 40,
            "actual_calls": 4,
            "deadline_seconds": 600.0,
            "duration_seconds": 12.0,
            "trace_limit_bytes": 33_554_432,
            "trace_bytes": 2048,
            "source_assertions_passed": True,
            "judge_blinding_verified": True,
            "admission_capacity_errors": 0,
            "self_scores": scores,
            "judge_scores": scores,
            "judge_model": "fable-judge",
            "judge_effort": "high",
            "rubric_sha256": SHA,
            "measurements": None,
        }
    )


def test_append_creates_private_canonical_history_and_retains_failed_attempt(
    tmp_path: Path,
) -> None:
    """A failed attempt must be durably retained with private directory/file modes."""
    directory = tmp_path / "private-attempts"
    evidence = _attempt("failed-attempt", transport_passed=False)
    path = append_attempt(directory, evidence)
    assert path == directory / "failed-attempt.json"
    assert path.read_bytes() == canonical_bytes(evidence)
    assert path.stat().st_mode & 0o777 == 0o600
    assert directory.stat().st_mode & 0o777 == 0o700
    assert read_attempts(directory) == (evidence,)


def test_append_is_exclusive_and_read_order_is_attempt_id_not_mtime(tmp_path: Path) -> None:
    """A collision must never overwrite, and timestamps must not affect replay order."""
    directory = tmp_path / "private-attempts"
    second = append_attempt(directory, _attempt("z-attempt"))
    first = append_attempt(directory, _attempt("a-attempt"))
    os.utime(second, (1, 1))
    os.utime(first, (2, 2))
    with pytest.raises(FileExistsError):
        append_attempt(directory, _attempt("a-attempt", transport_passed=False))
    assert [item.attempt_id for item in read_attempts(directory)] == [
        "a-attempt",
        "z-attempt",
    ]


@pytest.mark.parametrize("raw", [b"not-json", b'{"attempt_id":"broken"}\n'])
def test_read_rejects_malformed_or_noncanonical_artifacts(tmp_path: Path, raw: bytes) -> None:
    """Malformed and mutable-format files must not be interpreted as evidence."""
    directory = tmp_path / "private-attempts"
    directory.mkdir(mode=0o700)
    path = directory / "broken.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    with pytest.raises(EvaluationHistoryError):
        read_attempts(directory)


def test_read_rejects_symlink_and_filename_payload_mismatch(tmp_path: Path) -> None:
    """Artifact links and renamed payload identities must not cross the private boundary."""
    directory = tmp_path / "private-attempts"
    directory.mkdir(mode=0o700)
    outside = tmp_path / "outside.json"
    outside.write_bytes(canonical_bytes(_attempt("linked")))
    outside.chmod(0o600)
    (directory / "linked.json").symlink_to(outside)
    with pytest.raises(EvaluationHistoryError):
        read_attempts(directory)
    (directory / "linked.json").unlink()

    renamed = directory / "wrong-name.json"
    renamed.write_bytes(canonical_bytes(_attempt("actual-name")))
    renamed.chmod(0o600)
    with pytest.raises(EvaluationHistoryError):
        read_attempts(directory)


def test_history_rejects_oversized_artifact_and_unbounded_count(tmp_path: Path) -> None:
    """Reading must stop before parsing more than 1 MiB or 10,000 artifacts."""
    oversized_directory = tmp_path / "oversized"
    oversized_directory.mkdir(mode=0o700)
    oversized = oversized_directory / "large.json"
    oversized.write_bytes(b" " * (1024 * 1024 + 1))
    oversized.chmod(0o600)
    with pytest.raises(EvaluationHistoryError):
        read_attempts(oversized_directory)

    crowded = tmp_path / "crowded"
    crowded.mkdir(mode=0o700)
    for index in range(10_001):
        path = crowded / f"attempt-{index:05d}.json"
        path.touch(mode=0o600)
    with pytest.raises(EvaluationHistoryError):
        read_attempts(crowded)
    with pytest.raises(EvaluationHistoryError):
        append_attempt(crowded, _attempt("one-more"))


def test_history_rejects_nonspecific_or_nonprivate_directories(tmp_path: Path) -> None:
    """The caller must choose an explicit owned private path, never a broad directory."""
    with pytest.raises(EvaluationHistoryError):
        append_attempt(Path("relative-history"), _attempt("relative"))
    with pytest.raises(EvaluationHistoryError):
        append_attempt(Path.home(), _attempt("home"))

    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(EvaluationHistoryError):
        append_attempt(public, _attempt("public"))


def test_read_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    """Last-key-wins JSON parsing must not rewrite the meaning of stored evidence."""
    directory = tmp_path / "private-attempts"
    directory.mkdir(mode=0o700)
    payload = json.loads(canonical_bytes(_attempt("duplicate-key")))
    raw = canonical_bytes(payload)
    duplicate = raw[:-1] + b',"attempt_id":"duplicate-key"}'
    path = directory / "duplicate-key.json"
    path.write_bytes(duplicate)
    path.chmod(0o600)
    with pytest.raises(EvaluationHistoryError):
        read_attempts(directory)
