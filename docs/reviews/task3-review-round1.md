# Task 3 review, round 1

Date: 2026-09-05
Reviewer: gpt-5.6-sol (`/root/fleet_research`)
Scope: `clinpgx_link/data/{catalog,coverage,repository,schema.sql}.py/sql`,
`clinpgx_link/ingest/{acquire,builder,json_records,spreadsheets,tabular}.py`, and
their Task 3 tests. This was a read-only production-code review; focused probes wrote
only to temporary directories.

## Verdict

**Spec compliance: NOT APPROVED. Quality: NOT APPROVED.** Exact source-byte retention
and the ordinary immutable-repository path are well implemented, but two resource/
accounting failures are release blockers. Four additional correctness/provenance
issues should be resolved before treating a snapshot as complete.

## Critical findings

### 1. XLSX quarantine catches the global row-limit failure and leaves unaccounted rows

`_ingest_member` inserts rows as it iterates and raises when the per-build remainder is
exceeded (`builder.py:341-352`). Its broad `except DataValidationError` then converts
*every* XLSX failure into a successful quarantine (`builder.py:354-357`). There is no
member savepoint or row cleanup. The candidate therefore keeps rows inserted before
the failure while recording both member and dataset counts as zero
(`builder.py:439-485`). Repeating XLSX members can bypass `max_ingest_rows`, and query
counts disagree with the manifest.

A focused probe with `max_ingest_rows=1` and a two-row XLSX produced a successful
candidate with:

```text
record table: 1
source_member: quarantined, record_count=0
dataset: record_count=0
```

Quarantine must be restricted to an explicit dataset/member allowlist and occur in a
member savepoint that is rolled back. Safety-limit failures and required-profile schema
failures must abort the whole candidate.

### 2. Nested OOXML expansion and worksheet dimensions bypass all archive/row caps

The outer ZIP reader bounds only the compressed source ZIP and its immediate members
(`acquire.py:135-162`). An XLSX member is itself a ZIP. `parse_spreadsheet` reads that
member into memory, opens it twice with non-read-only openpyxl, and has no inner member,
expanded-byte, XML, sheet, row, column, cell, or merge-area limit
(`spreadsheets.py:46-80`). It then materializes every cell and expands every merged
range coordinate (`spreadsheets.py:63-71, 84-138`) before the builder's row limit can
run. A small nested compression bomb, a sparse cell at Excel's maximum coordinate, or
a giant merge range can exhaust memory/CPU despite the advertised archive and ingest
limits.

Apply bounded OOXML package admission before openpyxl and explicit worksheet/cell/
merge bounds. Prefer read-only parsing where it can preserve the declared semantics.

## Important findings

### 3. `SourceInput` validation is bypassable through its public dataclass constructor

Only `SourceInput.from_path` validates dataset ID, HTTPS source origin, timestamps,
tier, and file shape (`catalog.py:194-235`). The exported dataclass has no
`__post_init__` validation (`catalog.py:175-192`), while `build_snapshot` accepts any
`SourceInput` and does not revalidate those fields (`builder.py:519-535`). The byte
reader verifies only digest/path/size, plus ZIP media type later
(`catalog.py:237-268`, `acquire.py:135-139`).

A focused probe built a successful manifest containing dataset ID `../../forged`,
source URL `http://evil.invalid/forged`, and retrieval time `not-a-time` by directly
constructing the dataclass. This permits forged provenance and noncanonical identities.
Make invalid instances unconstructable or revalidate the complete receipt at the build
boundary, including the registry URL/path binding and metadata types.

### 4. Missing summary identity fails open to every evidence/allele row

For a summary parent, `related` looks up its `annotation_id`; when none exists it uses
an empty filter (`repository.py:392-406`). The resulting query returns every row in the
target evidence or allele member (`repository.py:417-428`). A focused snapshot with a
summary row lacking its ID and two unrelated evidence rows returned both as joined
children. Missing join identity must be a typed validation/not-found failure, never an
unfiltered join. The builder should also validate required join headers/values.

