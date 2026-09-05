# ClinPGx Link design

Date: 2026-09-05. Status: measured source selection and independent adversarial
review complete; review corrections being frozen for implementation.

The binding [source access contract](2026-09-05-source-access-contract.md) specifies
the final tool signatures, large-content retrieval, local discovery, snapshot safety,
release boundaries and objective acceptance. It supersedes conflicting preliminary
wording below. The independently recorded round-one review is in `docs/reviews/`.

## Purpose and scope

Implement a research-only GeneFoundry MCP backend named `clinpgx-link`, namespace
`clinpgx`, exposing public ClinPGx records, annotations, relationships, reports,
and downloadable datasets. Common questions must be answerable by a local agent
using actual tool results and source citations. Comprehensive access means every
documented read operation is callable and every published download is discoverable,
with its content accessible as indexed records or an explicitly identified source
artifact. Missing, unavailable, non-public, unparsed, and oversized content must
never be represented as a complete empty result.

User follow-ups require (1) actual API/download timing before source selection,
(2) complete data-release support in this repository, and (3) verification that
downloads cover public API and website data, or a working fallback for every gap.
A downloadable binary link alone does not establish usable coverage of its tables
or text. The coverage matrix must distinguish content discovery, retrieval, parsing,
searchability, and field completeness and exercise each claimed fallback.

No patient-specific interpretation, phenotype inference, genotype calling, dose
calculation, or treatment recommendation is implemented. Tools retrieve source
evidence. Retrieval is read-only; archive acquisition and refresh are operator CLI
operations. No email, external publication, or production fleet changes are needed.

## Evidence and approach

Sources: `../../research/clinpgx-sources.md`, `../../research/fleet.md`,
`../../research/benchmark-design.md`, and `../../research/architecture-options.md`.
The current public API root is `https://api.clinpgx.org/v1`, its operation registry
is `https://api.clinpgx.org/openapi.json`, and its documented traffic limit is two
requests per second. The OpenAPI does not declare result pagination. Public exports
are discoverable through `/v1/data/file/data/?view=min` and generated monthly.

The measured decision in `../../research/api-vs-download-decision.md` selects a
hybrid design with separate, honest representations. API responses provide
rich current records and relationships; cached API responses retain their retrieval
date. SQLite indexes provide repeatable local search across export rows. A TSV row
must not masquerade as the full API record. Data source selection is visible in
every response. API-only startup works without a downloaded mirror; dataset queries
without an index return actionable availability errors.

## Stack and layout

- Python 3.12+, `uv`, frozen `uv.lock`, Hatchling packaging.
- FastAPI, FastMCP 3.x, MCP SDK 1.x, Pydantic 2, pydantic-settings, httpx.
- Typer/Rich CLI, structlog, asgi-correlation-id, SQLite/FTS5.
- Ruff (including security rules), strict mypy, pytest/pytest-asyncio/respx.
- Package `clinpgx_link/`, single entrypoint `clinpgx-link = clinpgx_link.cli:app`.
- Modules remain below 600 nonblank/noncomment lines; separate domain and MCP planes.
- HTTP only: unified FastAPI and MCP by default; REST-only mode for fleet parity.

## Upstream client and operation registry

`api/client.py` owns the long-lived asynchronous HTTP client, request scheduling,
bounded decoded-body reads, retries, response cache, and timeouts. Allow only exact
configured HTTPS origins. Reject userinfo and check every redirect hop (maximum 5).
Never accept arbitrary URLs from MCP callers. Do not forward caller authorization.
Default one serving worker; a process-wide scheduler spaces API requests at least
0.5 seconds apart, including retries and concurrent tools. Document that replicas
need shared rate coordination or a divided per-instance request budget.

Use fixed, sanitized upstream status errors: 404 not_found; 429 rate_limited;
timeouts/5xx upstream_unavailable; bad supported parameters invalid_input. Bound
Retry-After and retry attempts within a call deadline. Do not log upstream bodies,
query text, record content, tokens, or URLs containing caller parameters.

Vendor the actual OpenAPI as a source artifact with retrieval date and SHA-256.
`api/registry.py` resolves stable operation identifiers, path parameters, query
parameters, enums, array forms, required fields and bounds from that artifact.
Reject unknown operations/parameters and unsupported value types before HTTP.
Support all documented GET operations and the read-only Infobutton POST only if
its declared request contract is verified. Never generically enable future writes.
Registry tests compare implementation coverage to the vendored operations, and a
live drift check reports newly added/changed operations rather than silently trusting
them. Download-file discovery is a separately allowlisted observed endpoint.

The client caches complete bounded response objects under normalized operation and
arguments. It must detect unexpected body/envelope shapes. Locally paginated API
results are tied to the cached response digest and query identity. Unknown upstream
completeness is explicitly typed; a local slice is not proof that upstream returned
its whole database. Expired or mismatched cursors fail with invalid_input.

## Downloads and local storage

