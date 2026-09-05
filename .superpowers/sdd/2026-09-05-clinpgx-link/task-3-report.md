# Task 3 report: download catalog and queryable snapshots

Date: 2026-09-05. Branch: `feat/clinpgx-mcp`.

## Gated interface sketch

The RED suite defines the following Task 3 boundary without adding production code:

- `DownloadCatalog.load(registry, coverage)` accounts for all 120 registry entries
  and exposes exact per-file dates/sizes/evidence without claiming unreviewed rights.
- Frozen `SourceInput.from_path(...)` binds a canonical dataset ID and HTTPS source
  URL to exact local bytes, SHA-256, byte count, acquisition/publication metadata,
  media type, license ID, registry tier, and optional HTTP version evidence.
  `read_verified()` detects mutation between cataloguing and build.
- `ArchiveLimits` and `read_local_source` preserve archive/member bytes exactly while
  rejecting wrong signatures, traversal/alias paths, symlinks, oversized archives,
  oversized members, total expansion overflow, and member-count overflow.
- `TabularReader` yields original header/value mappings and one-based source ordinals;
  duplicate headers and width drift fail rather than overwrite or pad fields.
- `iter_json_records` streams top-level array entries, retains object roots, and emits
  stable RFC 6901 pointers plus nearest retained-parent pointers for nested array
  records. Exact member bytes remain independently retained.
- `parse_spreadsheet` returns sheet/row/cell objects that distinguish values, formulas,
  absent cached values, absent cells, merged ranges/anchors, and hidden sheet state.
- `build_snapshot(sources, destination, release_tag)` writes a candidate at
  `<destination>/<release_tag>/clinpgx.sqlite`, returns its manifest/database/snapshot
  identity, never activates `current`, and removes an invalid partial candidate.
  Frozen inputs must produce byte-identical SQLite and manifest outputs regardless of
  destination path.
- `DatasetRepository` implements the shared signatures verbatim over one immutable
  database handle. Repository pages use the standard row shape and exact pagination
  details, validate `expected_snapshot` before dataset lookup/count/select, expose
  only canonical filters, use literal AND FTS tokens, and apply declared membership
  tokenizers only.
- Summary evidence/allele joins use exact annotation identity and return original child
  rows once with join provenance. Relationship lookup retains published endpoint
  direction. The observed all-Yes genes VIP field remains source data plus an anomaly
  warning, never a normalized truth claim.

## RED evidence

Fixtures were added before production modules. `sourced/` contains reduced,
change-labelled excerpts retaining actual wrappers/headers; `adversarial/` contains
only project-created malformed inputs.

```text
$ uv run ruff check tests/unit/test_catalog.py tests/unit/test_acquire.py \
    tests/unit/test_builder.py tests/unit/test_repository.py tests/unit/test_formats.py
All checks passed!

$ uv run pytest tests/unit/test_catalog.py tests/unit/test_acquire.py \
    tests/unit/test_builder.py tests/unit/test_repository.py tests/unit/test_formats.py -q
49 failed in 0.14s
```

Every failure is the intended missing-feature failure (`clinpgx_link.data.catalog` or
`clinpgx_link.ingest` absent); imports live inside test bodies, so collection succeeds
and each contract test independently demonstrates RED.

## Implementation and GREEN evidence

The Task 1 gate was approved before production work began. Implementation now covers
the frozen local-input boundary, candidate builder, loss-preserving parsers and the
pinned read-only repository.

Additional TDD cycles caught real-data problems that the reduced fixtures did not:

- Recursive pathway JSON initially produced a normalized row for every nested array
  value. A failing regression fixed `pathways.json` to its 283 published top-level
  pathway records while keeping the exact complete JSON member independently.
- Repeating long external record IDs in every membership index made a 5,000-row test
  database 28,983,296 bytes for a 610,107-byte TSV. A failing storage regression led
  to integer internal keys plus contentless FTS; the exact external identity remains
  unique and returned by the repository.
- The All of Us ZIP contains canonical directory entries and AppleDouble resource
  forks whose names end in `.tsv`. Separate failing regressions now preserve directory
  and platform-metadata bytes/status without extraction or attempted tabular parsing.
  Traversal, aliases, links, encryption and duplicate paths remain rejected.
