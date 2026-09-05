# ClinPGx Link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build a fleet-conformant ClinPGx MCP with comprehensive public data access, verified fallback coverage, independently versioned data releases, and real local-agent benchmarks.

**Architecture:** Download-backed SQLite discovery and search, plus explicit cached live API/website adapters for current details and export gaps. Separate domain services, MCP envelopes, source acquisition, and immutable data release operations.

**Tech Stack:** Python 3.12+, uv/Hatchling, FastAPI, FastMCP 3.x, MCP SDK 1.x, Pydantic 2, httpx, SQLite/FTS5, Typer/Rich, structlog, asgi-correlation-id, zstandard, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-05-clinpgx-link-design.md`.

Status: own-model adversarial review completed (Fable usage limit; substitute explicitly
authorized). Corrections are specified in the binding
`docs/superpowers/specs/2026-09-05-source-access-contract.md`; read that addendum with
this plan. It supersedes preliminary signatures/acceptance wording when inconsistent.

## Global constraints

- Python 3.12+, `uv`, frozen `uv.lock`, Hatchling packaging.
- HTTP only: unified FastAPI and MCP by default; REST-only mode for fleet parity.
- Package `clinpgx_link/`, single entrypoint `clinpgx-link = clinpgx_link.cli:app`.
- Modules remain below 600 nonblank/noncomment lines; separate domain and MCP planes.
- Default one serving worker; API requests including retries are spaced at least 0.5 seconds apart.
- No arbitrary caller URLs, caller-token forwarding, upstream body/error logging, SQL inputs, or unbounded archive extraction.
- Tools are read-only; refresh, installation, publication and rollback are CLI/workflow operations.
- Primary MCP keys are `result`/`results`; errors use the six fleet codes and isError=true.
- Source identity, coverage, dates, citations and research-use flags survive all response modes.
- Never fabricate full-source coverage, remote release assets/digests, or test/review success.
- Use only exact observed source contracts; unsupported/unavailable upstream operations return explicit limitations and a tested alternative when one exists.

## Execution order and ownership

First finalize source contracts and obtain adversarial review. Task 1 establishes shared interfaces.
Tasks 2 and 3 then run in parallel, with Task 4's boundary tests developed against fakes.
Task 5 consumes the verified data builder and can run alongside Task 4's integration.
Task 6 integrates every component and supplies end-to-end evidence. Main agent owns
cross-component integration, spec/plan updates and final verification; agents own
only their assigned modules/tests. No sibling repository is changed.

## Shared interfaces

Create these in Task 1 and import them thereafter; do not create competing wrappers.

```python
# clinpgx_link/models.py
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class SourceInfo:
    source: str
    url: str
    retrieved_at: str
    sha256: str
    data_source: str  # api, api_cache, website, download
    published_at: str | None = None
    release_tag: str | None = None
    coverage: str = "unknown"  # complete, partial, unknown, unavailable
    warnings: tuple[str, ...] = ()

@dataclass
class SourceResponse:
    value: Any
    source: SourceInfo
    details: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class BoundRequest:
    method: str
    path: str
    params: dict[str, Any]
    form: dict[str, Any] | None = None
    representation: str = "json"

# Domain errors contain fixed safe messages and structured developer-owned details.
# SourceResponse is a data-plane result, never an MCP success/error envelope.
```

`exceptions.py` defines `ClinPGxError(message, *, field=None, hint=None,
subtype=None)` and subclasses `InvalidInputError`, `NotFoundError`,
`AmbiguousQueryError`, `UpstreamUnavailableError`, `RateLimitedError`,
`DataValidationError`, and `ResponseTooLargeError`. DataValidationError maps to
upstream_unavailable with subtype=data_invalid; ResponseTooLargeError maps to
invalid_input with subtype=response_too_large. Only developer-owned messages and
grammar-validated identifiers enter these exception fields.

`SourceResponse.value` is the complete raw operation result, or a repository page's
list of row objects. A repository row has `record_id`, `dataset_id`, `member`,
`ordinal`, `fields` (original row/object) and optional validated `id`. Repository
page details always contain `total_count`, `offset`, `returned`, `has_more`, and
`snapshot_id`. Full API arrays omit repository page markers: the MCP boundary pages
them from the cached full value, binding cursors to the response SHA-256. Registry
and catalog discovery arrays follow that full-value convention. `snapshot_id` is
the validated snapshot/database identity, not the archive acquisition time.

The boundary creates all `_meta.pagination` fields. It must not page an already
paged repository response a second time. Dataset cursors decode to a validated
offset plus snapshot/query identity before calling repository.search; API cursors
reuse the cached full result and reject expired/mismatched identity.

Stable collaborator interfaces:

```python
class ClinPGxClient:
    async def request(self, method: str, path: str, *,
                      params: dict[str, Any] | None = None,
                      form: dict[str, Any] | None = None,
                      representation: str = "json") -> SourceResponse: ...
    async def close(self) -> None: ...

