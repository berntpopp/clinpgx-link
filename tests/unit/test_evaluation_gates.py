"""Deterministic acceptance-gate tests over normalized evaluation evidence."""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from scripts.evaluation_contracts import (
    ASPECTS,
    AttemptEvidence,
    BatchDeclaration,
    CampaignDeclaration,
    canonical_bytes,
    estimated_tokens,
)
from scripts.evaluation_gates import evaluate_attempt, evaluate_batch, evaluate_campaign

SHA = "a" * 64
ALT_SHA = "b" * 64
COMMIT = "c" * 40


def _scores(value: int | None = 95) -> dict[str, int | None]:
    return dict.fromkeys(ASPECTS, value)


def _attempt(**overrides: object) -> AttemptEvidence:
    consumer = overrides.get("consumer", "opus")
    is_terra = consumer == "terra"
    task_id = str(overrides.get("task_id", "task-01"))
    values: dict[str, object] = {
        "attempt_id": f"dev.{task_id}.{consumer}",
        "campaign_id": "campaign-1",
        "batch_id": "development-1",
        "task_id": task_id,
        "suite": "frozen12",
        "consumer": consumer,
        "candidate_sha": COMMIT,
        "snapshot_sha256": SHA,
        "config_sha256": SHA,
        "prompt_sha256": SHA,
        "trace_sha256": SHA,
        "source_assertions_sha256": SHA,
        "judge_view_sha256": SHA,
        "judge_report_sha256": SHA,
        "requested_model": "gpt-5.6-terra" if is_terra else "opus",
        "expected_model": "gpt-5.6-terra" if is_terra else "claude-opus-5",
        "observed_model": "gpt-5.6-terra" if is_terra else "claude-opus-5",
        "requested_effort": "high" if is_terra else None,
        "observed_effort": "high" if is_terra else None,
        "effort_unavailable_reason": None if is_terra else "not_configured_or_exposed",
        "model_rerouted": False,
        "transport_passed": True,
        "trace_complete": True,
        "instructed_call_limit": 35,
        "hard_call_limit": 40,
        "actual_calls": 9,
        "deadline_seconds": 600.0,
        "duration_seconds": 42.0,
        "trace_limit_bytes": 33_554_432,
        "trace_bytes": 4096,
        "source_assertions_passed": True,
        "judge_blinding_verified": True,
        "admission_capacity_errors": 0,
        "self_scores": _scores(95),
        "judge_scores": _scores(93),
        "judge_model": "fable-judge",
        "judge_effort": "high",
        "rubric_sha256": SHA,
        "measurements": None,
    }
    values.update(overrides)
    if "attempt_id" not in overrides:
        values["attempt_id"] = f"{values['batch_id']}.{task_id}.{consumer}"
    return AttemptEvidence.model_validate(values)


def test_missing_observed_model_is_recordable_and_nonpassing() -> None:
    evidence = _attempt(observed_model=None, transport_passed=False)

    result = evaluate_attempt(evidence)

    assert evidence.observed_model is None
    assert result.passed is False
    assert "observed_model_mismatch" in result.failures
    assert "transport_failed" in result.failures


def _tasks(count: int, *, heldout: int = 0) -> dict[str, dict[str, str]]:
    tasks = {
        f"task-{index:02d}": {"suite": "frozen12", "prompt_sha256": SHA}
        for index in range(1, count + 1)
    }
    tasks.update(
        {
            f"heldout-{index:02d}": {"suite": "heldout", "prompt_sha256": ALT_SHA}
            for index in range(1, heldout + 1)
        }
    )
    return tasks


def _batch(
    *,
    batch_id: str = "development-1",
    phase: str = "development",
    tasks: dict[str, dict[str, str]] | None = None,
    consumers: tuple[str, ...] = ("opus", "terra"),
    **overrides: object,
) -> BatchDeclaration:
    model_by_consumer = {
        consumer: "claude-opus-5" if consumer == "opus" else "gpt-5.6-terra"
        for consumer in consumers
    }
    requested_model_by_consumer = {
        consumer: "opus" if consumer == "opus" else "gpt-5.6-terra" for consumer in consumers
    }
    effort_by_consumer = {
        consumer: None if consumer == "opus" else "high" for consumer in consumers
    }
    values: dict[str, object] = {
        "campaign_id": "campaign-1",
        "batch_id": batch_id,
        "phase": phase,
        "required_consumers": consumers,
        "expected_tasks": tasks if tasks is not None else _tasks(12),
        "candidate_sha": COMMIT,
        "snapshot_sha256": SHA,
        "config_sha256_by_consumer": dict.fromkeys(consumers, SHA),
        "model_by_consumer": model_by_consumer,
        "requested_model_by_consumer": requested_model_by_consumer,
        "effort_by_consumer": effort_by_consumer,
        "judge_model": "fable-judge",
        "judge_effort": "high",
        "rubric_sha256": SHA,
        "parallelism": 4,
    }
    values.update(overrides)
    return BatchDeclaration.model_validate(values)