- Failing tests bind the compressed-byte cap to the verified file read, bind snapshot
  identity to build limits and parser/schema/coverage source identity, and publish
  the profiled frequency-field semantics through dataset discovery.

The database stores both exact source layers (`source_archive.raw` and every
`source_member.raw`) and derived normalized rows. Derived rows retain dataset/member,
one-based ordinal or RFC 6901 pointer/parent pointer, exact source field names, and a
stable external record ID. The manifest records per-source timestamps/digests,
per-member parser status/limitations, the build-limit projection and a digest of the
schema/parser/coverage implementation. Frozen inputs therefore remain reproducible,
while a materially different transform or admission configuration cannot reuse the
same snapshot identity.

The repository implements:

```text
status()
list_datasets()
describe(dataset_id)
search(dataset_id, *, member=None, query=None, filters=None, limit=20,
       offset=0, match="exact", expected_snapshot=None)
get_record(record_id, *, expected_snapshot=None)
search_entities(entity_type, *, query=None, filters=None, limit=20,
                offset=0, expected_snapshot=None)
related(record_id, *, result_type, other_id=None, limit=20, offset=0,
        expected_snapshot=None)
asset_content(dataset_id, *, member=None, expected_snapshot=None)
read_asset(dataset_id, *, member=None, start=0, length=4096,
           expected_snapshot=None)
```

`asset_content` returns the exact retained bytes only after the configured full-body
bound is checked. `read_asset` returns independently decodable base64 chunks with
progress and complete-byte digest. `get_record.details.asset` and described members
provide dataset/member/digest/media type/byte count; callers can form the shared
digest-bound reference without this layer inventing an opaque reference syntax.
Tests prove archive and member retrieval still works after the build input is deleted
or replaced.

### Actual current-core validation

The final current-code run used the 15 locally retained archives downloaded and
verified during source research, registry timestamps captured in
`docs/research/clinpgx-download-registry-2026-09-05.json`, and a frozen acquisition
observation of `2026-09-05T10:30:00Z`. This is build evidence, not a checked-in data
release. The source archives were not copied into the repository.

```text
elapsed:       18.761 s
SQLite bytes:  1,038,249,984
records:       452,450
members:       521 indexed; 190 preserved; 0 quarantined
snapshot:      sha256:8b8f522b4c510de45c69bb6bb7265c3bd8a183c52c1ce89b93d8f2b71f7d6ead
transform:     clinpgx-link-ingest-v1:sha256:f6e64e791ee01c8c3bd6dfc0cc6239f9aae5881be3cfc104d797461941409a91
```

| Dataset | Registry source date | Normalized rows |
| --- | --- | ---: |
| `chemicals.zip` | 2026-09-05 00:31:19 -07:00 | 5,313 |
| `clinicalVariants.zip` | 2026-09-05 00:31:23 -07:00 | 5,191 |
| `drugLabels.zip` | 2026-08-05 01:12:06 -07:00 | 1,671 |
| `drugs.zip` | 2026-09-05 00:31:19 -07:00 | 3,763 |
| `genes.zip` | 2026-09-05 00:37:36 -07:00 | 25,041 |
| `guidelineAnnotations.json.zip` | 2026-08-05 01:12:06 -07:00 | 7,879 |
| `occurrences.zip` | 2026-08-05 01:10:20 -07:00 | 146,520 |
| `pathways-biopax.zip` | 2026-08-05 01:10:54 -07:00 | 0 (145 members retained) |
| `pathways-tsv.zip` | 2026-08-05 01:10:54 -07:00 | 3,212 |
| `pathways.json.zip` | 2026-08-05 01:10:55 -07:00 | 283 |
| `phenotypes.zip` | 2026-08-05 01:11:00 -07:00 | 1,621 |
| `relationships.zip` | 2026-08-05 01:11:34 -07:00 | 127,786 |
| `summaryAnnotations.zip` | 2026-08-05 01:12:59 -07:00 | 50,899 |
| `variantAnnotations.zip` | 2026-08-05 01:14:32 -07:00 | 65,656 |
| `variants.zip` | 2026-08-05 01:34:43 -07:00 | 7,615 |

