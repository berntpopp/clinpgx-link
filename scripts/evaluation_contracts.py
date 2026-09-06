"""Strict normalized evidence contracts for deterministic evaluation gates."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ASPECTS = (
    "correctness_confidence",
    "completeness",
    "discoverability",
    "token_efficiency",
    "speed",
    "error_recovery",
    "provenance_clarity",
    "overall_usability",
)

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")]
BoundedString = Annotated[str, Field(min_length=1, max_length=200)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
StrictPositiveInt = Annotated[int, Field(strict=True, gt=0)]
StrictScore = Annotated[int, Field(strict=True, ge=0, le=100)]
FiniteNonNegativeFloat = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
FinitePositiveFloat = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
Suite = Literal["exploratory", "frozen12", "heldout", "frozen18"]
Consumer = Literal["opus", "terra"]
Phase = Literal["exploratory", "development", "validation"]
EffortUnavailableReason = Literal[
    "not_configured", "not_exposed_by_client", "not_configured_or_exposed"
]


class StrictModel(BaseModel):
    """Immutable contract base which refuses unknown or coerced fields."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class AspectScores(StrictModel):
    """Raw subjective values; null means the aspect was not observed."""

    correctness_confidence: StrictScore | None
    completeness: StrictScore | None
    discoverability: StrictScore | None
    token_efficiency: StrictScore | None
    speed: StrictScore | None
    error_recovery: StrictScore | None
    provenance_clarity: StrictScore | None
    overall_usability: StrictScore | None


class AspectCoverage(StrictModel):
    correctness_confidence: StrictNonNegativeInt
    completeness: StrictNonNegativeInt
    discoverability: StrictNonNegativeInt
    token_efficiency: StrictNonNegativeInt
    speed: StrictNonNegativeInt
    error_recovery: StrictNonNegativeInt
    provenance_clarity: StrictNonNegativeInt
    overall_usability: StrictNonNegativeInt


class Measurements(StrictModel):
    """Optional bounded telemetry, with unavailable values retained as null."""

    input_tokens: StrictNonNegativeInt | None = None
    output_tokens: StrictNonNegativeInt | None = None
    cache_read_input_tokens: StrictNonNegativeInt | None = None
    cache_creation_input_tokens: StrictNonNegativeInt | None = None
    total_cost_usd: FiniteNonNegativeFloat | None = None
    returned_text_bytes: StrictNonNegativeInt | None = None


class AttemptEvidence(StrictModel):
    """Normalized evidence produced by external runner/checker/judge stages."""

    schema_version: Literal[1] = 1
    attempt_id: Identifier
    campaign_id: Identifier
    batch_id: Identifier
    task_id: Identifier
    suite: Suite
    consumer: Consumer
    candidate_sha: GitSha
    snapshot_sha256: Sha256
    config_sha256: Sha256
    prompt_sha256: Sha256
    trace_sha256: Sha256 | None
    source_assertions_sha256: Sha256 | None
    judge_view_sha256: Sha256 | None
    judge_report_sha256: Sha256 | None
    requested_model: BoundedString
    expected_model: BoundedString
    observed_model: BoundedString
    requested_effort: BoundedString | None
    observed_effort: BoundedString | None
    effort_unavailable_reason: EffortUnavailableReason | None
    model_rerouted: bool
    transport_passed: bool
    trace_complete: bool
    instructed_call_limit: StrictPositiveInt
    hard_call_limit: StrictPositiveInt
    actual_calls: StrictNonNegativeInt
    deadline_seconds: FinitePositiveFloat
    duration_seconds: FiniteNonNegativeFloat
    trace_limit_bytes: StrictPositiveInt
    trace_bytes: StrictNonNegativeInt
    source_assertions_passed: bool | None
    judge_blinding_verified: bool | None
    admission_capacity_errors: StrictNonNegativeInt
    self_scores: AspectScores
    judge_scores: AspectScores
    judge_model: BoundedString | None
    judge_effort: BoundedString | None
    rubric_sha256: Sha256 | None
    measurements: Measurements | None = None

    @model_validator(mode="after")
    def effort_availability_is_explicit(self) -> AttemptEvidence:
        unavailable = self.requested_effort is None or self.observed_effort is None
        if unavailable != (self.effort_unavailable_reason is not None):
            raise ValueError("effort availability reason does not match evidence")
        return self


class ExpectedTask(StrictModel):
    suite: Suite
    prompt_sha256: Sha256


