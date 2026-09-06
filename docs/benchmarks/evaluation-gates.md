# Deterministic evaluation evidence gates

`scripts/evaluation_gates.py` evaluates normalized, declared evidence. It does not
run a model or campaign, parse raw traces, execute factual source assertions, or
verify judge blinding. Separate adapters, source-checkers, and blinded-judge stages
must produce those facts and hashes. A passing `GateResult` therefore means only
that the supplied normalized evidence satisfies the declared deterministic gate;
the result always reports
`normalized_evidence_truth_not_independently_verified` as a limitation.

Raw questions, traces, answer assertions, judgments, authentication data, and the
private rubric do not belong in these contracts. Campaign orchestration remains
responsible for the two-requests-per-second outbound limit. A runner's transport
`accepted` flag alone is never campaign acceptance.

## Python API

The supported entry points are:

```python
from pathlib import Path
from collections.abc import Sequence

from scripts.evaluation_contracts import (
    ASPECTS,
    AttemptEvidence,
    BatchDeclaration,
    CampaignDeclaration,
    GateResult,
    canonical_bytes,
    estimated_tokens,
)
from scripts.evaluation_gates import evaluate_attempt, evaluate_batch, evaluate_campaign
from scripts.evaluation_history import append_attempt, read_attempts

def evaluate_attempt(evidence: AttemptEvidence) -> GateResult: ...
def evaluate_batch(
    declaration: BatchDeclaration,
    attempts: Sequence[AttemptEvidence],
) -> GateResult: ...
def evaluate_campaign(
    declaration: CampaignDeclaration,
    attempts: Sequence[AttemptEvidence],
) -> GateResult: ...
def append_attempt(directory: Path, evidence: AttemptEvidence) -> Path: ...
def read_attempts(directory: Path) -> tuple[AttemptEvidence, ...]: ...
def canonical_bytes(value: object) -> bytes: ...
def estimated_tokens(value: object) -> int: ...
```

All models are frozen strict Pydantic models with unknown fields forbidden.
Malformed contracts raise Pydantic `ValidationError`; valid nonpassing evidence
returns `GateResult(passed=False, ...)`. Numeric booleans and nonfinite values are
malformed. `canonical_bytes` emits finite UTF-8 JSON with sorted keys,
`ensure_ascii=False`, separators `(",", ":")`, and no newline. The token estimate
is `ceil(len(canonical_bytes(value)) / 4)`.

`ASPECTS` is exactly:

```text
correctness_confidence, completeness, discoverability, token_efficiency,
speed, error_recovery, provenance_clarity, overall_usability
```

## Input contracts

Every identifier is 1–80 ASCII characters from `A-Za-z0-9_.-`. Git candidate
identities are 40 lowercase hexadecimal characters; every SHA-256 identity is 64
lowercase hexadecimal characters. Bounded identity strings are 1–200 characters.
All mappings reject extra keys through their typed enclosing models.

`AttemptEvidence` has these exact fields:

| Field | Contract |
| --- | --- |
| `schema_version` | literal `1` |
| `attempt_id`, `campaign_id`, `batch_id`, `task_id` | identifiers |
| `suite` | `exploratory`, `frozen12`, `heldout`, or `frozen18` |
| `consumer` | `opus` or `terra` |
| `candidate_sha` | Git identity |
| `snapshot_sha256`, `config_sha256`, `prompt_sha256` | required SHA-256 identities |
| `trace_sha256`, `source_assertions_sha256`, `judge_view_sha256`, `judge_report_sha256` | SHA-256 identity or `null`; null is nonpassing |
| `requested_model`, `expected_model`, `observed_model` | bounded strings; only expected versus observed is compared at attempt scope |
| `requested_effort`, `observed_effort` | bounded string or `null` |
| `effort_unavailable_reason` | `not_configured`, `not_exposed_by_client`, `not_configured_or_exposed`, or `null`; required iff either effort is null |
| `model_rerouted`, `transport_passed`, `trace_complete` | strict booleans |
| `instructed_call_limit`, `hard_call_limit`, `trace_limit_bytes` | strict positive integers |
| `actual_calls`, `trace_bytes`, `admission_capacity_errors` | strict nonnegative integers |
| `deadline_seconds` | finite positive float |
| `duration_seconds` | finite nonnegative float |
| `source_assertions_passed`, `judge_blinding_verified` | strict boolean or `null` |
| `self_scores`, `judge_scores` | all eight exact aspect keys, each strict integer 0–100 or `null` |
| `judge_model`, `judge_effort` | bounded string or `null`; null is nonpassing |
| `rubric_sha256` | SHA-256 identity or `null`; null is nonpassing |
| `measurements` | closed object or `null` |