The observed `genes.tsv` all-`Yes` VIP column is retained byte-for-byte and row-for-row
but surfaced as `upstream_anomaly:genes_is_vip_all_yes`; it is not projected as a
verified VIP fact.

Five repeated calls on one open immutable repository handle (count, select and JSON
decoding included) produced these medians: exact PA124 gene 14.845 ms; exact PA449053
chemical 3.173 ms; CYP2C19 + clopidogrel summary-member query 3.724 ms (six source
rows); literal FTS `clopidogrel platelet` 1.351 ms (226 rows); relationship membership
query 51.304 ms (732 rows). Summary `655384602` joined to exactly one evidence and
three allele source rows. The relationship lookup is the clear remaining local-index
performance candidate; it is correct but slower than the other indexed selectors.

### Actual auxiliary validation

The final current-code run used all six researched auxiliary archives:

```text
elapsed:       7.014 s
SQLite bytes:  380,092,416
records:       366,491
members:       223 indexed; 56 preserved; 49 quarantined
snapshot:      sha256:6610829d1b61ade19332c382879e23b5f0a0207025d67aa294a9cfda96821754
```

| Dataset | Rows | Result |
| --- | ---: | --- |
| `clinpgxHaplotypes.zip` | 1,385 | Both current allele TSVs indexed; 3 documentation/license members preserved. |
| `cpic.drug.mapping.zip` | 684 | All 171 profiled XLSX workbooks indexed; schema/source pairs validated. |
| `haplotypes.zip` | 0 | Exactly 49 corrupt XLSX members quarantined; 2 metadata/license members preserved. |
| `pharmcat.zip` | 358,124 | All 3 JSON members indexed incrementally; 2 license/creation members preserved. |
| All of Us frequencies | 2,730 | 31 data TSVs indexed; all 44 directory/platform/document members preserved. |
| UKBB frequencies | 3,568 | 16 TSVs indexed; 3 license/creation/document members preserved. |

Profiled memberships make current allele definitions, CPIC drug identifiers,
PharmCAT parent-gene + named-allele rows, and both frequency exports queryable without
changing their raw fields. On five repeated calls, medians were: cross-source
CYP2C19 `*1` allele rows 12.418 ms (17 population/source rows), CPIC clopidogrel +
PA449053 0.851 ms (one mapping row), PharmCAT/auxiliary TPMT `*1` 11.587 ms (16
source/population rows), All of Us CYP2C19 `*1` 0.212 ms (seven groups), and UKBB
CYP2C19 `*1` 0.242 ms (seven populations). Counts are source rows, deliberately not
deduplicated biological assertions.

## Verification

Final commands and outputs are recorded after implementation:

```text
$ uv run pytest -q tests/unit/test_catalog.py tests/unit/test_acquire.py \
    tests/unit/test_builder.py tests/unit/test_repository.py tests/unit/test_formats.py \
    tests/unit/test_spreadsheets.py
96 passed

$ uv run ruff check clinpgx_link/data clinpgx_link/ingest \
    tests/unit/test_catalog.py tests/unit/test_acquire.py tests/unit/test_builder.py \
    tests/unit/test_repository.py tests/unit/test_formats.py tests/unit/test_spreadsheets.py
All checks passed!

$ uv run mypy --strict clinpgx_link/data/catalog.py clinpgx_link/data/coverage.py \
    clinpgx_link/data/repository.py clinpgx_link/ingest/acquire.py \
    clinpgx_link/ingest/builder.py clinpgx_link/ingest/tabular.py \
    clinpgx_link/ingest/json_records.py clinpgx_link/ingest/spreadsheets.py
Success: no issues found in 8 source files
```

### Review remediation

The independent round-one review initially rejected the implementation. Each finding
was reproduced before its correction:

- XLSX row-limit quarantine left a partial inserted row with a zero manifest count.
  Member savepoints now roll back all partial rows; only the explicitly identified
  legacy `data/haplotypes.zip` may quarantine ordinary workbook corruption, while
  resource-limit and required-profile failures abort the candidate (`041fb8b`).