`data/catalog.py` normalizes public file registry records without losing metadata.
Each entry declares its identifier, filename, URL, reported size/date, content family,
license category and whether it is current, legacy, or a related-project artifact.
Never infer currentness from a filename alone. Preserve unavailable catalog rows.

`ingest/acquire.py` streams operator-selected sources to temporary files with byte,
time and disk-space limits and SHA-256. Validate each redirect destination. Sources
must come from the catalog or an explicit operator-only local path. Archive members
are read without extracting arbitrary paths; reject absolute/traversal names,
symlinks, encrypted members, and archive expansion beyond configured limits.

`ingest/builder.py` creates and validates a candidate SQLite snapshot without activating
it. The release installer exclusively owns atomic activation. A failed refresh
preserves the previous usable snapshot. Store source/member identity, exact row ordinal, original field names,
JSON payload, searchable text, schema and source metadata. Index TSV/CSV and JSON
records; recursively identify array records without silently dropping other JSON
structure. Inventory non-tabular members with content type, size and access location.
No presumed column spelling may discard records or fields. Unsupported members are
counted separately from indexed content. FTS queries use literal token semantics;
field filters validate against the selected member schema. No user-supplied SQL.

`data/repository.py` opens published SQLite files read-only and returns stable ordered
pages, counts, source identity and member coverage. Snapshot-based cursors bind the
dataset/member/query; refresh invalidates unavailable snapshot cursors explicitly.
Indexed rows have stable identifiers derived from snapshot, member and row ordinal.
Raw source and member manifests retain checksums and source dates for reproducibility.
Acquisition time is not represented as the upstream publication date.

CLI data commands: `catalog`, `build`, `refresh`, `status`. Build accepts explicit
dataset selections and a documented current-core profile; optional/related-project
artifacts are discoverable and selectable without silently assuming their licenses.
Data is stored outside application images. The MCP does not initiate bulk builds.

## Data releases

Ship reproducible data build, validate, pack, install/pull, status and rollback
operations with a documented release workflow. The release unit is an immutable
manifest plus compressed SQLite/source metadata artifact, separate from the code
image and semantic application version. Each manifest records build/schema version,
source artifact hashes and publication dates, retrieval times, record/member counts,
license categories, anomalies, coverage/fallback inventory, compatible application
schema versions, and archive/database content digests.

Build releases in a temporary directory; validate the database, source completeness,
semantic sentinels and counts before publication. A source fetch/import failure
must not quietly publish a successful partial release. Optional source profiles
have explicit manifests and never claim the full profile's coverage. Upstream
anomalies are preserved and typed; release validation must not convert them into
false normalized biological assertions.

Install verifies manifest/schema and compressed/decompressed digests, then atomically
activates the new snapshot. Interrupted or invalid installation leaves the current
release available. Keep the previous validated release for explicit rollback and
test rollback under the same serving contract. Application containers mount reference
data read-only; mutable API cache and operator staging live separately.

A scheduled/manual GitHub workflow builds and validates data, detects unchanged
source identity, and publishes uniquely tagged immutable assets only when changed.
Re-running a release must compare existing identity and refuse conflicting bytes;
never overwrite a published tag or asset silently. Preserve manifests and validation
reports as evidence on failure too. Follow the current fleet manifest/reusable
workflow contract identified in `../../research/data-release-design.md`. Local
release validation/installation/rollback are required before handoff; do not invent
remote release URLs, artifact digests, or publication success.

## MCP tool surface

These are functional boundaries; final parameter schemas are frozen in the plan.

| Tool | Contract |
|---|---|
| `get_server_capabilities` | Identity, tools/signatures, sources, supported families, workflows, limits, licenses and coverage |
| `get_diagnostics` | Local index/cache state, source dates and optional bounded upstream health |
| `search_records` | Local aliases/membership/broad discovery or explicit live filters; source selection and paging are visible |
| `get_record` | Gene, chemical, disease, variant, literature, annotations, pathway and other supported entity by ID |
| `get_related_records` | Connected-object or pair report, with declared result-type enum |
| `get_api_schema` | Discover supported read operation schemas and examples, paged when necessary |
| `get_api_data` | Validated access to every documented read operation, including reports and auxiliary record endpoints |
| `get_website_data` | Validated observed website operations for data missing from exports/OpenAPI |
| `list_datasets` | Discover public downloadable artifacts and local indexing status |
| `get_dataset` | Member schemas, upstream metadata, license category, checksums and explicit coverage |
| `search_dataset` | Query indexed export rows by literal search and validated member-field filters |
| `get_dataset_record` | Retrieve complete indexed row by stable identity; select a JSON pointer for large nested records |
| `get_source_content` | Digest-bound structure/text/exact-byte chunks, including retained source documents |

Tools use unprefixed canonical names and readOnlyHint=true, destructiveHint=false,
idempotentHint=true, openWorldHint=true. Every argument has a description, required
arguments and arrays have examples, closed vocabularies have enums, and numerics
have bounds. Per-tool schema cost is at most 1,200 estimated tokens, total at most
10,000. Use `dereference_schemas=False`; suppress output schema if needed to fit.

