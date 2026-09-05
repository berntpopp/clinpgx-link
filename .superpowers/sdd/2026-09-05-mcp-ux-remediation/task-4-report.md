# Task 4 report: honest matching and bounded diagnostics

## Status

Implemented R5 honest scientific matching and bounded exact-zero diagnostics on
base `a409a085ed712445bfc81671a4f62b52de95a475`. No SQLite schema,
candidate-builder, release, registry, coverage-table, dependency, or source fixture
contract changed.

ASCII `*` is now rejected only in the free-text `query` argument with fixed
guidance to omit `query` and use a supported exact `gene` or `name` filter.
Structured filter values containing `*` remain exact. Other punctuation remains
literal token search: `Reference/Reference`, `5-fluorouracil`,
`N-acetyltransferase`, slash-separated drug text, and rsIDs are tokenized and are
not represented as exact matches.

## TDD evidence

Initial query-contract RED:

```text
$ uv run pytest -q tests/unit/test_repository.py -k 'ascii_star or nonstar_punctuation'
FFF.....
3 failed, 5 passed, 32 deselected
```

All three required star cases (`*1`, `*1/*1`, `CYP2C19*2`) failed because no
exception was raised. All five allowed punctuation cases already demonstrated the
intended literal token shape.

Expanded diagnostic/progress/MCP RED before implementation:

```text
$ uv run pytest -q tests/unit/test_search_diagnostics.py tests/unit/test_repository.py \
  -k 'diagnostic or dpyd or star_query or ascii_star'
FFFFFFFFFFFFF
13 failed, 37 deselected
```

Failures covered absent `search_diagnostics`, absent progress-hook interfaces,
missing wildcard recovery, missing MCP fencing, and the three wildcard rejection
cases.

Controller review identified a nested ownership edge case. The focused regression
reproduced it before the fix:

```text
$ uv run pytest -q tests/unit/test_search_diagnostics.py -k identical_nested
F
ValueError: list.remove(x): x not in list
1 failed, 11 deselected
```

Root cause: dataclass value equality caused an identical inner budget to remove the
outer budget object. Cleanup now removes the exact state by identity. The same
focused command then passed.

Final focused GREEN:

```text
$ uv run pytest -q tests/unit/test_search_diagnostics.py tests/unit/test_repository.py
52 passed in 0.98s
```

The focused tests cover positive exact behavior, negative DPYD behavior, wildcard
scope, allowed punctuation, first-two priority, retaining all other filters,
distinct record counts despite duplicate membership rows, BINARY distinct top
three, hostile examples, absence of failed-value echo, deterministic VM
interruption, a deterministic 50 ms clock expiry, expired outer-deadline
composition, identical nested-budget cleanup, unrelated-query recovery, indexed
plans, real-size selectivity, MCP zero-result disclosure/fencing, and fixed cursor
recovery for selector mismatch, expiry, and nonzero offset.

## Implementation and interfaces

- `data/search_diagnostics.py` owns canonical diagnostic priority
  `id/gene/chemical/variant/name/source/annotation_id`, literal query validation,
  bounded diagnostic SQL, exact streaming aggregation, and progress ownership.
- `DatasetRepository.search()` adds `SourceResponse.details["search_diagnostics"]`
  only for exact zero results having supported canonical filters. It never changes
  result membership, count, order, offset, pagination, matching, or coverage
  semantics.
- At most the first two present canonical keys are diagnosed. Each query removes
  only its target key, retains dataset/member/query and remaining canonical scope,
  counts distinct matching records, and retains at most three distinct example
  strings ordered by UTF-8 byte order, equivalent to SQLite BINARY order for these
  validated text values.
- Every individual result has `status=available` with a complete exact count and
  examples, or only `status=diagnostics_unavailable`. Interrupted counts/examples
  are discarded. The top-level status is unavailable if any requested diagnostic
  was interrupted.
- Exact source-header predicates currently require `json_each` and therefore lack
  the indexed access required by R5. Searches combining those predicates with
  canonical filters receive explicit per-filter `diagnostics_unavailable`; the
  implementation does not ignore the predicate or fabricate a result.
- MCP places diagnostics under `_meta.search_diagnostics`. Only stored examples
  become `untrusted_text` fences. Filter names, statuses, counts, and the explicit
  “Observed examples are not equivalence claims” limitation are code-owned. Caller
  values are absent.
