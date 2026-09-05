# Benchmark harness implementation report

## Scope and provenance

- Implemented `scripts/benchmark_agent.py` and `tests/unit/test_benchmark_agent.py` only.
- Task began from source commit `a5cb6f4`; the shared tree advanced through the root-owned docs commit `0db98fd` while implementation was active. No installer, MCP, release, evaluation prompt, or benchmark protocol file was edited.
- This is the reusable one-prompt trace-capture foundation. It does not execute or score the frozen 18-case acceptance suite.

## Contract implemented

- Accepts an existing canonical loopback HTTP MCP URL, a regular prompt file, an exclusive new output directory, model (`opus` by default), deadline, tool-call ceiling, and aggregate stdout/stderr byte ceiling.
- Invokes the resolved `claude` executable once, with no retry or fallback model: print/verbose stream JSON, strict single-server MCP configuration, empty built-in tools, only `mcp__clinpgx__*` allowed, restricted mode, empty setting sources, explicit hook-disabled/plugin-empty settings, disabled skills, no browser integration, no permission prompts, and no session persistence. The working directory and invocation-owned configuration are private temporary files.
- Does not use `--safe-mode`: a root live probe established that this Claude build suppresses even the explicit MCP configuration under safe mode. The retained isolation flags are the combination already shown to keep the mounted server available.
- Streams stdout JSONL and stderr incrementally to exclusive mode-0600 files inside a mode-0700 directory. Total retained stream bytes cannot exceed the configured ceiling. Deadline, excess tool calls, or excess bytes terminate the process group with bounded TERM/KILL escalation. Capture exceptions also kill the group and reap the direct process.
- Writes a canonical mode-0600 `summary.json` after a complete capture. It records prompt SHA-256/size, Git SHA, requested and resolved model, actual init tools/server state, runner and reported durations, exit and termination state, distinct token/cache counters, total and per-model cost facts, tool calls/results/errors, stdout/stderr hashes and sizes, and the final response.
- Trace-valid acceptance is fail-closed for timeout, byte truncation, tool-call limit, nonzero exit, malformed/pathologically nested JSONL, duplicate init/result/call/result records, missing result event/final response, unmatched tool calls, out-of-allowlist init or observed calls, wrong/disconnected MCP server, and model-family substitution. `acceptance_scope` is fixed to `transport_and_trace_integrity_only`; task/rubric scoring remains separate.
- Terminal output is one compact status/summary-path line. Complete traces and final response are not echoed.

## TDD evidence

1. Initial 11-test contract was added before the script. RED: 6 expected failures because `scripts/benchmark_agent.py` did not exist; 5 input-validation cases already terminated at the missing command boundary.
2. Minimal runner implementation reached 11 passing tests.
3. The isolation regression test was changed to reject `--safe-mode` and require explicit hook-disabled settings. RED: the success case became invalid because the stub rejected the old launch; GREEN after changing the command.
4. Integrity tests were added for unmatched/out-of-allowlist calls, balanced pathological JSON recursion, explicit acceptance scope, and process-group cleanup after simulated artifact failure. RED: 5 expected failures exposed each missing behavior; GREEN after bounded parser and lifecycle changes.
5. A successful result event without its final-response field was added. RED: the runner returned success; GREEN after fail-closed validation.

## Focused verification

- `uv run --frozen ruff format scripts/benchmark_agent.py tests/unit/test_benchmark_agent.py`
- `uv run --frozen ruff check scripts/benchmark_agent.py tests/unit/test_benchmark_agent.py` — passed.
- `uv run --frozen pytest -q tests/unit/test_benchmark_agent.py` — `16 passed in 0.71s`.
- `git diff --check` — passed.
- Runner nonblank/noncomment count: 502, below the repository limit of 600.

## Live-run boundary and concerns

- Root started Opus round 1 after the initial 11-test GREEN and before the later integrity increment loaded. That run must be independently audited and must not be described as having used the later 16-test version.
- Stream byte accounting is intentionally aggregate across stdout and stderr. On overflow, retained artifacts stop exactly at the configured ceiling, `trace_truncated` is true, the group is terminated, and trace-valid acceptance is false.
- The harness preserves Claude's raw per-model usage object in addition to separately projected top-level token/cache/cost fields. Those provider counters are observations and are not treated as mutually exclusive prompt-content totals.
