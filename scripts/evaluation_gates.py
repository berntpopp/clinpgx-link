"""Pure deterministic gates over declared, normalized evaluation evidence.

Runner, source-check, and blinded-judge stages create the evidence. This module
binds their declared identities and values but does not independently verify the
truth of their reports, parse raw traces, execute prompts, or invoke models.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from scripts.evaluation_contracts import (
    ASPECTS,
    AspectCoverage,
    AspectScores,
    AttemptEvidence,
    BatchDeclaration,
    CampaignDeclaration,
    Consumer,
    ConsumerStatistics,
    GateResult,
)

_CONSUMERS: tuple[Consumer, ...] = ("opus", "terra")
_TRUST_LIMITATION = "normalized_evidence_truth_not_independently_verified"
_SUITE_LIMITS = {
    "frozen12": (35, 40, 600.0, 33_554_432),
    "heldout": (35, 40, 600.0, 33_554_432),
    "frozen18": (30, 30, 180.0, 33_554_432),
}


def _score_values(scores: AspectScores) -> dict[str, int | None]:
    return {aspect: getattr(scores, aspect) for aspect in ASPECTS}


def _statistics(attempts: Sequence[AttemptEvidence]) -> dict[Consumer, ConsumerStatistics]:
    result: dict[Consumer, ConsumerStatistics] = {}
    for consumer in _CONSUMERS:
        selected = [attempt for attempt in attempts if attempt.consumer == consumer]
        series_values: dict[str, dict[str, list[int]]] = {
            "self": {aspect: [] for aspect in ASPECTS},
            "judge": {aspect: [] for aspect in ASPECTS},
        }
        for attempt in selected:
            for series, scores in (
                ("self", attempt.self_scores),
                ("judge", attempt.judge_scores),
            ):
                for aspect, value in _score_values(scores).items():
                    if value is not None:
                        series_values[series][aspect].append(value)
        self_values = series_values["self"]
        judge_values = series_values["judge"]
        result[consumer] = ConsumerStatistics(
            self_minima=AspectScores.model_validate(
                {aspect: min(values) if values else None for aspect, values in self_values.items()}
            ),
            judge_minima=AspectScores.model_validate(
                {aspect: min(values) if values else None for aspect, values in judge_values.items()}
            ),
            self_coverage=AspectCoverage.model_validate(
                {aspect: len(values) for aspect, values in self_values.items()}
            ),
            judge_coverage=AspectCoverage.model_validate(
                {aspect: len(values) for aspect, values in judge_values.items()}
            ),
        )
    return result


def _differences(attempts: Sequence[AttemptEvidence]) -> dict[str, AspectScores]:
    result: dict[str, AspectScores] = {}
    identity_counts = Counter(attempt.attempt_id for attempt in attempts)
    for attempt in attempts:
        if identity_counts[attempt.attempt_id] > 1:
            result[attempt.attempt_id] = AspectScores.model_validate(dict.fromkeys(ASPECTS))
            continue
        self_values = _score_values(attempt.self_scores)
        judge_values = _score_values(attempt.judge_scores)
        result.setdefault(
            attempt.attempt_id,
            AspectScores.model_validate(
                {
                    aspect: _absolute_difference(self_values[aspect], judge_values[aspect])
                    for aspect in ASPECTS
                }
            ),
        )
    return result


def _absolute_difference(left: int | None, right: int | None) -> int | None:
    return abs(left - right) if left is not None and right is not None else None


def _limitations(attempts: Sequence[AttemptEvidence]) -> set[str]:
    limitations = {_TRUST_LIMITATION}
    limitations.update(
        f"effort_{attempt.effort_unavailable_reason}"
        for attempt in attempts
        if attempt.effort_unavailable_reason is not None
    )
    return limitations


def _result(
    attempts: Sequence[AttemptEvidence], failures: Iterable[str], limitations: Iterable[str]
) -> GateResult:
    stable_failures = tuple(sorted(set(failures)))
    return GateResult(
        passed=not stable_failures,
        failures=stable_failures,
        limitations=tuple(sorted(set(limitations))),
        per_consumer=_statistics(attempts),
        inter_rater_differences=_differences(attempts),
    )


def _attempt_failures(evidence: AttemptEvidence) -> set[str]:
    failures: set[str] = set()
    if evidence.observed_model != evidence.expected_model:
        failures.add("observed_model_mismatch")
    if (
        evidence.requested_effort is not None
        and evidence.observed_effort is not None
        and evidence.requested_effort != evidence.observed_effort
    ):
        failures.add("known_effort_mismatch")
    if evidence.model_rerouted:
        failures.add("model_rerouted")
    if not evidence.transport_passed:
        failures.add("transport_failed")
    if not evidence.trace_complete:
        failures.add("trace_incomplete")
    if evidence.source_assertions_passed is False:
        failures.add("source_assertions_failed")
    elif evidence.source_assertions_passed is None:
        failures.add("source_assertions_unavailable")
    if evidence.judge_blinding_verified is False:
        failures.add("judge_blinding_failed")
    elif evidence.judge_blinding_verified is None:
        failures.add("judge_blinding_unavailable")
    for value, failure in (
        (evidence.trace_sha256, "trace_digest_missing"),
        (evidence.source_assertions_sha256, "source_assertions_digest_missing"),
        (evidence.judge_view_sha256, "judge_view_digest_missing"),
        (evidence.judge_report_sha256, "judge_report_digest_missing"),
        (evidence.rubric_sha256, "rubric_digest_missing"),
    ):
        if value is None:
            failures.add(failure)
    if evidence.judge_model is None or evidence.judge_effort is None:
        failures.add("judge_identity_missing")
    if any(
        value is not None and value <= 90
        for scores in (evidence.self_scores, evidence.judge_scores)
        for value in _score_values(scores).values()
    ):
        failures.add("score_not_above_90")
    if evidence.actual_calls > evidence.instructed_call_limit:
        failures.add("instructed_call_limit_exceeded")
    if evidence.actual_calls > evidence.hard_call_limit:
        failures.add("hard_call_limit_exceeded")
    if evidence.duration_seconds > evidence.deadline_seconds:
        failures.add("deadline_exceeded")
    if evidence.trace_bytes > evidence.trace_limit_bytes:
        failures.add("trace_limit_exceeded")
    if evidence.admission_capacity_errors:
        failures.add("admission_capacity_error")
    required_limits = _SUITE_LIMITS.get(evidence.suite)
    if (
        required_limits is not None
        and (
            evidence.instructed_call_limit,
            evidence.hard_call_limit,
            evidence.deadline_seconds,
            evidence.trace_limit_bytes,
        )
        != required_limits
    ):
        failures.add("suite_limit_mismatch")
    if evidence.suite == "exploratory":
        failures.add("exploratory_not_ux_acceptance")
    elif evidence.suite == "frozen18":
        failures.add("frozen18_not_ux_acceptance")
    return failures


def evaluate_attempt(evidence: AttemptEvidence) -> GateResult:
    """Evaluate one normalized attempt without manufacturing missing observations."""
    attempts = (evidence,)
    return _result(attempts, _attempt_failures(evidence), _limitations(attempts))


def _is_nonacceptance_taskset(declaration: BatchDeclaration) -> bool:
    return declaration.phase == "exploratory" or any(
        task.suite in {"exploratory", "frozen18"} for task in declaration.expected_tasks.values()
    )


def _batch_failures(declaration: BatchDeclaration, attempts: Sequence[AttemptEvidence]) -> set[str]:
    failures: set[str] = set()
    attempt_ids = [attempt.attempt_id for attempt in attempts]
    if len(set(attempt_ids)) != len(attempt_ids):
        failures.add("duplicate_attempt_id")
    expected = {
        (declaration.batch_id, task_id, consumer)
        for task_id in declaration.expected_tasks
        for consumer in declaration.required_consumers
    }
    actual = Counter((attempt.batch_id, attempt.task_id, attempt.consumer) for attempt in attempts)
    if any(actual[key] == 0 for key in expected):
        failures.add("missing_attempt")
    if any(key not in expected or count > 1 for key, count in actual.items()):
        failures.add("extra_attempt")
    if any(count > 1 for count in actual.values()):
        failures.add("duplicate_task_consumer")
    for attempt in attempts:
        attempt_failures = _attempt_failures(attempt)
        if attempt_failures:
            failures.add("attempt_failed")
            failures.update(attempt_failures)
        if attempt.campaign_id != declaration.campaign_id:
            failures.add("campaign_identity_mismatch")
        if attempt.batch_id != declaration.batch_id:
            failures.add("batch_identity_mismatch")
        expected_task = declaration.expected_tasks.get(attempt.task_id)
        if expected_task is None:
            failures.add("task_identity_mismatch")
        else:
            if attempt.suite != expected_task.suite:
                failures.add("suite_identity_mismatch")
            if attempt.prompt_sha256 != expected_task.prompt_sha256:
                failures.add("prompt_identity_mismatch")
        if attempt.candidate_sha != declaration.candidate_sha:
            failures.add("candidate_identity_mismatch")
        if attempt.snapshot_sha256 != declaration.snapshot_sha256:
            failures.add("snapshot_identity_mismatch")
        if attempt.consumer not in declaration.required_consumers:
            failures.add("consumer_identity_mismatch")
            continue
        consumer = attempt.consumer
        if attempt.config_sha256 != declaration.config_sha256_by_consumer[consumer]:
            failures.add("config_identity_mismatch")
        if attempt.requested_model != declaration.requested_model_by_consumer[consumer]:
            failures.add("requested_model_identity_mismatch")
        if (
            attempt.expected_model != declaration.model_by_consumer[consumer]
            or attempt.observed_model != declaration.model_by_consumer[consumer]
        ):
            failures.add("model_identity_mismatch")
        if attempt.requested_effort != declaration.effort_by_consumer[consumer]:
            failures.add("effort_identity_mismatch")
        declared_effort = declaration.effort_by_consumer[consumer]
        if declared_effort is not None and attempt.observed_effort != declared_effort:
            failures.add("effort_identity_mismatch")
        if attempt.judge_model != declaration.judge_model:
            failures.add("judge_model_identity_mismatch")
        if attempt.judge_effort != declaration.judge_effort:
            failures.add("judge_effort_identity_mismatch")
        if attempt.rubric_sha256 != declaration.rubric_sha256:
            failures.add("rubric_identity_mismatch")
    if declaration.phase == "exploratory" or any(
        task.suite == "exploratory" for task in declaration.expected_tasks.values()
    ):
        failures.add("exploratory_not_ux_acceptance")
    if any(task.suite == "frozen18" for task in declaration.expected_tasks.values()):
        failures.add("frozen18_not_ux_acceptance")
    if not _is_nonacceptance_taskset(declaration):
        statistics = _statistics(attempts)
        if any(
            getattr(coverage, aspect) == 0
            for consumer in declaration.required_consumers
            for coverage in (
                statistics[consumer].self_coverage,
                statistics[consumer].judge_coverage,
            )
            for aspect in ASPECTS
        ):
            failures.add("missing_aspect_coverage")
    return failures


def evaluate_batch(
    declaration: BatchDeclaration, attempts: Sequence[AttemptEvidence]
) -> GateResult:
    """Evaluate the exact declared task-by-consumer product; no input is filtered."""
    local_attempts = tuple(attempts)
    return _result(
        local_attempts,
        _batch_failures(declaration, local_attempts),
        _limitations(local_attempts),
    )


def evaluate_campaign(
    declaration: CampaignDeclaration, attempts: Sequence[AttemptEvidence]
) -> GateResult:
    """Require one passing development batch and all three fresh validations."""
    all_attempts = tuple(attempts)
    failures: set[str] = set()
    batches = (declaration.development, *declaration.validation)
    expected = {
        (batch.batch_id, task_id, consumer)
        for batch in batches
        for task_id in batch.expected_tasks
        for consumer in batch.required_consumers
    }
    actual = Counter(
        (attempt.batch_id, attempt.task_id, attempt.consumer) for attempt in all_attempts
    )
    if any(actual[key] == 0 for key in expected):
        failures.add("campaign_missing_attempt")
    if any(key not in expected or count > 1 for key, count in actual.items()):
        failures.add("campaign_extra_attempt")
    attempt_ids = [attempt.attempt_id for attempt in all_attempts]
    if len(set(attempt_ids)) != len(attempt_ids):
        failures.add("duplicate_attempt_id")
    if any(count > 1 for count in actual.values()):
        failures.add("duplicate_task_consumer")
    for attempt in all_attempts:
        failures.update(_attempt_failures(attempt))
        if attempt.campaign_id != declaration.campaign_id:
            failures.add("campaign_identity_mismatch")
    for index, batch in enumerate(batches):
        batch_attempts = tuple(
            attempt for attempt in all_attempts if attempt.batch_id == batch.batch_id
        )
        batch_result = evaluate_batch(batch, batch_attempts)
        if not batch_result.passed:
            failures.add("development_batch_failed" if index == 0 else "validation_batch_failed")
            failures.update(batch_result.failures)
    return _result(all_attempts, failures, _limitations(all_attempts))


__all__ = ["evaluate_attempt", "evaluate_batch", "evaluate_campaign"]
