# MCP UX Remediation Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement this plan task-by-task. The user's direction overrides iterative document reviews: one plan review, then implementation.

**Goal:** Repair measured MCP usability defects and evaluate whether every observed aspect exceeds 90/100 without weakening evidence or safety gates.

**Architecture:** Retain the existing repository, source adapters and MCP tools. Shared code-owned contracts govern discovery and recovery; selection precedes shaping; timing and admission live at the MCP boundary. Keep exact retained source bytes independently reachable.

**Tech Stack:** Python 3.12+, uv, SQLite, FastMCP 3.x, MCP SDK 1.x, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-05-mcp-ux-remediation-design.md`; the original source-access contract remains binding.

## Global Constraints

- Preserve read-only research scope; no patient-specific inference or treatment.
- Preserve the six public error codes and exact TextContent/structuredContent mirroring, including errors. Output schemas remain optional.
- Preserve each emitted external-string fence, source provenance and raw digest.
- Preserve the 100,000-byte serialized envelope ceiling, existing content limits, complete source-byte retention, explicit deferral, and usable continuations.
- Keep source selectors explicit; no automatic fallback execution or source switch following an explicit API/download/website request.
- Keep outbound scheduling at two requests per second during evaluation.
- Modules remain below 600 nonblank/noncomment lines. Split touched modules by responsibility where necessary; no unrelated reorganization.
- No tests, frozen prompts, safety limits or thresholds are weakened to pass.
- Use apply_patch, exact-file staging and mandatory hooks. Never stage private traces or `.superpowers` artifacts. Continue on the existing feature branch without relocating ongoing work.

## Execution decisions from the completed spec review

These resolve concrete ambiguities without restarting document review.

- The independent held-out author freezes and hashes at least five new tasks before candidate fixes. Implementers receive no question bodies. The shared filesystem is not a security boundary; this is procedural blinding and must be reported as such. Each validation batch includes the held-out set and its scores in the minimum; any held-out failure fails that batch.
- Frozen Opus prompts and raw scores are unchanged. Their intermediate integer scores are subjective interpolations, not equivalent calibrated measurements of the supplementary judge anchors. Report the two series separately; both must pass. Judge retained baselines with the same frozen rubric before claiming judge-score improvement.
- Keep the approved whole-call container-selection error. It is an explicit scalar-only contract, not a protocol violation. The error provides structure retrieval; measure its usability rather than silently expanding statuses.
- Deadline expiry returns `upstream_unavailable`, subtype `execution_deadline`, with fixed retry/diagnostics guidance. Existing upstream rejection/HTTP throttling, not normal scheduler waiting, produces `rate_limited` / `upstream_throttle`. Any `admission_capacity` result in a normal four-session UX batch fails capacity acceptance.
- Asterisk rejection applies only to token/FTS query arguments, never exact matching or structured filters. Recovery names the current tool's supported exact route.
- Tabular locators are explicitly provenance-only; a member content_ref and valid get_source_content arguments supply the executable path.
- No external admission-report loader is introduced without an authenticated snapshot binding contract. Existing historic rows expose unknown classification and null independent times unless retained evidence itself establishes them; never invent an admission time. New acquisition evidence may classify acquired_at when captured at acquisition.
- All size budgets use finite JSON serialized with Python's pinned runtime `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")`. Non-finite adapter values produce typed data-validation failure, never coercion. Numbers retain Python JSON representation; this is not an RFC canonicalization claim.
- Upstream drift alone permits a new, separately recorded campaign after a versioned assertion correction, without cosmetic code changes. All failed campaigns remain visible.

### Task 1: Correct collection and owning-row provenance (R1)

**Files:** Modify `clinpgx_link/models.py`, `clinpgx_link/data/repository.py`, `clinpgx_link/mcp/dataset_record_tools.py`, `clinpgx_link/mcp/record_shaping.py`; test `tests/unit/test_repository.py`, `tests/unit/test_mcp_dataset_records.py`, `tests/unit/test_mcp_records.py`. Extract `clinpgx_link/mcp/row_provenance.py` if needed for the module limit.

**Interfaces:** Preserve SourceInfo/SourceResponse callers. Add backward-compatible `retrieval_time_kind`, nullable `acquired_at` and `admitted_at` to SourceInfo. `_source(None)` describes the collection; owning-record/member SourceResponse supplies row provenance. Classification defaults to unknown, never inferred from retrieved_at's name.