def _campaign(*, terra_effort: str = "high") -> CampaignDeclaration:
    validation_tasks = _tasks(12, heldout=5)
    effort_by_consumer = {"opus": None, "terra": terra_effort}
    return CampaignDeclaration(
        campaign_id="campaign-1",
        development=_batch(effort_by_consumer=effort_by_consumer),
        validation=tuple(
            _batch(
                batch_id=f"validation-{index}",
                phase="validation",
                tasks=validation_tasks,
                effort_by_consumer=effort_by_consumer,
            )
            for index in range(1, 4)
        ),
    )


def _batch_attempts(declaration: BatchDeclaration) -> list[AttemptEvidence]:
    attempts: list[AttemptEvidence] = []
    for task_id, task in declaration.expected_tasks.items():
        for consumer in declaration.required_consumers:
            attempts.append(
                _attempt(
                    attempt_id=f"{declaration.batch_id}.{task_id}.{consumer}",
                    campaign_id=declaration.campaign_id,
                    batch_id=declaration.batch_id,
                    task_id=task_id,
                    suite=task.suite,
                    consumer=consumer,
                    candidate_sha=declaration.candidate_sha,
                    snapshot_sha256=declaration.snapshot_sha256,
                    config_sha256=declaration.config_sha256_by_consumer[consumer],
                    prompt_sha256=task.prompt_sha256,
                    requested_model=declaration.requested_model_by_consumer[consumer],
                    expected_model=declaration.model_by_consumer[consumer],
                    observed_model=declaration.model_by_consumer[consumer],
                    requested_effort=declaration.effort_by_consumer[consumer],
                    observed_effort=declaration.effort_by_consumer[consumer],
                    effort_unavailable_reason=(
                        "not_configured_or_exposed"
                        if declaration.effort_by_consumer[consumer] is None
                        else None
                    ),
                    judge_model=declaration.judge_model,
                    judge_effort=declaration.judge_effort,
                    rubric_sha256=declaration.rubric_sha256,
                )
            )
    return attempts


def _campaign_attempts(declaration: CampaignDeclaration) -> list[AttemptEvidence]:
    return [
        attempt
        for batch in (declaration.development, *declaration.validation)
        for attempt in _batch_attempts(batch)
    ]


def test_evaluation_contract_api_and_canonical_unicode_are_stable() -> None:
    """Missing or noncanonical contract helpers must break the public evaluator API."""
    assert ASPECTS == (
        "correctness_confidence",
        "completeness",
        "discoverability",
        "token_efficiency",
        "speed",
        "error_recovery",
        "provenance_clarity",
        "overall_usability",
    )
    assert canonical_bytes({"z": 1, "é": "雪"}) == '{"z":1,"é":"雪"}'.encode()
    assert estimated_tokens({"é": "雪"}) == 3


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_bytes_rejects_nonfinite_json(value: float) -> None:
    """Nonfinite values must never enter hashed evaluation evidence."""
    with pytest.raises(ValueError):
        canonical_bytes({"value": value})


def test_attempt_contract_preserves_alias_and_unknown_effort_without_fabrication() -> None:
    """A raw Opus alias and unavailable effort must remain distinct from effective identity."""
    evidence = _attempt()
    assert evidence.requested_model == "opus"
    assert evidence.expected_model == evidence.observed_model == "claude-opus-5"
    assert evidence.requested_effort is evidence.observed_effort is None
    assert evidence.effort_unavailable_reason == "not_configured_or_exposed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("actual_calls", True),
        ("admission_capacity_errors", False),
        ("trace_bytes", True),
        ("self_scores", {**_scores(), "speed": True}),
        ("duration_seconds", math.nan),
        ("deadline_seconds", math.inf),
    ],
)
def test_attempt_contract_rejects_bool_counts_scores_and_nonfinite_numbers(
    field: str, value: object
) -> None:
    """Loose numeric coercion must not turn booleans or nonfinite values into evidence."""
    with pytest.raises(ValidationError):
        _attempt(**{field: value})