- Wildcard and cursor errors use closed `RecoveryPlan` variants. Guidance is fixed,
  executable via the capability tool, and never replays a cursor or issues a search
  with changed selectors.

The composable Task 5 seam is:

```python
with repository.execution_budget(deadline=absolute_monotonic_deadline):
    repository.search(...)
```

The context must wrap work in the same worker thread. It holds the repository's
existing reentrant lock and composes with nested diagnostic budgets. Lower-level
tests may inject `DiagnosticLimits(step_budget=..., timeout_seconds=...,
quantum=...)`; the production diagnostic defaults are exactly 50,000 SQLite VM
steps, 0.05 monotonic seconds, and a 100-step callback quantum.

`SQLiteProgressHooks` explicitly owns its connection's callback and maintains all
active budgets. A callback interrupts when any active local or outer budget is
spent. Inner exit leaves the owner callback and outer state active; final exit or
exception clears the owned callback. Python SQLite has no progress-handler getter,
so this code does not claim to discover or restore an unknown prior callback.

## Query-plan and real-snapshot work evidence

The diagnostic stream deterministically anchors on the first remaining canonical
filter in code-owned priority, using `membership_lookup(kind,value,match_mode,
record_pk)`. With no remaining filter it uses
`record_dataset_member_ordinal`. Target values use the membership primary-key
index. It performs no statistics/count probe and no source-specific lookup.
Streaming state is one last record key, one exact count, and at most three example
strings; no complete vocabulary is retained.

The unit EXPLAIN regression rejects `SCAN` and `TEMP B-TREE` and confirms both
`membership_lookup` and `sqlite_autoindex_membership_1` searches.

The supplied immutable real candidate
`data-clinpgx-extended-b18839f000000000/clinpgx.sqlite` produced:

```text
exact filters gene=DPYD,name=*1/*1: total_count 0
remove gene: available, count_without_filter 11,
             examples CYP2B6 / CYP2C19 / CYP2C9
remove name: diagnostics_unavailable
post-diagnostic SELECT 1: 1
```

Both real plans were:

```text
SEARCH anchor USING COVERING INDEX membership_lookup
SEARCH r USING INTEGER PRIMARY KEY
SEARCH candidate USING COVERING INDEX sqlite_autoindex_membership_1 LEFT-JOIN
```

There was no full-table scan or temporary sort. The DPYD/name relaxation exhausting
the fixed work budget is intentionally disclosed as unavailable; no partial count
or fabricated examples escaped, and the unrelated query succeeded after handler
cleanup.

## Final verification

The required pinned gate was run once on the final code before commit:

```text
$ make ci-local GENEFOUNDRY_ROUTER_DIR=../genefoundry-router
format: 138 files already formatted
lint: All checks passed
file-size: passed
vendor/conformance digests: verified; pinned router bytes verified
mypy: Success: no issues found in 68 source files
unit: 892 passed, 2 warnings in 16.86s
FastMCP import check: passed
```

The two warnings are the existing FastAPI/Starlette dependency deprecations. They
remain explicitly deferred; no dependency or warning suppression changed.

## Files and self-review

- Added `clinpgx_link/data/search_diagnostics.py`.
- Added `clinpgx_link/mcp/search_diagnostics.py`.
- Added `tests/unit/test_search_diagnostics.py`.
- Modified `clinpgx_link/data/repository.py`.
- Modified `clinpgx_link/mcp/dataset_record_tools.py`.
- Modified `clinpgx_link/mcp/envelope.py`.
- Modified `clinpgx_link/mcp/recovery.py`.
- Modified `tests/unit/test_repository.py`.

Self-review confirmed that all SQL identifiers and metadata keys are code-owned;
all caller values stay bound parameters; diagnostics run on the repository's same
immutable connection and serialized lock; no source body, argument, exception, or
query telemetry was added; original zero results remain zero; exact matching and
`data/coverage.py` authority are unchanged; all production modules remain below
600 counted lines; and no Task 5 admission or 60-second execution behavior was
implemented.

Remaining concern: broad but valid real scopes can legitimately return
`diagnostics_unavailable` under the fixed budget. This is the required honest
behavior, not a transient retry recommendation. No blocking concern remains.