### 5. Local semantic support is overclaimed and partly inferred from generic JSON keys

`known_filters` declares `id`, `name`, `gene`, `chemical`, and `source` for every
`*.json.zip` without inspecting a dataset-specific profile (`coverage.py:322-344`).
`json_memberships` recursively interprets any `id`, `name`, `title`, or `source` key,
and guesses gene/chemical meaning from path substrings (`coverage.py:280-312`). This
can turn unrelated nested objects into canonical entities and makes an unsupported
filter return a successful empty result instead of the required actionable error.
`search_entities` also validates only the global filter vocabulary, not a per-entity
matrix (`repository.py:335-371`), despite the binding requirement that entity-specific
unsupported keys fail and capabilities declare installed combinations.

Replace blanket JSON inference with explicit dataset/member/pointer profiles and use
that same profile to validate `search_entities` and publish capabilities.

### 6. JSON normalized rows lose numeric precision

The incremental parser requests `ijson(..., use_float=True)`
(`json_records.py:90-101`). A focused input containing
`0.12345678901234567890123456789` was normalized to
`0.12345678901234568`. Exact member bytes remain retained, but `fields_json` no longer
preserves the source field value, contrary to the loss-preserving normalized-row
contract. Use a lossless numeric representation (with an explicit serialization
contract) or declare such values deferred rather than silently rounding them.

### 7. Builder accepts release tags that production readiness rejects

The builder only checks a prefix plus `/` and `..` (`builder.py:527-539`), while runtime
readiness accepts only `data-clinpgx-(core|extended)-[0-9a-f]{16}`. A focused build
accepted `data-clinpgx-invalid tag`; that candidate can never become healthy through
the specified production path. Use one shared canonical release-tag validator.

## Minor findings

- `DownloadCatalog.load` silently overwrites duplicate coverage-ledger IDs in a dict
  and accepts boolean/negative reported sizes (`catalog.py:109-162`). Validate unique
  ledger rows and nonnegative, non-boolean sizes before claiming 120/120 accounting.
- `_read_member` does not translate `NotImplementedError` for an unsupported ZIP
  compression method into `DataValidationError` (`acquire.py:111-125`). Untrusted
  archive failures should remain within the typed data-validation boundary.

## Confirmed strengths

- Source archives and every member, including preserved/quarantined members, are stored
  as exact BLOBs with byte counts and SHA-256 (`schema.sql:33-55`,
  `builder.py:409-438`). Repository asset reads no longer depend on build-cache files.
- Acquisition re-reads the frozen source under the compressed cap and checks both byte
  count and digest (`catalog.py:237-268`, `acquire.py:129-180`). The immediate archive
  layer rejects traversal/aliases, symlinks, encryption, duplicate paths, excessive
  members, per-member overflow, and aggregate expansion overflow.
- TSV/CSV handling is strict about UTF-8, duplicate/empty headers, row widths and field
  limits while retaining empty cells and ordinals (`tabular.py:35-75`).
- `DatasetRepository` resolves and opens one read-only immutable SQLite handle, keeps
  count and selection on that handle, and checks `expected_snapshot` before dataset or
  record lookup (`repository.py:29-58, 259-315, 335-428`). This is the right core
  defense against activation TOCTOU; release retention remains an installer duty.

## Adjacent authored logging fix (excluded from Task 3 verdict)

Commit `c1d07e1` changes only `clinpgx_link/logging_config.py` and
`tests/unit/test_foundation.py`. A RED regression demonstrated that stdlib `httpx`
logged a hostile query URL and FastMCP logged an arbitrary message plus traceback.
The fix erases known dependency messages/arguments/exception data at LogRecord creation
before any handler or telemetry can observe them, routes known dependency handlers to
the payload-free root, and emits a fixed structured `dependency_event`. Focused tests,
Ruff, mypy, and diff-check passed. The full unit run at that point had 295 passes and
six unrelated failures because newly added `tests/unit/test_file_size.py` referenced
the not-yet-created `scripts/check_file_size.py`. This adjacent fix still requires the
root's independent review.