- [ ] RED: Extend the real SQLite fixtures with genes and pathways archives at different dates. Assert collection URL equals `https://www.clinpgx.org/downloads` regardless of most recent archive, and verify direct/search row fences, deferred references, archive hash and member identity use the owning genes archive. Example assertion:

  ```python
  assert repository.list_datasets().source.url == "https://www.clinpgx.org/downloads"
  assert repository.list_datasets().source.retrieval_time_kind == "unknown"
  ```

- [ ] Run `uv run pytest tests/unit/test_repository.py tests/unit/test_mcp_dataset_records.py tests/unit/test_mcp_records.py -q`; retain the expected provenance failure before implementation.
- [ ] GREEN: Replace arbitrary archive collection identity; explicitly label aggregate snapshot timestamp/digest scope. Shape every row with its own asset SourceInfo, add bounded dataset URL/archive hash/recorded timestamp provenance, keep all source fences and complete member fallback. Never rewrite source captures or invent timestamp evidence.
- [ ] Verify the three focused suites, `uv run ruff check clinpgx_link tests/unit`, `uv run mypy clinpgx_link`, then `make ci-local GENEFOUNDRY_ROUTER_DIR=../genefoundry-router`. Commit only owned files as `fix: preserve owning dataset provenance in MCP results`.
- [ ] Task-scoped independent code review: mixed collection identity, fence source consistency, timestamp evidence and byte-budget regressions.

### Task 2: Share executable search and recovery contracts (R2)

**Files:** Create `clinpgx_link/mcp/search_contracts.py`; modify `clinpgx_link/mcp/record_tools.py`, `clinpgx_link/mcp/recovery.py`, `clinpgx_link/mcp/facade.py`; test `tests/unit/test_mcp_records.py` and new `tests/unit/test_mcp_search_contracts.py`.

**Interfaces:** Code-owned contract entries describe entity, source, allowed selectors, semantics and fixed examples. Capabilities, validation and recovery consume the same entries. Preserve existing tool names and public error codes.

- [ ] RED: Real FastMCP Client + stub API adapter tests replay every published example and invalid batch2 combinations. Assert API gene/chemical use canonical gene/chemical selectors; unsupported filters yield subtype `unsupported_api_filters`; recovery never recommends unfiltered download search. Hostile rejected values must be absent from serialized results.
- [ ] Run `uv run pytest tests/unit/test_mcp_search_contracts.py tests/unit/test_mcp_records.py -q` and capture the expected failure.
- [ ] GREEN: Introduce the shared matrix; route name-based guideline discovery through identifier resolution then get_related_records. Only captured accession parameters with strict PA IDs permit direct shortcuts. Describe local search as membership, not entity-family identity. Keep the full matrix out of tool schemas.
- [ ] Verify published commands through the real client, typing, lint and schema budgets; commit only task files as `fix: unify MCP search discovery and recovery`.
- [ ] Review the changed contract and fixed recovery commands for schema validity, source fidelity and hostile reflection.

### Task 3: Project before shaping and implement truthful profiles/modes (R3–R4)

**Files:** Create `clinpgx_link/mcp/selection.py`, `clinpgx_link/mcp/record_profiles.py`; modify `clinpgx_link/mcp/dataset_record_tools.py`, `clinpgx_link/mcp/dataset_record_fields.py`, `clinpgx_link/mcp/record_tools.py`, `clinpgx_link/mcp/shaping.py`, `clinpgx_link/mcp/facade.py`, existing API/website tool registration modules and cursor selector helpers. Tests: new `tests/unit/test_mcp_selection.py` plus existing dataset, records, data, presenter and content suites.

**Interfaces:** `include_fields` is an optional ordered list of 1–16 unique profiled names <=512 characters on search_dataset/get_dataset_record. `pointers` is an optional ordered list of 1–12 unique RFC6901 pointers <=4096 characters/128 segments on the four specified read tools. Bind selection order into cursors; exclude mode and page size. A reusable selection helper returns code-owned value/absent/deferred entries without authorizing untrusted keys.

