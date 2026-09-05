# Authenticated materialization implementation report

## Scope and design decisions

- Implemented on the approved source/rights contracts (`4e654d4`) and bounded bundle codec fix (`686d72b`). Bundle modules, server, CLI, and outer fleet manifest were not edited.
- Public APIs are frozen `ReleaseInput` and `MaterializationReceipt` records plus `stage_release`, `install_release`, and `rollback_release`. Every operation is offline and requires the independently pinned exact outer-manifest digest.
- `materialization.json` uses the root-approved exact projection: original outer UTF-8 text/hash, artifact/tree facts, schema range, tag, predecessor, source-set and snapshot identities. Runtime-v1 is excluded to avoid a digest cycle.
- Added actual retained `ArtifactProvenance.byte_count`; stale `registry_size` remains independent evidence. Database `parser_status` maps `indexed`/`quarantined` to `consumed=true`, `preserved` to false, and only `indexed` to `indexed=true`.
- `schema.json` is a canonical strict project contract with the exact SQLite IDs and exact five count fields. Its generated schema is 1,242 bytes, SHA-256 `23771f5f4db001da44b41ec88b21eaf6fe22402224a32e8ef75912ecb416905d`. Updated source schema is 8,746 bytes, SHA-256 `1c9607ca50bf35d5744bf456615c8595d45cf09a6de9940ab91c1aaadb75e8aa`.
- SQLite verification uses fixed code-owned SQL, immutable/read-only access through an admitted descriptor, `trusted_schema=OFF`, quick/foreign-key checks, exact IDs/counts/tag, and streamed archive/member BLOB hashing.
- One no-follow bounded exclusive lock covers predecessor/candidate staging, revalidation, and descriptor-relative selection. Generation publication is NOREPLACE. Selection restores the prior link if parent fsync fails; dual commit/recovery failure has an explicit recovery subtype and preserves evidence.

## TDD evidence

Database-schema RED before the module existed:

```text
$ uv run pytest tests/unit/test_release_schema.py -q --tb=short
FFFFFFFFFF                                                               [100%]
10 failed in 0.03s
```

All ten failures were the expected missing `clinpgx_link.releases.schema` import.

Materializer RED before its module existed:

```text
$ uv run pytest tests/unit/test_materialize.py -q --tb=short
FF                                                                       [100%]
2 failed in 0.05s
```

Both failures were the expected missing `clinpgx_link.releases.materialize` import.
Subsequent focused RED/GREEN cycles covered direct predecessor clean installs,
per-source imported-count reconciliation, descriptor-safe retained verification,
and the corrected actual-byte-count source fact.

Final focused integration and dependency GREEN:

```text
$ uv run ruff check clinpgx_link/releases/materialize.py clinpgx_link/releases/materialization.py clinpgx_link/releases/schema.py tests/unit/test_materialize.py tests/unit/test_release_schema.py tests/unit/test_source_manifest.py
All checks passed!
$ uv run mypy clinpgx_link/releases/materialize.py clinpgx_link/releases/materialization.py clinpgx_link/releases/schema.py clinpgx_link/releases/source_manifest.py
Success: no issues found in 4 source files
$ uv run pytest tests/unit/test_materialize.py tests/unit/test_release_schema.py tests/unit/test_source_manifest.py tests/unit/test_release_licenses.py tests/unit/test_release_bundle.py tests/unit/test_runtime_identity.py -q --tb=short
........................................................................ [ 34%]
........................................................................ [ 69%]
..............................................................           [100%]
206 passed in 0.57s
```

The real fixture creates SQLite/source/rights/schema files, packs the actual bounded
zstd/USTAR artifact, installs A then B, and performs retained-only rollback to A.
Tests also cover clean B with direct A, skipped A→C through direct B without history
recursion, noncanonical authenticated outer bytes, stale registry sizes, actual-byte
and parser-status mismatches, compressed and retained tampering, extra/link files,
same-tag collision, interrupted staging, failed selection commit recovery, existing
generation revalidation, and concurrent identical bootstrap convergence.

Repository foundation gate:

```text
$ make ci-local
102 files already formatted
All checks passed!
vendor-check: digest verified
vendor-check: conformance digests verified
Success: no issues found in 54 source files
........................................................................ [  9%]
........................................................................ [ 19%]
........................................................................ [ 29%]
........................................................................ [ 39%]
........................................................................ [ 49%]
........................................................................ [ 59%]
........................................................................ [ 68%]
........................................................................ [ 78%]
........................................................................ [ 88%]
........................................................................ [ 98%]
............                                                             [100%]
732 passed, 2 warnings in 7.48s
<class 'fastmcp.server.server.FastMCP'> <class 'fastmcp.client.client.Client'> <class 'mcp.types.CallToolResult'>
```