class ApiRegistry:
    def list_operations(self) -> list[dict[str, Any]]: ...
    def describe(self, operation: str) -> dict[str, Any]: ...
    def bind(self, operation: str, path_parameters: dict[str, Any],
             query_parameters: dict[str, Any], *,
             form_parameters: dict[str, Any] | None = None,
             representation: str = "json") -> BoundRequest: ...

class ApiService:
    async def call(self, operation: str, *, path_parameters: dict[str, Any] | None = None,
                   query_parameters: dict[str, Any] | None = None,
                   form_parameters: dict[str, Any] | None = None,
                   representation: str = "json") -> SourceResponse: ...
    async def search(self, entity_type: str, filters: dict[str, Any],
                     view: str = "base") -> SourceResponse: ...
    async def get(self, entity_type: str, record_id: str,
                  view: str = "max") -> SourceResponse: ...

class DatasetRepository:
    def status(self) -> dict[str, Any]: ...
    def list_datasets(self) -> SourceResponse: ...
    def describe(self, dataset_id: str) -> SourceResponse: ...
    def search(self, dataset_id: str, *, member: str | None = None,
               query: str | None = None, filters: dict[str, str] | None = None,
               limit: int = 20, offset: int = 0, match: str = "exact",
               expected_snapshot: str | None = None) -> SourceResponse: ...
    def get_record(self, record_id: str, *,
                   expected_snapshot: str | None = None) -> SourceResponse: ...
    def search_entities(self, entity_type: str, *, query: str | None = None,
                        filters: dict[str, str] | None = None, limit: int = 20,
                        offset: int = 0,
                        expected_snapshot: str | None = None) -> SourceResponse: ...
    def related(self, record_id: str, *, result_type: str,
                other_id: str | None = None, limit: int = 20, offset: int = 0,
                expected_snapshot: str | None = None) -> SourceResponse: ...
```

These are interface signatures, not placeholder implementation bodies. The concrete
tests and required behavior below define each implementation. Changes to signatures
must be coordinated by main before callers are written.

### Task 1: Runtime foundation and source contracts

**Files:** `pyproject.toml`, `uv.lock`, `clinpgx_link/{__init__,config,models,exceptions,logging_config}.py`, `clinpgx_link/api/operations.json`, `clinpgx_link/data/coverage.json`, `tests/unit/test_foundation.py`, `Makefile`, `AGENTS.md`.

**Consumes:** verified research inventories and fleet standards.
**Produces:** shared interfaces/settings/errors and package installation.

- [ ] Freeze source operation/fallback coverage from the actual inventories; include documented-but-broken operations with status and an observed fallback, never delete them from coverage accounting.
- [ ] Write failing tests for installed version parity, validated settings, no credential reflection, exact six-code taxonomy, and complete operation/coverage inventory.

```python
def test_operation_inventory_matches_source(registry, official_operations):
    implemented = {(o["method"], o["path"]) for o in registry.list_operations()}
    expected = {(o["method"], o["path"]) for o in official_operations}
    assert expected <= implemented

def test_rejects_excess_api_rate():
    with pytest.raises(ValidationError):
        Settings(api_requests_per_second=3)