- [ ] RED: Real-client tests retrieve two genes fields in one call under 8,000 serialized envelope bytes; five PharmCAT child fields retain numeric allele count 2. Test selected safe fields beside hostile unselected keys, all four modes, explicit selection precedence, duplicates/empty/unknown names, RFC6901 escapes, malformed pointers, absent versus stored null, containers, incompatible arguments, selection-bound cursor and oversized-first/small-later selection.
- [ ] Run `uv run pytest tests/unit/test_mcp_selection.py -q`; record missing-selection failures before code.
- [ ] GREEN: Validate selections before acquisition, resolve all pointers from one retained JSON response, project before SourcePresenter. Containers fail with usable structure retrieval. Preserve normalized and original locators separately, with provenance-only tabular coordinates and executable member fallback. Use per-row 12,000-byte finite-JSON value budget, shaped-page 70,000 bytes, whole envelope 100,000 bytes. Trim page tail with progress cursor; bounded whole-row deferral or typed size error if first row cannot fit.
- [ ] Add code-owned minimal/compact/standard/full shape profiles, documented source-column justification and required/optional fields. Publish profiles in get_dataset. Drift disables only the incompatible profile and explicitly blocks coverage/UX acceptance, not byte access. Every known shape is profiled or explicitly unprofiled.
- [ ] Verify mode field/size differences and invariant matches/counts/limitations; schema estimates <=1,200/tool and <=10,000 aggregate using the agreed serializer. Run focused suites, lint, typing and local CI; commit as `feat: add bounded MCP selections and truthful response profiles`.
- [ ] Review hostile-key safety, original-byte locators, source attribution, cursor progress and projection budgets.

### Task 4: Preserve scientific matching and bounded diagnostics (R5)

**Files:** Modify `clinpgx_link/data/repository.py`, `clinpgx_link/mcp/recovery.py`, dataset/record tool descriptions; create `clinpgx_link/data/search_diagnostics.py` to keep SQL work bounded; tests in `tests/unit/test_repository.py` and new `tests/unit/test_search_diagnostics.py`.

**Interfaces:** Existing exact selectors remain exact. Zero-result diagnostics are supplemental metadata, never altered membership. Use the existing serialized SQLite connection lock; scope and restore progress handlers.