def test_attempt_contract_requires_exact_aspect_keys_and_effort_reason() -> None:
    """Incomplete score maps and unexplained unknown effort must be malformed input."""
    with pytest.raises(ValidationError):
        _attempt(self_scores={"speed": 95})
    with pytest.raises(ValidationError):
        _attempt(effort_unavailable_reason=None)
    with pytest.raises(ValidationError):
        _attempt(
            requested_effort="high",
            observed_effort="high",
            effort_unavailable_reason="not_exposed_by_client",
        )


def test_batch_contract_requires_exact_unique_consumer_pin_maps() -> None:
    """Missing, extra, or repeated consumer declarations must not reach evaluation."""
    with pytest.raises(ValidationError):
        _batch(required_consumers=("opus", "opus"))
    with pytest.raises(ValidationError):
        _batch(model_by_consumer={"opus": "claude-opus-5"})


def test_campaign_contract_freezes_all_three_validation_declarations() -> None:
    """A campaign cannot omit repetitions, held-out tasks, or cross-batch pins."""
    campaign = _campaign()
    assert len(campaign.validation) == 3
    with pytest.raises(ValidationError):
        CampaignDeclaration(
            campaign_id="campaign-1",
            development=campaign.development,
            validation=campaign.validation[:2],
        )
    drifted = campaign.validation[2].model_copy(update={"rubric_sha256": ALT_SHA})
    with pytest.raises(ValidationError):
        CampaignDeclaration(
            campaign_id="campaign-1",
            development=campaign.development,
            validation=(*campaign.validation[:2], drifted),
        )


def test_attempt_accepts_declared_opus_alias_and_reports_unknown_effort_limitation() -> None:
    """Comparing a CLI alias directly to an effective model would reject real evidence."""
    result = evaluate_attempt(_attempt())
    assert result.passed
    assert result.failures == ()
    assert result.limitations == (
        "effort_not_configured_or_exposed",
        "normalized_evidence_truth_not_independently_verified",
    )
    assert result.per_consumer["opus"].self_minima.correctness_confidence == 95
    assert result.per_consumer["terra"].self_minima.correctness_confidence is None
    assert result.inter_rater_differences["development-1.task-01.opus"].speed == 2


@pytest.mark.parametrize(
    ("overrides", "failure"),
    [
        ({"observed_model": "claude-sonnet-5"}, "observed_model_mismatch"),
        ({"model_rerouted": True}, "model_rerouted"),
        ({"transport_passed": False}, "transport_failed"),
        ({"trace_complete": False}, "trace_incomplete"),
        ({"actual_calls": 36}, "instructed_call_limit_exceeded"),
        ({"instructed_call_limit": 36}, "suite_limit_mismatch"),
        ({"duration_seconds": 601.0}, "deadline_exceeded"),
        ({"trace_bytes": 33_554_433}, "trace_limit_exceeded"),
        ({"source_assertions_passed": False}, "source_assertions_failed"),
        ({"source_assertions_passed": None}, "source_assertions_unavailable"),
        ({"judge_blinding_verified": None}, "judge_blinding_unavailable"),
        ({"admission_capacity_errors": 1}, "admission_capacity_error"),
        ({"trace_sha256": None}, "trace_digest_missing"),
        ({"source_assertions_sha256": None}, "source_assertions_digest_missing"),
        ({"judge_view_sha256": None}, "judge_view_digest_missing"),
        ({"judge_report_sha256": None}, "judge_report_digest_missing"),
        ({"judge_model": None}, "judge_identity_missing"),
        ({"rubric_sha256": None}, "rubric_digest_missing"),
        ({"self_scores": {**_scores(), "speed": 90}}, "score_not_above_90"),
        (
            {
                "requested_effort": "high",
                "observed_effort": "medium",
                "effort_unavailable_reason": None,
            },
            "known_effort_mismatch",
        ),
    ],
)
def test_attempt_rejects_each_nonpassing_evidence_condition(
    overrides: dict[str, object], failure: str
) -> None:
    """Removing an attempt check must let its specific nonpassing evidence through."""
    result = evaluate_attempt(_attempt(**overrides))
    assert not result.passed
    assert failure in result.failures


def test_frozen18_limits_are_checked_without_requiring_subjective_scores() -> None:
    """The separate original-18 factual gate must not acquire a fabricated UX rating gate."""
    result = evaluate_attempt(
        _attempt(
            suite="frozen18",
            instructed_call_limit=30,
            hard_call_limit=30,
            deadline_seconds=180.0,
            self_scores=_scores(None),
            judge_scores=_scores(None),
        )
    )
    assert not result.passed
    assert result.failures == ("frozen18_not_ux_acceptance",)