```

- [ ] Run `uv run pytest tests/unit/test_foundation.py -q`; confirm missing implementation failure before creating modules.
- [ ] Implement Pydantic settings with documented CLINPGX_ environment variables, exact HTTPS origin allowlists, bounded timeout/body/cache/ingest settings and validated paths. API defaults permit api.clinpgx.org; downloads additionally require s3.pgkb.org. Website and approved attachment origins come from the verified coverage manifest.
- [ ] Implement metadata-derived version, dataclasses, typed domain exceptions and payload-free correlated logging. Install with `uv sync --group dev` and verify installed FastMCP symbols directly.
- [ ] Run foundation tests, Ruff and mypy, then commit this independently usable contract.

### Task 2: Complete API and website read adapters

**Files:** `clinpgx_link/api/{client,registry,cache,website}.py`, `clinpgx_link/services/api.py`, `tests/unit/test_{api_client,api_registry,api_service,website}.py`, `tests/integration/test_sources.py`.

**Consumes:** Task 1 types/settings/errors and verified registry.
**Produces:** ClinPGxClient, ApiRegistry, ApiService plus explicit website fallback adapters.

- [ ] Write failing tests against real recorded JSend objects/arrays, raw report objects/arrays, numeric text, 204, HTML Infobutton, empty 404 search versus missing-record 404, and bad-body/status cases.

```python
@pytest.mark.asyncio
async def test_gene_response_preserves_source(api_service, gene_api_fixture):
    result = await api_service.get("gene", "PA124")
    assert result.value["id"] == "PA124"
    assert result.value["vipTier"] == gene_api_fixture["data"]["vipTier"]
    assert result.source.sha256
    assert result.source.data_source == "api"

def test_unknown_query_parameter_is_rejected(registry):
    with pytest.raises(InvalidInputError):
        registry.bind("GET /data/gene", {}, {"symobl": "CYP2C19"})
```

- [ ] Run the targeted tests and observe failures.
- [ ] Implement registry binding with required path/query semantics, real enum vocabularies and route-shape overrides. Use operation identity `METHOD /path/template`; validate path segments before URL construction. Explicitly declare runtime-required search criteria missing from OpenAPI. Preserve wider website/export source vocabularies through their verified adapters instead of sending unsupported API enums.
- [ ] Implement shared request scheduler, decoded body cap, redirect-origin checks, bounded retries/deadlines and TTL response cache. Cache immutable content with source date/digest; do not cache outages as valid empty data. Test concurrency with a fake clock and request hooks.
- [ ] Implement route-specific decoders and verified website endpoints required by the coverage matrix. A live fallback must be tested with a captured response and an actual bounded live probe. Broken VIP endpoint gets typed coverage guidance and working gene/website fallback.
- [ ] Add live drift/probe tests separately marked `integration`; do not run them in ordinary unit tests. Run API tests, strict typing and lint, then commit.

### Task 3: Download catalog and queryable source snapshots

**Files:** `clinpgx_link/data/{catalog,repository,schema.sql,coverage}.py`, `clinpgx_link/ingest/{acquire,builder,tabular,json_records,spreadsheets}.py`, `tests/unit/test_{catalog,acquire,builder,repository,formats}.py`, `tests/fixtures/exports/`.

**Consumes:** Task 1 contracts; registry discovery through the bounded client.
**Produces:** DatasetRepository; `build_snapshot(sources, destination, release_tag)` returning a validated source manifest and database.

- [ ] Write small fixtures matching actual headers/wrappers, including summary evidence/allele joins, per-file guideline JSON, array-root pathway JSON, large CSV fields and profiled allele spreadsheets. Keep synthetic adversarial fixtures distinct from sourced excerpts.
- [ ] Write failing tests for exact source/member/row identity, no column loss, query counts, bad filters, snapshot provenance, nested JSON navigation, failed refresh retention, unsafe member rejection and format-signature mismatch.

```python
def test_failed_refresh_preserves_previous_snapshot(built_store, malformed_archive):
    before = built_store.database.read_bytes()
    with pytest.raises(DataValidationError):
        build_snapshot([malformed_archive], built_store.root, "data-2026-09-05-bad")
    assert built_store.database.read_bytes() == before

def test_unknown_filter_is_not_successful_empty(repository):
    with pytest.raises(InvalidInputError):
        repository.search("genes.zip", member="genes.tsv", filters={"Symobl": "CYP2C19"})