class BatchDeclaration(StrictModel):
    """Controller-issued identities and exact task/consumer product for one batch."""

    schema_version: Literal[1] = 1
    campaign_id: Identifier
    batch_id: Identifier
    phase: Phase
    required_consumers: Annotated[tuple[Consumer, ...], Field(min_length=1, max_length=2)]
    expected_tasks: Annotated[dict[Identifier, ExpectedTask], Field(min_length=1)]
    candidate_sha: GitSha
    snapshot_sha256: Sha256
    config_sha256_by_consumer: dict[Consumer, Sha256]
    model_by_consumer: dict[Consumer, BoundedString]
    requested_model_by_consumer: dict[Consumer, BoundedString]
    effort_by_consumer: dict[Consumer, BoundedString | None]
    judge_model: BoundedString
    judge_effort: BoundedString
    rubric_sha256: Sha256
    parallelism: StrictPositiveInt

    @model_validator(mode="after")
    def consumer_maps_are_exact(self) -> BatchDeclaration:
        required = set(self.required_consumers)
        if len(required) != len(self.required_consumers):
            raise ValueError("required consumers must be unique")
        maps = (
            self.config_sha256_by_consumer,
            self.model_by_consumer,
            self.requested_model_by_consumer,
            self.effort_by_consumer,
        )
        if any(set(mapping) != required for mapping in maps):
            raise ValueError("consumer pin maps must exactly match required consumers")
        return self


class CampaignDeclaration(StrictModel):
    """One development batch plus three fresh, identity-frozen validation batches."""

    schema_version: Literal[1] = 1
    campaign_id: Identifier
    development: BatchDeclaration
    validation: Annotated[tuple[BatchDeclaration, ...], Field(min_length=3, max_length=3)]

    @model_validator(mode="after")
    def ux_campaign_is_complete_and_frozen(self) -> CampaignDeclaration:
        development = self.development
        batches = (development, *self.validation)
        if development.phase != "development" or any(
            batch.phase != "validation" for batch in self.validation
        ):
            raise ValueError("campaign batch phases are invalid")
        if any(batch.campaign_id != self.campaign_id for batch in batches):
            raise ValueError("campaign identity does not match every batch")
        if len({batch.batch_id for batch in batches}) != len(batches):
            raise ValueError("campaign batch identities must be distinct")
        if len(development.expected_tasks) != 12 or any(
            task.suite != "frozen12" for task in development.expected_tasks.values()
        ):
            raise ValueError("development must contain exactly twelve frozen tasks")
        validation_tasks = self.validation[0].expected_tasks
        if any(batch.expected_tasks != validation_tasks for batch in self.validation[1:]):
            raise ValueError("validation task declarations must be identical")
        for task_id, expected in development.expected_tasks.items():
            if validation_tasks.get(task_id) != expected:
                raise ValueError("validation must retain every original frozen task")
        extra_tasks = {
            task_id: expected
            for task_id, expected in validation_tasks.items()
            if task_id not in development.expected_tasks
        }
        if len(extra_tasks) < 5 or any(
            expected.suite != "heldout" for expected in extra_tasks.values()
        ):
            raise ValueError("validation must add at least five held-out tasks")
        shared_fields = (
            "candidate_sha",
            "snapshot_sha256",
            "config_sha256_by_consumer",
            "model_by_consumer",
            "requested_model_by_consumer",
            "effort_by_consumer",
            "judge_model",
            "judge_effort",
            "rubric_sha256",
        )
        if any(
            getattr(batch, field) != getattr(development, field)
            for batch in self.validation
            for field in shared_fields
        ):
            raise ValueError("campaign evidence identities must remain frozen")
        return self


class ConsumerStatistics(StrictModel):
    self_minima: AspectScores
    judge_minima: AspectScores
    self_coverage: AspectCoverage
    judge_coverage: AspectCoverage


class GateResult(StrictModel):
    """Deterministic result over declared, normalized—but externally trusted—evidence."""

    passed: bool
    scope: Literal["deterministic_declared_evidence_gate"] = "deterministic_declared_evidence_gate"
    failures: tuple[BoundedString, ...]
    limitations: tuple[BoundedString, ...]
    per_consumer: dict[Consumer, ConsumerStatistics]
    inter_rater_differences: dict[Identifier, AspectScores]


def canonical_bytes(value: object) -> bytes:
    """Serialize finite JSON deterministically without a trailing newline."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def estimated_tokens(value: object) -> int:
    """Return the protocol's deterministic four-serialized-bytes estimate."""
    return (len(canonical_bytes(value)) + 3) // 4


__all__ = [
    "ASPECTS",
    "AspectCoverage",
    "AspectScores",
    "AttemptEvidence",
    "BatchDeclaration",
    "CampaignDeclaration",
    "Consumer",
    "ConsumerStatistics",
    "ExpectedTask",
    "GateResult",
    "Measurements",
    "canonical_bytes",
    "estimated_tokens",
]