def test_passing_batch_reports_literal_minima_coverage_and_differences() -> None:
    """An averaged score or hidden null cannot replace per-series minima and coverage."""
    declaration = _batch()
    attempts = _batch_attempts(declaration)
    attempts[0] = attempts[0].model_copy(
        update={"self_scores": attempts[0].self_scores.model_copy(update={"speed": 92})}
    )
    result = evaluate_batch(declaration, attempts)
    assert result.passed
    assert result.per_consumer["opus"].self_minima.speed == 92
    assert result.per_consumer["opus"].self_coverage.speed == 12
    assert result.per_consumer["terra"].judge_minima.speed == 93
    assert result.per_consumer["terra"].judge_coverage.speed == 12
    assert result.inter_rater_differences[attempts[0].attempt_id].speed == 1


def test_batch_fails_when_one_consumer_series_has_no_aspect_coverage() -> None:
    """Null scores must remain unobserved instead of being treated as perfect values."""
    declaration = _batch()
    attempts = _batch_attempts(declaration)
    attempts = [
        attempt.model_copy(
            update={"judge_scores": attempt.judge_scores.model_copy(update={"speed": None})}
        )
        if attempt.consumer == "terra"
        else attempt
        for attempt in attempts
    ]
    result = evaluate_batch(declaration, attempts)
    assert not result.passed
    assert "missing_aspect_coverage" in result.failures
    assert result.per_consumer["terra"].judge_minima.speed is None
    assert result.per_consumer["terra"].judge_coverage.speed == 0


@pytest.mark.parametrize(
    ("mutate", "failure"),
    [
        (lambda attempts: attempts[:-1], "missing_attempt"),
        (lambda attempts: [*attempts, attempts[0]], "duplicate_attempt_id"),
        (
            lambda attempts: [
                attempts[0].model_copy(update={"prompt_sha256": ALT_SHA}),
                *attempts[1:],
            ],
            "prompt_identity_mismatch",
        ),
        (
            lambda attempts: [
                attempts[0].model_copy(update={"candidate_sha": "d" * 40}),
                *attempts[1:],
            ],
            "candidate_identity_mismatch",
        ),
        (
            lambda attempts: [
                attempts[0].model_copy(update={"config_sha256": ALT_SHA}),
                *attempts[1:],
            ],
            "config_identity_mismatch",
        ),
        (
            lambda attempts: [
                attempts[0].model_copy(update={"requested_model": "claude-opus-5"}),
                *attempts[1:],
            ],
            "requested_model_identity_mismatch",
        ),
        (
            lambda attempts: [
                attempts[0].model_copy(update={"rubric_sha256": ALT_SHA}),
                *attempts[1:],
            ],
            "rubric_identity_mismatch",
        ),
    ],
)
def test_batch_rejects_missing_duplicate_or_mismatched_declared_evidence(
    mutate: Callable[[list[AttemptEvidence]], list[AttemptEvidence]], failure: str
) -> None:
    """No missing, duplicate, or identity-drifted attempt may be selected around."""
    declaration = _batch()
    result = evaluate_batch(declaration, mutate(_batch_attempts(declaration)))
    assert not result.passed
    assert failure in result.failures


def test_batch_rejects_duplicate_task_consumer_even_with_distinct_attempt_id() -> None:
    """Changing only an attempt ID must not turn a duplicate run into extra evidence."""
    declaration = _batch()
    attempts = _batch_attempts(declaration)
    duplicate = attempts[0].model_copy(update={"attempt_id": "another-attempt"})
    result = evaluate_batch(declaration, [*attempts, duplicate])
    assert "duplicate_task_consumer" in result.failures
    assert "extra_attempt" in result.failures


def test_duplicate_attempt_identity_never_selects_one_inter_rater_value() -> None:
    """A duplicate ID must fail with null differences instead of choosing one run."""
    declaration = _batch()
    attempts = _batch_attempts(declaration)
    duplicate = attempts[0].model_copy(
        update={"judge_scores": attempts[0].judge_scores.model_copy(update={"speed": 99})}
    )
    result = evaluate_batch(declaration, [*attempts, duplicate])
    assert "duplicate_attempt_id" in result.failures
    assert result.inter_rater_differences[attempts[0].attempt_id].speed is None


def test_batch_requires_observed_known_terra_effort_pin() -> None:
    """A Terra batch declaring high must not pass when the client observation is absent."""
    declaration = _batch()
    attempts = _batch_attempts(declaration)
    terra_index = next(
        index for index, attempt in enumerate(attempts) if attempt.consumer == "terra"
    )
    attempts[terra_index] = attempts[terra_index].model_copy(
        update={
            "observed_effort": None,
            "effort_unavailable_reason": "not_exposed_by_client",
        }
    )
    result = evaluate_batch(declaration, attempts)
    assert not result.passed
    assert "effort_identity_mismatch" in result.failures