Warnings are the existing third-party Starlette/AnyIO deprecations.

## Remaining integration gaps and concerns

- Seed-index/lock trust resolution, network preparation, operator CLI wiring, pruning, and container init integration remain later tasks. This library never derives rollback trust from its sidecar.
- The fixture is explicitly test-only and does not claim production full-profile coverage.
- Generation publication before activation may retain a fully verified but unselected generation after a later failure; this is intentional immutable evidence, not partial state. Only call-owned scratch is removed.
- SQLite immutable mode assumes sealed DELETE-journal snapshots, as produced by the current builder; WAL-based candidate admission is not supported.

## Independent review remediation — round 1

The `a5cb6f4` independent review identified five Important installer gaps and four
adjacent coverage gaps. The original author addressed them after the benchmark
harness was committed separately as `91167ab`.

- A non-bootstrap candidate can no longer authenticate itself as its predecessor.
  Self-digest predecessor remains limited to an actually empty bootstrap, while an
  exact already-current candidate takes the existing full-generation revalidation
  path regardless of a redundant predecessor argument.
- Fixed generation files, SQLite, and the installation lock now use nonblocking,
  no-follow descriptor admission before type/link/mode checks. A subprocess test
  proves a substituted FIFO cannot hang either fixed-file or lock admission; a
  separate flag-level regression pins `O_NONBLOCK`, and retained hardlinks fail.
- Semantic validation now checks the exact bidirectional relationship among every
  retained artifact, artifact `license_id`, license record, and
  `affected_artifacts`. This structural gate is deliberately separate from
  `validate_distribution`: local staging verifies recorded rights identity/mapping
  but does not fabricate or require an affirmative public/controlled/operator-local
  publication decision. A valid locally retained release with a negative local
  distribution decision stages; mismapped evidence fails as `rights_invalid`.
- All SQLite `fetchall` calls were removed. Quick/integrity diagnostics read at most
  the decisive rows, singleton facts read at most two rows, and dataset/member rows
  stream one at a time against the already bounded source inventory with one final
  excess-row probe. BLOB contents remain chunk-hashed.
- First creation of `versions` now requires `fsync(data_root)` before staging can
  continue. A synthetic parent-fsync failure is typed and cannot select data or
  populate the new generation directory.
- Lock timeout conversion catches huge-integer overflow as typed invalid input.
  Tests additionally pin application-version incompatibility and dual selection
  commit/recovery failure cleanup; a failed recovery no longer strands its private
  recovery symlink.

TDD RED evidence was observed before implementation: 7 of 29 tests failed for the
reproduced self-predecessor activation, fixed FIFO hang, missing rights mapping,
remaining `fetchall`, absent parent sync, raw `OverflowError`, and stranded recovery
link. A separate two-test run then failed both the true self-predecessor activation
and missing nonblocking flags after correcting the reproduction fixture.

Fresh scoped GREEN evidence:

```text
$ uv run --frozen pytest -q tests/unit/test_materialize.py
..............................                                           [100%]
30 passed in 0.51s
$ uv run --frozen ruff check clinpgx_link/releases/materialize.py clinpgx_link/releases/materialization.py tests/unit/test_materialize.py
All checks passed!
$ uv run --frozen mypy clinpgx_link/releases/materialize.py clinpgx_link/releases/materialization.py
Success: no issues found in 2 source files
$ uv run --frozen pytest -q tests/unit/test_materialize.py tests/unit/test_release_schema.py tests/unit/test_source_manifest.py tests/unit/test_release_licenses.py tests/unit/test_release_bundle.py tests/unit/test_runtime_identity.py
216 passed in 0.64s
```

The source modules remain below the repository cap: 453 and 301
nonblank/noncomment lines. The immutable-generation behavior is unchanged: a fully
verified but unselected candidate can remain after later activation failure, while
call-owned scratch/recovery links are cleaned where the filesystem permits it.

The fresh repository foundation gate also passed: `make ci-local` formatted/linted
106 files, verified both vendor gates, strictly typed 54 modules, and passed 758
unit tests in 7.70 seconds with the two existing third-party deprecation warnings.
