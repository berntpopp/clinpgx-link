# Mounted Opus MCP user-experience evaluation

Requested 2026-09-05: multiple adversarial Claude Code rounds using Opus with this
MCP mounted, followed by implementation fixes and fresh evaluation. The user
subsequently raised the target to strictly greater than 90/100 in every category
and requested at least ten diverse tasks in parallel. Preserve the original
baseline scores; do not retrospectively relabel its target or outcome.

## Method

Use isolated Claude Code sessions with only the loopback ClinPGx HTTP MCP, no
built-in tools, unrelated servers, repository access, skills, plugins, or hooks.
Record the actual resolved model rather than assuming the `opus` alias's version.
Do not silently fall back to another model. Preserve all failed attempts.

Freeze each task prompt before running it. The initial task set is
`tests/eval/opus-ux-round1.md`; it includes mounted-state identities and stored row
identifiers that require tool evidence. The evaluator receives neither an answer
key nor the score target. Biomedical questions alone are not claimed to be
impossible to answer through other public sources.

Capture complete bounded raw event traces directly into private run artifacts;
record prompt/code/data identities, tools and arguments/results, wall time,
token/cache counters, cost, errors, termination, and final output. A truncated
trace, missing result, substituted model, unavailable task, or unobserved category
cannot pass. Keep client-reported cache counters distinct from unique input tokens.
Separate subjective speed ratings from measured latency and API waits.

Run at least three evaluation rounds. After the baseline, reproduce meaningful
defects as tests, implement fixes, review them, and rerun affected gates. Use both
repeat tasks for comparison and fresh equivalent tasks to check generalization;
never coach the evaluator toward a desired score or remove failed tasks. Preserve
the original frozen 18-case acceptance manifest separately and unchanged.

For subsequent rounds, run twelve diverse tasks in isolated Opus sessions, with
four sessions concurrently against the same MCP. Keep the existing shared upstream
rate limit. Record queue delay separately from execution duration and label
contention/cold-versus-warm conditions. Also run targeted single-session timing
checks so concurrency is not confused with individual tool latency.

## Scoring and evidence

The eight independently rated categories are correctness confidence, completeness,
discoverability, token efficiency, speed, error recovery, provenance clarity, and
overall usability. Each rating must cite concrete interactions and improvements.
Null/unobserved is incomplete, not 100. Self-ratings supplement independent answer
and trace audits; an unsupported answer cannot pass because its author is confident.

Report every task's observed scores. An aspect untested by one task may be evaluated
by another task, but each aspect needs actual coverage. Use the minimum observed
score per aspect for the batch gate, with independently checked task completion;
an average cannot hide a low-scoring task. Do not tell Opus the target score.

Audit returned identities and exact fields against read-only local source records
or captured upstream responses. Report measured call counts, result sizes, elapsed
time, and token usage beside subjective ratings. Cold/warm cache conditions and
data inventory differences must be visible in comparisons.

These development rounds do not prove hardened-container deployment, authenticated
release installation, complete API/download/website equivalence, or the full
18-case acceptance benchmark. Those remain separate required gates.