- [ ] RED: Assert free-text `*1/*1`/`CYP2C19*2` reject, exact equivalents remain allowed; Reference/Reference, 5-fluorouracil, N-acetyltransferase, slash combinations and rs IDs remain token searches. DPYD *1/*1 stays exact zero with Reference/Reference only a fenced non-equivalence example. Assert cursor expiry/selector/offset guidance is executable.
- [ ] Run `uv run pytest tests/unit/test_search_diagnostics.py tests/unit/test_repository.py -q` and retain expected failures.
- [ ] GREEN: Diagnose at most first two present filters in id/gene/chemical/variant/name/source/annotation_id order. Under remaining filters return count and at most three distinct stored values in binary lexical index order. Each query gets 50,000 VM steps and 50ms deadline; injected step budget makes tests deterministic. Budget exhaustion returns diagnostics_unavailable, not invented counts; restore progress handler before unrelated queries.
- [ ] Verify deterministic interruption, hostile examples, exact positives and negatives, unrelated-query recovery, lint/typing and local CI. Commit as `fix: preserve exact scientific matching and bounded recovery`.
- [ ] Review SQL bounds, connection-lock lifetime and absence/equivalence claims.

### Task 5: Measure boundary latency and bound underlying work (R6)

**Files:** Modify `clinpgx_link/mcp/middleware.py`, `clinpgx_link/mcp/envelope.py`, `clinpgx_link/config.py`, diagnostics wiring and `scripts/benchmark_agent.py`; create `clinpgx_link/mcp/admission.py`; tests in `tests/unit/test_mcp_boundary_behaviour.py`, new `tests/unit/test_mcp_admission.py`, `tests/unit/test_benchmark_agent.py` and a real HTTP cancellation integration test.

**Interfaces:** Boundary owns monotonic elapsed_ms and timing_scope=tool_boundary on every returned success/error envelope. Configuration max active defaults 16, range 2–128; local ceil(n/2), upstream floor(n/2), no borrowing. Classification consumes Task 2 contracts; uncertain auto is upstream. Remove implicit unmeasured zero from constructors while preserving exact mirroring.

- [ ] RED: Fake-clock assertions cover validation, successful source work, errors and overload. Hold upstream slots with controllable work and prove local calls proceed. Cancel a real HTTP caller while thread-backed work remains active: its slot must remain held until termination. Exercise deadline and subsequent recovery.
- [ ] Run `uv run pytest tests/unit/test_mcp_admission.py tests/unit/test_mcp_boundary_behaviour.py -q`; capture expected missing timing/admission failures.
- [ ] GREEN: Add prompt admission rejection, 60-second active-work deadline including scheduler wait, bounded upstream requests and cooperative SQLite cancellation. Underlying unfinished work remains accounted for; expose cancellation-pending only in diagnostics and degrade readiness until actual completion. No argument/body/exception telemetry. Classify overload/deadline/upstream throttle as above.
- [ ] Instrument evaluator send/result timing only where supported; unavailable is null with reason. Separate scheduler queue time, execution, cache/local/live and errors. Verify outbound 2 RPS unchanged, no mirrored divergence, real transport cancellation, lint/typing and local CI; commit as `feat: measure and bound MCP tool execution`.
- [ ] Review cancellation lifetime, routing pools, timeouts, synchronization and telemetry privacy.

### Task 6: Freeze evaluation evidence and exercise the mounted MCP (R7)

**Files:** Extend `scripts/benchmark_agent.py` only after Task 5, add evaluation aggregation/source-check scripts and tests, add versioned held-out prompt manifest, frozen judge rubric and `docs/benchmarks/opus-ux-remediation.md`. Raw questions/traces/judgments remain private where required; commit digest manifests and concise reports.

**Interfaces:** Reuse current harness summary/trace contract. Preserve all twelve prompts, frozen eighteen manifest and their limits. The independent held-out author and rubric preparation may run read-only/in disjoint evaluation files before implementation; no implementer reads held-out question bodies.

- [ ] RED: Fixture-based aggregator tests reject model mismatch, truncated traces, 36 instructed calls, missing aspect coverage, any observed score <=90, nonpassing source assertions, capacity errors and a failed validation batch. Null never counts as 100. Check deterministic serialization/token estimator and append-only attempt history.
- [ ] Run focused evaluator tests, then implement aggregation with independent factual assertions bound to snapshot/archive/member or captured live responses. Preserve individual Opus and one blinded Fable judgment per trace; report absolute differences, not averaged passes. Record model, effort and unknown sampling settings honestly.
- [ ] Run all twelve frozen tasks, four isolated actual Claude Code Opus sessions concurrently against one pinned HTTP MCP candidate. Each task retains 35 instructed/40 hard calls, 600 seconds and 32 MiB trace cap. Record code/snapshot/config/prompt hashes, queue/execution time, calls/errors/cost/cache and source checks.
- [ ] Inspect concrete failures and implement scoped regressions through the earlier task boundaries; never edit frozen prompts or repeat unchanged candidates until lucky. Each passing development candidate receives exactly three fresh validation batches including held-out tasks. Any failure makes that campaign nonpassing. Stop for direction if no reproducible issue supports another change.
- [ ] Independently run original frozen eighteen under unchanged 30/30 call and 180-second limits. Publish precise remaining release/container/coverage gates rather than claiming whole-project completion from UX scores.
- [ ] Run final `make ci-local GENEFOUNDRY_ROUTER_DIR=../genefoundry-router`, verify ignored traces and exact staged files, then whole-branch code review before handoff.

## Dependency and review ownership

Implementation tasks 1–5 are serialized at shared files; independent held-out/rubric preparation can proceed alongside implementation. The controller writes plans, checks interfaces and dispatches implementation/code-review tasks. One Fable 5.1 plan review follows creation; there are no spec/plan re-review cycles. Runtime code review and mounted evaluations are not document reviews.

| Tasks sharing files/interface | Resolution |
| --- | --- |
| 1 → 3 (SourceInfo, row shaping) | Selection consumes corrected owning-row provenance; do not duplicate source construction. |
| 2 → 3/4/5 (contracts/recovery/facade) | Later tasks consume the shared map; serialize edits. |
| 3 → 4 (dataset search/cursors) | Diagnostic metadata must preserve selection-bound continuation. |
| 4 → 5 (SQLite progress hooks/lock) | Deadline and diagnostic hooks compose under the connection lock; neither resets an active outer deadline. |
| 5 → 6 (harness/timing) | Freeze timing fields before batch aggregation; unavailable measurements remain explicit. |
| 1–6 individually | Tests and implementation target the same R1–R7 contracts; final >90 acceptance is empirical, not inferred from unit tests. |