When present, `measurements` contains only `input_tokens`, `output_tokens`,
`cache_read_input_tokens`, `cache_creation_input_tokens`, `total_cost_usd`, and
`returned_text_bytes`. Counts are nonnegative integers or null; cost is a finite
nonnegative float or null. Missing measurements remain null.

`BatchDeclaration` has `schema_version`, `campaign_id`, `batch_id`, `phase`,
`required_consumers`, `expected_tasks`, `candidate_sha`, `snapshot_sha256`,
`config_sha256_by_consumer`, `model_by_consumer`,
`requested_model_by_consumer`, `effort_by_consumer`, `judge_model`,
`judge_effort`, `rubric_sha256`, and `parallelism`. Phase is `exploratory`,
`development`, or `validation`. Consumers are a unique nonempty tuple. Every
consumer pin map has exactly those keys. Each expected-task value contains only
`suite` and `prompt_sha256`.

`CampaignDeclaration` has `schema_version`, `campaign_id`, `development`, and
`validation`. Development is exactly 12 frozen12 tasks. Validation is exactly
three distinct validation batches with identical task declarations: the original
12 tasks with unchanged prompt hashes plus at least five held-out tasks. Candidate,
snapshot, consumer/config/requested-model/effective-model/effort, judge, and rubric
pins are identical in all four batches. Every acceptance batch declares both
consumers and parallelism 4.

Consumer count, parallelism, and Terra-high are acceptance conditions rather than
input-shape conditions: a structurally valid nonpassing declaration remains
recordable and produces stable gate failures. The same batch check is used for a
standalone development/validation result and for every batch within a campaign.

The historical Claude representation is valid without relabeling:

```python
assert evidence.requested_model == "opus"          # actual CLI value
assert evidence.expected_model == "claude-opus-5"  # declared exact identity
assert evidence.observed_model == "claude-opus-5"  # adapter observation
assert evidence.requested_effort is None
assert evidence.observed_effort is None
assert evidence.effort_unavailable_reason == "not_configured_or_exposed"
```

Terra UX declarations instead pin requested/effective model `gpt-5.6-terra` and
requested/observed effort `high`. A lower or unavailable Terra effort is
nonpassing. Known differing efforts fail. Unknown Opus effort is retained as a
limitation and never fabricated.

## Output contract and gates

`GateResult` contains exactly `passed`, scope
`deterministic_declared_evidence_gate`, `failures`, `limitations`,
`per_consumer`, and `inter_rater_differences`. `per_consumer` always has `opus`
and `terra`. Each contains `self_minima`, `judge_minima`, `self_coverage`, and
`judge_coverage`, each with all eight aspects. Minima remain null when coverage is
zero. Differences are keyed by attempt ID and contain the nullable absolute
self-versus-judge difference for every aspect. Scores are never averaged.

Attempt gates enforce exact suite limits, expected/observed model identity, known
effort identity, no reroute, passing transport, complete trace, passing source
assertions and judge blinding, all stage digests and judge/rubric identities,
strictly-above-90 observed scores, both call ceilings, deadline, trace size, and
zero admission-capacity errors. Null scores alone do not fail an attempt.

