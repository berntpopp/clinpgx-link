# Task 2 report: API and website read adapters

## Outcome

Implemented the bounded ClinPGx REST, observed website-route, and CPIC reference-read
adapters. Every public request is bound by a vendored operation registry before URL
construction. Complete response bytes are retained through the shared `ContentStore`,
and decoded results carry digest, retrieval time, media type, byte count, status,
cache state, and content reference provenance.

The website registry contains 36 captured ClinPGx website operations and all 40
captured CPIC GET inventory entries. The CPIC schema root, internal Flyway table, and
four unverified GET RPCs remain discoverable but `inventory_only` and non-callable;
the other 34 read-only table/view routes use bounded `limit`/`offset` pagination.

## Evidence inputs

- ClinPGx primary website operations: 23 operations,
  SHA-256 `dafe6240b476c2f49398c6c25d2014c857258e4bb42266b40633b9c859157e37`.
- ClinPGx additional guideline/tab operations: 13 operations,
  SHA-256 `3064cd07a9724377bca056a4f59ce4862ff3f97ef2604dd2f5f3204c2ba5c60c`.
- CPIC operation inventory: 40 GET operations,
  SHA-256 `be43f0158a98f01048d8a9447eb418eb69bf4ac46345017e9be62f562651d50d`.
- ClinPGx REST registry remains the byte-derived 34-operation Task 1 inventory.

The normalized evidence hashes are embedded in `api/website_operations.json`; source
files under `docs/research` remain audit evidence rather than runtime dependencies.

## RED evidence

- API registry: 22 import failures before `ApiRegistry` existed.
- API client: 11 failures before the bounded client/cache existed.
- Website/CPIC adapter: 6 import failures before its registry and client existed.
- API service: 4 import failures before the stable service mappings existed.
- Added focused RED cases for representation/content-type mismatch, noncanonical and
  outside-prefix redirects, missing JSend envelopes, duplicate JSON keys, non-finite
  JSON, source-specific cache provenance, scheduler limit injection, and CPIC 206
  pagination headers.

## Implemented contracts

- Exact operation identity and separated path/query/form binding; unknown keys,
  missing runtime criteria, unsafe path segments, unsupported representations and
  unverified operations fail as domain `InvalidInputError`s.
- Route-specific decoding for JSend objects/arrays, raw reports, strict JSON/JSON-LD,
  numeric text, no-content 204, HTML, TSV/text attachments, and the observed
  text/plain allele-function JSON exception.
- JSON-LD requires `application/ld+json`; HTML and JSON likewise require their
  requested media type. Duplicate keys and non-finite numbers are rejected rather
  than silently normalized.
- One serialized scheduler covers ClinPGx API, website, CPIC, and every retry at no
  more than 2 requests/second. Network errors and 429/502/503/504 retry at most twice
  under one overall deadline.
- Redirects require canonical, userinfo-free, port-free HTTPS origins and remain
  under the configured `/v1` path prefix. Callers cannot supply URLs.
- Complete decoded bodies are capped without partial success and are admitted to the
  shared `ContentStore` before response success. TTL/LRU decoded caching returns a
  defensive copy with `data_source=cache` while retaining original source identity.
- CPIC calls use the separate configured base/origin, `Range-Unit: items`, and
  `Prefer: count=exact`; exact `Content-Range` is retained. CPIC CC0 and ClinPGx
  CC-BY-SA metadata remain distinct.
- The documented broken VIP API detail operation returns typed guidance to the
  verified `GET /site/vip/{id}` fallback; it never switches source silently.

## Verification

- Targeted Task 2 unit suite: `56 passed in 0.13s`.
- Fresh shared full unit suite: `228 passed, 1 failed`; the sole failure is the
  concurrently owned Task 3 ZIP-directory preservation test in
  `tests/unit/test_acquire.py`, outside this task's files. Task 2 tests remain green.
- Ruff on every owned source/test file: clean.
- Strict mypy on every owned source module: clean.
- Actual opt-in bounded live probe at one aggregate request/second:
  `CLINPGX_RUN_LIVE_TESTS=1 uv run pytest -q tests/integration/test_sources.py` →
  `1 passed in 2.01s`. It verified the live ClinPGx gene website route and CPIC
  guideline page, including CPIC exact-count pagination and both source digests.

## Scope notes

Attachment links discovered inside responses are not followed here; the task-owned
adapter only retains the response bytes. The MCP boundary/content-reference layer can
register and fetch approved embedded attachments under its separate path-prefix
contract. CPIC arbitrary projections/order, the schema document, internal migration
history, and GET RPCs are intentionally unavailable pending route-specific evidence.