```

- [ ] Run the targeted tests and observe failures.
- [ ] Implement catalog identity/date/license tiers, streaming downloads, byte and expansion caps, safe archive readers, and atomic SQLite build. Store original normalized records plus raw artifact/member hashes; reject duplicate headers and malformed rows rather than silently discarding values.
- [ ] Index arrays as retrievable records while retaining complete original document access. Support TSV/CSV/JSON and profiled spreadsheets required for allele/function/phenotype data. Represent merged cells, formula/cached values and missing cells honestly. Preserve source artifacts for other formats; implement tested live/document fallbacks required by the coverage matrix.
- [ ] Create indexed identifiers/labels, FTS, and relationship/annotation joins. Treat the all-Yes VIP column and empty CPIC guideline arrays as known upstream anomalies, not normalized facts. Do not use aggregate live stats as exact snapshot checksums.
- [ ] Run fixture tests then actual current-core import and query comparisons; report each source's coverage and date. Run lint/type checks, then commit.

### Task 4: Fleet MCP boundary and HTTP service

**Files:** `clinpgx_link/mcp/{facade,envelope,middleware,notfound_guard,untrusted_content,pagination,shaping,capabilities,resources}.py`, `clinpgx_link/mcp/tools/`, `clinpgx_link/{cli,server_manager}.py`, `tests/unit/test_mcp_*.py`, `tests/conformance/`.

**Consumes:** Tasks 1–3 services; fake services permit early independent tests.
**Produces:** `create_mcp(services)` and `create_app(settings)`; HTTP CLI and all public tools.

- [ ] Write failing real-FastMCP-client tests for structuredContent/TextContent equality, primary payload keys, all response modes, argument errors, unknown-name reflection, untrusted-text digests, paging and source identity.

```python
@pytest.mark.asyncio
async def test_bad_enum_has_fleet_execution_error(client):
    result = await client.call_tool("get_record", {"entity_type": "banana", "record_id": "PA124"}, raise_on_error=False)
    assert result.is_error
    assert result.structured_content["error_code"] == "invalid_input"

@pytest.mark.asyncio
async def test_response_modes_preserve_row_ids(client):
    identities = []
    for mode in ("minimal", "compact", "standard", "full"):
        call = await client.call_tool("search_records", {"entity_type": "gene", "filters": {"symbol": "CYP2C19"}, "response_mode": mode})
        identities.append([r["id"] for r in call.structured_content["results"]])
    assert all(ids == identities[0] for ids in identities)
```

- [ ] Run tests and confirm failure before boundary code.
- [ ] Implement the thirteen tools frozen in the binding source-access addendum with Pydantic argument descriptions/examples/enums/bounds, compact default, read-only annotations, and under-budget definitions. Website and document operations must be exercised through MCP.
- [ ] Implement one envelope boundary, source-text fencing and limits, fixed safe errors and the current fleet unknown-name guards. Validate JSON pointers and paginate selected arrays; never use a zero-progress continuation for oversized records.
- [ ] Implement snapshot/query-bound opaque cursors with tamper checks and expiry/refresh errors. A page total describes the returned source set, not a fabricated upstream corpus total; expose unknown upstream completeness separately.
- [ ] Mount stateless JSON `/mcp` at root after health/REST routes; add exact Host/Origin guards, correlation IDs and lifecycle client cleanup. Implement package Typer serve/config/health/version and connect data commands from Task 5.
- [ ] Vendor byte-identical fleet transport and behavior probes and exercise them against a real local HTTP process. Test schema budgets, version parity, citations/safety and resources. Run lint/type checks and commit.

### Task 5: Independent immutable data releases and hardened packaging

**Files:** `clinpgx_link/releases/`, `clinpgx_link/ingest/cli.py`, `docker/`, `container-release.json`, `.github/workflows/`, `.dockerignore`, `docs/{data-releases,deployment,configuration}.md`, `tests/unit/test_releases.py`, `tests/integration/test_release_roundtrip.py`.

**Consumes:** validated Task 3 snapshot builder and current fleet release manifest/runtime identity contracts.
**Produces:** reproducible release artifacts, verified install/rollback commands and runnable hardened image.

- [ ] Freeze exact schema/identity contract from `docs/research/data-release-design.md`; do not substitute an incompatible manifest that merely resembles fleet metadata.
- [ ] Write failing tests for build→validate→pack→install→query→upgrade→rollback; tampered compressed/uncompressed bytes, archive traversal, incompatible schema, interrupted extraction and same-tag/different-content collision must preserve previous active data.

```python
def test_corrupt_release_never_activates(installer, good_release, corrupt_release):
    installer.install(good_release)
    previous = installer.active_identity()
    with pytest.raises(DataValidationError):
        installer.install(corrupt_release)
    assert installer.active_identity() == previous