Every successful tool returns a JSON object with `success`, `result` or `results`,
`_meta`, `recommended_citation`, and `unsafe_for_clinical_use=true`, mirrored exactly
into MCP TextContent. `_meta` includes request_id, elapsed_ms, source, data source,
retrieval/snapshot identity, completeness and pagination when applicable. Echo the
correlation identifier into protocol metadata. `response_mode` is the fleet enum
minimal/compact/standard/full, default compact. Projection may omit optional fields
but must not remove rows or conceal sources/limitations. Full data remains reachable.

Paged tools accept limit/offset/cursor. `_meta.pagination` contains total_count
(null if unknown), has_more and next_cursor. Budget trimming must preserve a usable
continuation and correct counts. An oversized object exposes explicit deferred-content
descriptors with a usable get_source_content reference for structure, scalar or exact-byte
chunks. Acquisition cap failures require a verified narrower filter/download alternative
or remain an explicit incomplete coverage requirement. Never truncate silently or loop
forever on a zero-progress cursor. Soft response budget: 25,000 estimated tokens.

External free text is fenced at the MCP boundary as kind=untrusted_text, sanitized
NFC text, provenance and raw_sha256. Do not duplicate unfenced prose elsewhere.
Stable identity fields must follow a documented grammar; unconstrained names and
titles are external text too. Enforce v1.1 depth/count/byte limits with explicit
size errors. Trusted next_commands contain only fixed or validated identifiers.

The boundary alone converts exceptions and argument validation into isError=true
plus the six-code fleet envelope: invalid_input, not_found, ambiguous_query,
upstream_unavailable, rate_limited, internal. Specific subtypes distinguish response
limits and missing local datasets. Unknown tool/resource/prompt names and malformed
input must not reflect hostile input into responses or logs.

Resources expose capabilities, usage notes and the source catalog using `clinpgx://`
URIs. Larger original artifacts remain reachable through verified catalog URLs;
their accessibility and representation are stated explicitly instead of implying
that binary archives have been converted into searchable text.

## Serving, operations and integration

FastAPI owns `/health`, `/api/health`, typed REST routes using the same data services,
and documentation. Mount the FastMCP app at root with path=/mcp,
stateless_http=True and json_response=True. No trailing-slash redirect or transport
session ID. Check Origin and Host allowlists and protocol versions. Local defaults
bind 127.0.0.1. Container binding to 0.0.0.0 is limited to its network namespace.

Typer commands serve/config/health/version and data subcommands use environment
prefix CLINPGX_. Logging is JSON in production, readable in development, with request
correlation and no query payloads. Development API-only health reports missing mirror
readiness explicitly without failing liveness. Production /health requires the exact
configured data identity; /api/live supplies process-only liveness. A data upgrade or
rollback requires restart with its matching expected runtime digest.

Provide Docker multi-stage build, frozen dependencies, non-root fixed UID, code-only
runtime, healthcheck, hardened base/prod/npm Compose, read-only rootfs, explicit
data/cache/tmp mounts, resource limits, dropped capabilities and bounded logs.
Provide fleet-style CI/conformance/container/security workflows and release metadata,
but do not fabricate released image/data digests or deployed status. Supply an
integration fragment for the router and document local namespace validation.

## Verification and acceptance

1. Pin actual source schemas and small representative fixtures with provenance.
2. Unit-test registry validation, HTTP policy/retry/rate/cache behavior, actual export
   shapes, filtering/FTS, atomic refresh, snapshot cursors, schema drift and source identity.
3. Test every tool through a real MCP client, including bad arguments, hostile prose,
   missing data, upstream errors, all modes, pagination and complete-data retrieval.
4. Run shared fleet transport/behavior/contract probes against the local HTTP server;
   verify naming, schema budgets, citations, safety and content/structuredContent parity.
5. Build and run the hardened container, inspect effective controls, and test health/MCP.
6. Import real current exports. Measure cold acquisition/build, live API, warm API cache
   and local queries separately, with source dates and semantic differences reported.
7. Run a real Claude Code agent against only this local MCP for typical gene-drug,
   guideline-versus-label, annotation evidence, variant, relationship and freshness
   questions. Save tool traces, citations, rubric outcomes, latency and token metrics.
8. Run Ruff, strict mypy, meaningful unit/integration tests and all required local gates.
9. Obtain adversarial review of this spec and its implementation plan (Fable 5-family
   via Claude Code, or the user's explicitly authorized own-model/AGY substitute);
   record model identity, inputs/hashes, findings and resolutions. Review implementation
   independently before completion. A completed review is not itself proof of passing tests.

The requirements ledger remains authoritative for outstanding work. Published endpoint
coverage and benchmark success claims must be supported by the resulting evidence,
not inferred from tool counts or a generic smoke test.