Frozen12 and held-out attempts require 35 instructed calls, 40 hard calls, 600.0
seconds, and 33,554,432 trace bytes. Frozen18 requires 30/30 calls, 180.0 seconds,
and the same trace limit. Exploratory limits are caller-declared positive values.
Exploratory and frozen18 results never pass this UX gate.

Batch gates require the exact task-by-consumer product, unique attempt IDs and
unique batch/task/consumer identities, every declaration pin, every attempt gate,
and at least one observed score for every aspect in each self/judge series for
each required consumer. Inputs are never filtered or best-selected. Campaign gates
require the development batch and all three fresh validations to pass, with every
attempt accounted for exactly once.

Stable failure codes are:

```text
acceptance_consumer_set_mismatch, acceptance_parallelism_mismatch,
admission_capacity_error, attempt_failed, batch_identity_mismatch,
campaign_extra_attempt, campaign_identity_mismatch, campaign_missing_attempt,
candidate_identity_mismatch, config_identity_mismatch, consumer_identity_mismatch,
deadline_exceeded, development_batch_failed, duplicate_attempt_id,
duplicate_task_consumer, effort_identity_mismatch, exploratory_not_ux_acceptance,
extra_attempt, frozen18_not_ux_acceptance, hard_call_limit_exceeded,
instructed_call_limit_exceeded, judge_blinding_failed, judge_blinding_unavailable,
judge_effort_identity_mismatch, judge_identity_missing,
judge_model_identity_mismatch, judge_report_digest_missing,
judge_view_digest_missing, known_effort_mismatch, missing_aspect_coverage,
missing_attempt, model_identity_mismatch, model_rerouted, observed_model_mismatch,
prompt_identity_mismatch, requested_model_identity_mismatch,
rubric_digest_missing, rubric_identity_mismatch, score_not_above_90,
snapshot_identity_mismatch, source_assertions_digest_missing,
source_assertions_failed, source_assertions_unavailable, suite_identity_mismatch,
suite_limit_mismatch, task_identity_mismatch, terra_effort_declaration_mismatch,
terra_effort_not_high, trace_digest_missing,
trace_incomplete, trace_limit_exceeded, transport_failed, validation_batch_failed
```

Results may additionally report stable limitations
`normalized_evidence_truth_not_independently_verified`, `effort_not_configured`,
`effort_not_exposed_by_client`, or `effort_not_configured_or_exposed`.

## Append-only private history

`append_attempt` requires an absolute, explicit path other than HOME or the
repository root. It exclusively creates a missing directory as mode 0700 or
validates an existing owned, non-symlink mode-0700 directory. It writes exactly
`<attempt_id>.json` once using `O_EXCL`/`O_NOFOLLOW`, mode 0600, canonical JSON,
flush, and `fsync`. A prior passing or failed attempt is never overwritten.

`read_attempts` returns artifacts in attempt-ID order. It rejects malformed or
noncanonical JSON, duplicate keys/identities, symlinks, hard links, unsafe modes,
invalid names, payload/name mismatches, artifacts over 1 MiB, and histories over
10,000 artifacts. The history stores normalized evidence only, never raw prompts,
traces, credentials, or externally fetched data.

```python
private_history = Path("/explicit/private/evaluation-attempts")
stored_path = append_attempt(private_history, evidence)
assert stored_path.name == f"{evidence.attempt_id}.json"
assert read_attempts(private_history) == (evidence,)

attempt_result = evaluate_attempt(evidence)
serialized = canonical_bytes(attempt_result)
token_estimate = estimated_tokens(attempt_result)
```

The original frozen18 acceptance suite remains a separate factual whole-project
gate. This module validates its declared attempt limits if such evidence is
consumed, but refuses to count frozen18 tasks toward UX acceptance and does not
require absent self/judge UX ratings for them. A UX campaign pass does not satisfy
the original frozen18 gate or the remaining release, container, and coverage gates.