def test_rollback_restores_exact_identity(installer, release_a, release_b):
    installer.install(release_a)
    expected = installer.active_identity()
    installer.install(release_b)
    installer.rollback()
    assert installer.active_identity() == expected
```

- [ ] Run the tests and confirm failure.
- [ ] Implement deterministic source-selection manifests, separate build timestamps, data schema compatibility, canonical runtime identity, bounded compression/extraction and atomic activation. Verify exactly all authoritative files and reject extra/symlink files. Keep mutable cache/staging outside the immutable data root.
- [ ] Implement `data catalog/build/refresh/validate/pack/pull/install/status/rollback` with concise progress and machine-readable JSON output; include a dry-run release diff showing changed sources/dates/counts/coverage. No bulk data mutation tool is exposed over MCP.
- [ ] Add scheduled/manual data-release workflow with minimal permissions, concurrency guard, source-change detection, validation reports, immutable unique tags/assets, conflict refusal and retained evidence. Separate build/validation from publication; use current fleet framework rather than hand-rolled insecure asset fetching.
- [ ] Add code-only digest-pinned multi-stage container and hardened Compose files with verified UID, read-only data, private exposure, caps/resource/log controls, healthcheck and explicit local-test overlay. Wire pinned reusable container/security workflows and metadata without fake remote identities.
- [ ] Build, install and roll back two real local data releases; run MCP against installed data and validate the hardened container. Record actual identities and commands. Run lint/type checks and commit.

### Task 6: Full coverage audit, real-agent benchmark and handoff

**Files:** `scripts/benchmark_agent.py`, `tests/eval/`, `docs/benchmarks/`, `README.md`, `CHANGELOG.md`, `CITATION.cff`, `LICENSE`, `docs/{architecture,mcp-tool-catalog,coverage}.md`, `docs/integration/`.

**Consumes:** all preceding tasks and actual installed local data release.
**Produces:** demonstrable full objective completion or a precise remaining-coverage ledger.

- [ ] Populate a machine-readable feature/field/fallback matrix for every official API operation and public website data family, including nonregistry sources. Require runnable MCP tests and source fixture/probe evidence. Separate inventory-accounting and objective-coverage gates: explained unavailable families remain incomplete, not passes. Follow the binding source-access addendum's six coverage dimensions.
- [ ] Run deterministic agent-workflow tests covering CYP2C19/clopidogrel, DPYD/fluoropyrimidines, TPMT/NUDT15, SLCO1B1/statins, rs4244285, guidelines versus labels, allele definitions, population frequencies, literature, pathways, negative results and freshness.
- [ ] Freeze the 18-case manifest and sourced assertions specified in the source-access addendum before implementation tuning. Launch the actual HTTP MCP and run Claude Code (or an available explicitly authorized own-model agent) with only that server. Preserve traces, tool names/arguments, result IDs/citations, latency, token counts and failed-case classification. All 18 cases must complete, all critical assertions pass and at least 90% of noncritical rubric points pass; report unavailable cases in the denominator.
- [ ] Compare live API, warm cache and installed snapshot through MCP, not just internal functions; report startup/build/query costs separately. Verify large evidence arrays remain fully reachable through continuation/pointers.
- [ ] Run `make ci-local`, integration tests, shared conformance and hardened-container checks, plus release roundtrip and rollback. Investigate failures rather than weakening requirements or fixtures.
- [ ] Obtain independent implementation review; address findings and rerun affected gates. Complete requirement-by-requirement audit from the ledger.
- [ ] Write fleet README section order and full tools table, generated/validated tool catalog, source licenses/citations, data release runbook and local router integration fragment. No broken local links or invented live URLs/status.
- [ ] Commit completed implementation and evidence. Mark the user goal complete only when all original and follow-up requirements have current supporting evidence.

## Adversarial review gate

Use authenticated Claude Code `--model fable --effort high` with read-only file
tools and strict empty external MCP configuration. The availability probe identified
`claude-fable-5-1`. Supply this plan, the spec, source-selection report, coverage
matrix and fleet/data-release evidence. Save the exact input hashes and JSON review
output. Resolve concrete findings in the spec/plan before dispatching implementation.
Fable returned a usage-limit error before reading inputs. The user subsequently
authorized own-model or AGY 3.8 Flash review. Dispatch an independent GPT-6 reviewer
and preserve its actual identity, findings and dispositions; do not label that
review as Fable. No additional Fable quota is needed under the amended instruction.