def test_exploratory_batch_and_frozen18_taskset_never_pass_ux_acceptance() -> None:
    """Exploratory or original-18 work must not substitute for the UX campaign."""
    exploratory = _batch(
        phase="exploratory",
        tasks={"probe": {"suite": "exploratory", "prompt_sha256": SHA}},
        consumers=("opus",),
        parallelism=1,
    )
    exploratory_attempts = _batch_attempts(exploratory)
    exploratory_attempts[0] = exploratory_attempts[0].model_copy(
        update={"instructed_call_limit": 2, "hard_call_limit": 3, "deadline_seconds": 5.0}
    )
    exploratory_result = evaluate_batch(exploratory, exploratory_attempts)
    assert "exploratory_not_ux_acceptance" in exploratory_result.failures
    assert "acceptance_consumer_set_mismatch" not in exploratory_result.failures
    assert "acceptance_parallelism_mismatch" not in exploratory_result.failures

    original = _batch(
        tasks={"original": {"suite": "frozen18", "prompt_sha256": SHA}},
        consumers=("opus",),
        parallelism=1,
    )
    original_attempts = _batch_attempts(original)
    original_attempts[0] = original_attempts[0].model_copy(
        update={
            "instructed_call_limit": 30,
            "hard_call_limit": 30,
            "deadline_seconds": 180.0,
            "self_scores": original_attempts[0].self_scores.model_copy(
                update=dict.fromkeys(ASPECTS)
            ),
            "judge_scores": original_attempts[0].judge_scores.model_copy(
                update=dict.fromkeys(ASPECTS)
            ),
        }
    )
    original_result = evaluate_batch(original, original_attempts)
    assert "frozen18_not_ux_acceptance" in original_result.failures
    assert "missing_aspect_coverage" not in original_result.failures
    assert "acceptance_consumer_set_mismatch" not in original_result.failures
    assert "acceptance_parallelism_mismatch" not in original_result.failures


@pytest.mark.parametrize("phase", ["development", "validation"])
@pytest.mark.parametrize(
    ("consumers", "parallelism", "failure"),
    [
        (("opus",), 4, "acceptance_consumer_set_mismatch"),
        (("opus", "terra"), 1, "acceptance_parallelism_mismatch"),
    ],
)
def test_standalone_acceptance_batch_rejects_underspecified_execution_shape(
    phase: str,
    consumers: tuple[str, ...],
    parallelism: int,
    failure: str,
) -> None:
    """A standalone development or validation gate must enforce the acceptance shape."""
    declaration = _batch(
        phase=phase,
        tasks=_tasks(12, heldout=5) if phase == "validation" else _tasks(12),
        consumers=consumers,
        parallelism=parallelism,
    )
    result = evaluate_batch(declaration, _batch_attempts(declaration))
    assert not result.passed
    assert failure in result.failures


def test_full_campaign_rejects_declared_and_observed_terra_low_effort() -> None:
    """A complete campaign must not pass when every Terra attempt consistently uses low."""
    declaration = _campaign(terra_effort="low")
    attempts = _campaign_attempts(declaration)
    result = evaluate_campaign(declaration, attempts)
    assert len(attempts) == 126
    assert not result.passed
    assert "terra_effort_declaration_mismatch" in result.failures
    assert "terra_effort_not_high" in result.failures


def test_complete_positive_campaign_requires_every_fresh_validation_batch() -> None:
    """All 12-by-2 development and 17-by-2-by-3 validation attempts must pass once."""
    declaration = _campaign()
    attempts = _campaign_attempts(declaration)
    result = evaluate_campaign(declaration, attempts)
    assert len(attempts) == 126
    assert result.passed
    assert result.per_consumer["opus"].self_coverage.speed == 63
    assert result.per_consumer["terra"].judge_minima.speed == 93


def test_campaign_fails_for_one_failed_validation_attempt_or_unaccounted_attempt() -> None:
    """One failed repetition or missing attempt must fail without best-of-three selection."""
    declaration = _campaign()
    attempts = _campaign_attempts(declaration)
    failed = attempts[-1].model_copy(update={"trace_complete": False})
    failed_result = evaluate_campaign(declaration, [*attempts[:-1], failed])
    assert not failed_result.passed
    assert "trace_incomplete" in failed_result.failures
    assert "validation_batch_failed" in failed_result.failures

    missing_result = evaluate_campaign(declaration, attempts[:-1])
    assert not missing_result.passed
    assert "campaign_missing_attempt" in missing_result.failures