- Nested OOXML originally had no package, part, XML, grid, cell, or merge-area bounds.
  The first bounded implementation exposed three deeper parser/preflight mismatches:
  UTF-16 XML, non-ASCII namespace prefixes, and relocated relationship targets all
  reached `openpyxl`. The final admission path accepts only UTF-8 XML, uses streaming
  Expat events rather than lexical tag regexes, validates `[Content_Types].xml`, binds
  canonical workbook sheet IDs to their exact OPC relationships, and bounds the exact
  internal worksheet parts consumed by `openpyxl` before loading. A final parser-path
  audit found that shared strings and other XML are selected through content types as
  well. The supported CPIC workbook profile is therefore closed and MIME-bound, every
  parser-consumed XML part (including `.rels`) has UTF-8/declaration/element bounds,
  and shared-string materialization has its own cap (`7009f5e`, `2e6c9cc`, `3218870`,
  `8ef6fdd`, `1d25018`). Fourteen focused spreadsheet tests and all 171 current CPIC
  workbooks (684 rows) pass.
- Public `SourceInput` construction and catalog ledger parsing now validate complete
  immutable provenance, URL/dataset binding, unique IDs, and nonnegative non-boolean
  sizes. The builder and runtime share the exported canonical release-tag validator
  (`b832214`; runtime wiring is owned by the server boundary).
- Summary parent/evidence/allele identities are required at ingest, and missing
  installed join membership fails closed instead of issuing an unfiltered child query
  (`e6df495`).
- Generic recursive JSON-key inference was removed. Only declared
  dataset/member/pointer profiles create memberships, and entity searches reject
  filters unsupported by the installed co-membership profile (`09daddc`).
- Incremental JSON parsing no longer requests binary floats. Non-integer source
  numbers are retained in normalized rows under the explicit JSON-safe representation
  `{"$clinpgxJsonNumber":"<exact decimal>"}`; source integers remain integers and the
  exact member bytes remain independently available (`391b886`).

The review's unsupported-ZIP-compression minor did not reproduce as an untyped escape:
Python's `NotImplementedError` is a `RuntimeError`, already covered by the acquisition
translation boundary. A crafted unsupported-compression member produced the expected
`DataValidationError`, so no speculative code change was made.

## Files and self-review

Task-owned production files are `data/{catalog,coverage,repository,schema.sql}.py/sql`
and `ingest/{acquire,builder,tabular,json_records,spreadsheets}.py`. Task-owned tests
are the six targeted unit modules and sourced/adversarial fixtures under
`tests/fixtures/exports/`.

Self-review checked canonical path handling, frozen-source mutation, independent
compressed/member/expanded/count bounds, exact BLOB digests, parser failure rollback,
deterministic database bytes, unknown-filter rejection, literal FTS, snapshot checks
before lookup/count/select, source-direction-preserving relationships, exact
annotation joins, and full/chunked historical asset retrieval. No runtime databases,
download archives, caches or `__pycache__` files belong in the commit.

## Explicit gaps and handoff

- HTTP bulk streaming is not implemented here. `SourceInput` intentionally accepts an
  explicit local catalogued file and streams its digest/read under a byte cap. The
  reference-data API client buffers bounded API responses and must not be mislabelled
  as a bulk downloader. Task 5/operator acquisition must stream allowed registry URLs
  to a staging file, record immutable HTTP evidence, and then construct `SourceInput`.
- BioPAX OWL, PDFs and other undeclared formats are exact-byte retrievable but not
  queryable tables. Document extraction/live fallbacks remain separate content/MCP
  work.
- The 49 corrupt legacy haplotype workbooks remain an explicit incomplete source;
  their exact bytes and per-member errors are retained, but no rows are fabricated.
- All of Us has no embedded license file, so its auxiliary validation receipt uses
  `unknown`; release installation must apply the reviewed license ledger rather than
  inherit a neighboring archive's license.
- The builder creates candidates only. Trust/signature verification, bundle assembly,
  installation, activation, rollback and retention belong to the release boundary.
