# Cross-model mounted MCP harness

The cross-model evaluation has separate Claude Code Opus and Codex
`gpt-5.6-terra`/`high` consumer tracks. Both tracks use the same frozen task text,
candidate revision, source configuration, rubric, and task budgets. Results are
reported separately: an average cannot hide an observed aspect at or below 90.
The existing Opus self-review and one blinded Fable 5.1 judgment per trace remain
required; any additional Terra judgments are also reported separately. This runner
does not aggregate or judge results and does not replace the frozen twelve-task,
held-out, or eighteen-case acceptance suites.

Run at most four consumers concurrently at first and record queue time separately
from execution time. Every passing candidate/model track still requires its three
declared fresh validation batches; there is no best-of repetition, null-as-pass, or
raw-score override. Each covered aspect must be strictly greater than 90 in every
required judgment, and deterministic source assertions pass independently. The
private held-out bodies and frozen prompts are never exposed to implementation work.

## Terra runner

`scripts/benchmark_codex.py` runs one prompt through one ephemeral Codex app-server
thread and one turn. It pins the request to `gpt-5.6-terra`, provider `openai`, and
reasoning effort `high`; these are not command-line choices. Start the exact candidate
HTTP MCP separately, create a private parent directory, and give each attempt a new,
nonexistent output path:

```console
mkdir -m 700 .benchmarks
uv run --frozen python scripts/benchmark_codex.py \
  --mcp-url http://127.0.0.1:18765/mcp \
  --prompt-file /private/frozen-task.md \
  --output-dir .benchmarks/terra-task-01-attempt-001 \
  --deadline-seconds 600 \
  --max-tool-calls 40 \
  --max-trace-bytes 33554432
```

The instructed 35-call task budget still applies when the 40-call hard safeguard is
used. Use the appropriate unchanged limits for other frozen suites. The runner never
retries or starts a second turn; retain every accepted, failed, timed-out, or rejected
attempt.

## Platform and isolation

The adapter currently supports Linux with procfs, unprivileged user/PID namespaces,
Bubblewrap, and an installed Codex CLI whose native executable and code-mode host can
be resolved unambiguously. `CODEX_HOME` must be unset. `HOME` stays at its original
path, and the existing `.codex/auth.json` must be a regular non-symlink file. The
runner does not read, copy, print, or rewrite credential bytes: it masks the user
Codex state and exposes that one file read-only at the same path.

The namespace has an empty private working directory, a read-only root view, masked
user/project skill state, a cleared environment, and only one required loopback
ClinPGx MCP. Apps, plugins, hooks, shell, web, image viewing, and agents are disabled.
Before `turn/start`, the runner verifies the effective config layers, hooks, local
plugins, skills, instruction sources, connected MCP inventory, tool schemas, and
exact executable paths of descendants. The intrinsic client system skills are pinned
and disclosed in the private summary with the observed client version; they are client
inputs, not claimed absent. Any changed inventory or unexpected descendant rejects the
attempt before or during the turn. See the official [Codex app-server
documentation](https://learn.chatgpt.com/docs/app-server) for the initialize,
thread, turn, and item lifecycle.

## Identity and acceptance boundary

The summary records requested identity separately from app-server-observed effective
session `model`, `modelProvider`, and `reasoningEffort`. Missing or mismatched values,
nonempty `instructionSources`, or `model/rerouted` fail closed. This is client identity,
not upstream response/backend attestation; backend identity is recorded as unavailable
with a reason.

Transport acceptance requires a completed turn with at least one schema-valid,
paired ClinPGx MCP call and a nonempty `final_answer` after all calls complete. It
rejects incomplete, duplicate, reordered, mismatched, failed-transport, over-cap, or
non-ClinPGx/built-in tool lifecycles. A structured MCP error using one of the six public
codes is a valid observation, so a recovery task can continue to another call. It is
not converted into transport failure or hidden from the error ledger.

Transport acceptance does **not** establish factual correctness, source-assertion
success, UX score, or campaign acceptance. Those require the separate answer checks,
blind judgments, per-model gates, and frozen-suite rules.

## Private artifacts and unavailable measurements

The output directory is exclusively created as mode `0700`; `trace.jsonl` and
`summary.json` are exclusively created as mode `0600`, without following output-file
symlinks or overwriting prior attempts. The bounded private trace includes the prompt
inside the sent `turn/start`, model messages, MCP arguments/results, and final answer.
Never publish it or the private prompt body. The summary retains prompt/trace hashes,
revision, requested and observed identities, runtime tool schemas and digests, calls,
typed errors, final output, lifecycle timing, usage, isolation evidence, and rejection
reasons.

Client-observed wall and item-lifecycle durations are distinct from server-reported
tool timing. Scheduler queue time, backend identity, cost, or token usage absent from
app-server are represented as `null` plus an explicit reason, never as measured zero.
App-server stderr text is discarded; only bounded byte/chunk counts are retained.
