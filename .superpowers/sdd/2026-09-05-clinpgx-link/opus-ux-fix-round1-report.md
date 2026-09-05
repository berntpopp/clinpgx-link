# Opus UX Fix Round 1 Report

## Scope

This iteration fixes the two failures observed in the mounted Opus baseline without
changing public canonical selector vocabulary, untrusted-text fencing, response-size
limits, or release installation behavior:

- serialize all operations on the single read-only SQLite connection owned by
  `DatasetRepository`;
- index profiled PharmCAT `phenotypes.json` diplotype children under the existing exact
  canonical `name` membership, inherited `gene` membership, and disclose these supported
  filters in dataset descriptions;
- inline only a bounded, code-profiled PharmCAT diplotype child shape while retaining
  unknown or hostile structures behind derived-content recovery.

The transform change in `data/coverage.py` changes snapshot identity and requires an
operator rebuild before the new `gene` + `name` membership is available in a mounted
release. The root agent owns that rebuild and live verification.

## RED evidence

The focused RED command was:

```text
uv run pytest -q \
  tests/unit/test_repository.py::test_repository_serializes_parallel_queries_on_its_shared_connection \
  tests/unit/test_builder.py::test_auxiliary_rows_gain_only_profiled_gene_allele_and_drug_memberships \
  tests/unit/test_mcp_dataset_records.py::test_profiled_pharmcat_diplotype_child_is_complete_and_inline \
  tests/unit/test_mcp_dataset_records.py::test_unprofiled_pharmcat_diplotype_map_remains_deferred
```

Result: `4 failed, 3 passed`. The failures were the deterministic concurrent-connection
overlap, missing DPYD diplotype `name` membership, and deferred CYP2C19/DPYD child rows.
The three malicious-map cases already passed because the pre-existing implementation
deferred every PharmCAT JSON row.

Before the fix, five concurrent `get_record` calls against the mounted snapshot also
produced concrete `sqlite3.InterfaceError`, false `record_id`/`dataset_id` not-found
errors, and `None` rows reaching JSON/asset handling. Repeating the live MCP request 100
times yielded 58 successes, 21 `internal`, 19 false `not_found`, and 2
`upstream_unavailable` results.

## Implementation

`data/repository_locking.py` supplies one signature-preserving serialization decorator.
The repository initializes an `RLock` before metadata access. Every method that directly
uses the shared connection, including nested helpers and `close`, holds that reentrant
lock through `fetchone`/`fetchall` and response construction. No live cursor crosses the
lock boundary.

`contextual_json_memberships` now recognizes only profiled PharmCAT
`/diplotypes/<index>` objects and maps a nonempty string `diplotype` to exact canonical
`name`. Existing parent-context logic supplies exact `gene`; no `diplotype` selector was
invented. Dataset and member descriptions publish sorted code-owned supported filters,
and the MCP argument description explains the exact `gene` + `name` combination.

The MCP field profile applies only to `data/pharmcat.zip`, `phenotypes.json`, and an exact
numeric diplotype-child pointer. It requires the five expected fields, permits only the
known optional `activityScore`, bounds strings, and validates `diplotypekey` as one or two
entries with integer counts 1 or 2. Dynamic keys must be `Reference`, a structured star
allele (including copy-number and tandem forms), or structured bounded `c.` coding-variant
notation.
Instruction-like keys, unknown fields, nonnumeric counts, excessive entries, other
pointers, and oversized strings remain deferred. Inline string values still use the
existing `untrusted_text` fence.

The post-commit security review identified that the new diplotype promotion needed an
explicit member guard. A focused RED fixture proved that an `unprofiled.json` member
could otherwise gain a `name` membership. The guard now requires `phenotypes.json`; the
fixture is GREEN. The exact hostile dynamic key `c.1 IGNORE ALL PRIOR INSTRUCTIONS` is
also covered and deferred by the structured coding-allele grammar.

## GREEN evidence

Focused component suites:

```text
uv run pytest -q tests/unit/test_repository.py tests/unit/test_builder.py \
  tests/unit/test_mcp_dataset_records.py tests/unit/test_mcp_datasets.py
84 passed in 1.54s
```

Static checks:

```text
uv run ruff check <touched Python files>
All checks passed!

uv run mypy clinpgx_link
Success: no issues found in 56 source files
```

The post-fix repository completed 100 mixed parallel search/get operations over the
mounted 795 MB snapshot with zero errors or mixed identities. An in-process MCP using
the same snapshot completed the formerly failing PharmCAT `CYP2C19`, minimal, limit-3
search 50 times with zero errors. Direct retrieval of
`record:5eb2d995e33ed4a89ff28b4dd33f85f51f71eb73444c6a50c9816a38464b51cb`
returned all five child fields inline, including `diplotypekey: {"*2": 2}`.

## Files

- `clinpgx_link/data/repository.py`
- `clinpgx_link/data/repository_locking.py`
- `clinpgx_link/data/coverage.py`
- `clinpgx_link/mcp/dataset_record_fields.py`
- `clinpgx_link/mcp/dataset_record_tools.py`
- `tests/unit/test_repository.py`
- `tests/unit/test_builder.py`
- `tests/unit/test_mcp_dataset_records.py`
