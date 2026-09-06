# Retained evaluation trace normalization

`scripts/evaluation_traces.py` converts one already-retained Claude/Opus or
Codex/Terra runner directory into trace-derived `NormalizedRun` evidence. It does
not launch a model, contact a server, read authentication or global configuration,
execute source assertions, extract self-scores, or invoke a judge.

## Interface and artifact pins

```python
from pathlib import Path

from scripts.evaluation_traces import RunExpectation, normalize_run

normalized = normalize_run(
    Path("/absolute/private/run"),
    expected=RunExpectation(...),
)
```

The caller supplies `RunExpectation` from independent retained invocation
evidence. In particular, the expected summary and trace SHA-256 values must not be
computed from the same untrusted summary being checked. Claude directories contain
`stdout.jsonl` and `summary.json`; Terra directories contain `trace.jsonl` and
`summary.json`.

The run directory must be an explicit absolute, owned, mode-0700 directory without
symlink traversal. Each artifact is opened relative to that directory with
`O_NOFOLLOW`, must be an owned mode-0600 regular file with one link, and is read
once through its validated descriptor. Summaries are limited to 16 MiB and traces
to the independently declared limit, at most 32 MiB. Hash, unsafe-I/O, and malformed
artifact errors use only the fixed `AdapterInputError.reason` values
`hash_mismatch`, `unsafe_io`, and `malformed_artifact`; paths, source bodies, and
underlying exception strings are not exposed.

## What the adapters prove

Both adapters reject duplicate JSON keys, nonfinite JSON, invalid UTF-8,
non-object protocol records, and untrusted summaries that claim calls, answers,
identity, usage, digests, sizes, or lifecycle facts absent from the trace. A
partial JSONL tail makes capture incomplete and nonpassing while preserving calls
from preceding complete records. Well-formed failed or timed-out runs remain
normalizable with their partial calls, null final answer, nullable observed model,
and stable failure codes.

Claude and Terra expose different evidence and remain different in the normalized
result:

- Claude yields one decoded application envelope from each `tool_result`. The
  second MCP wire copy is not visible, so `wire_mirror` is `not_observed` and the
  run records `claude_wire_mirror_not_observed`; boundary mirror CI is the separate
  mirror proof. The retained CLI alias (normally `opus`) and observed exact model
  remain distinct. The current runner neither configures nor exposes reasoning
  effort, so both effort fields are null with
  `not_configured_or_exposed`. Runner monotonic duration is disclosed as such.
- Terra replays server notifications through the existing `ProtocolTrace` using
  runner-retained preflight tool schemas and identity. It independently validates
  each runtime schema digest, turn request model/effort/prompt, response IDs,
  thread/turn/item lifecycle, arguments, typed errors, reroutes, and both MCP wire
  copies using finite, type-sensitive canonical JSON. Wrapper elapsed times are
  preserved as client timestamps; they are not required to equal the runner
  summary's separately sampled timestamps. Preflight is runner-observed evidence,
  not independent backend attestation.

Application envelopes are retained exactly as decoded, including external-string
fences, source provenance, continuations, error recovery fields, and raw digests.
The adapters do not unfence, sanitize, select, or duplicate source content.
`returned_text_bytes` counts observed UTF-8 `TextContent` bytes once per call, not
both Terra mirror copies or a pretty-print. Missing counters, timing, cost, and
usage stay null rather than zero. Client-reported usage distinctions that do not
map unambiguously to `Measurements` remain in finite `raw_usage`.
Claude summary comparison uses the runner's exact four-counter usage projection;
additional raw terminal usage fields remain in `raw_usage` without invalidating
that projection. Runner duration is a required observation in `NormalizedRun`:
missing, nonfinite, negative, boolean, or wrongly typed duration is a malformed
artifact, while an observed numeric zero remains zero.

`final_answer` is the raw private consumer prose, including any self-review. It is
not a blinded judge view and must never be sent to a judge as-is.

## Later evidence assembly

Normalization is only the trace stage. A later controller must bind its output to
independently generated source-assertion, self-score, blinded-judge-view, and judge
report artifacts before constructing `AttemptEvidence`. The mapping is direct for
consumer/candidate/prompt/trace identities, model and effort observations,
transport and trace completeness, limits, call count, duration, and measurements.
The assembly stage—not this adapter—supplies snapshot/config identities,
instructed call limits, source-assertion results, self/judge scores, judge identity,
rubric identity, and blinding evidence. Until those stages exist, deterministic
evaluation gates correctly remain nonpassing.
