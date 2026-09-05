# Task 1 report: runtime foundation and source contracts

Date: 2026-09-05. Branch: `feat/clinpgx-mcp`. Base: `100e7d4`.

## Outcome

Implemented the independently usable Python package foundation and the shared data-plane contracts frozen in the plan/addendum. The package installs under Python 3.12 with locked dependencies, exposes a metadata-derived version, validates bounded `CLINPGX_` settings, protects sensitive configuration, supplies the exact domain error taxonomy, and emits payload-free correlated logs.

Vendored the byte-identical live OpenAPI capture, its normalized 34-operation inventory, and a conservative coverage ledger accounting for all 120 captured registry files and all ten required website families. The ledger deliberately marks every MCP retrieval dimension false at foundation time. It retains the documented-but-broken VIP operation and two observed working fallbacks.

## TDD evidence

RED was recorded before any application module or vendored package registry existed:

```text
$ uv run pytest tests/unit/test_foundation.py -q
33 failed in 0.20s
```

Failures were the intended missing-feature failures: no package version, models, settings, exceptions, logging module, operations registry, or coverage registry.

After the minimal implementation and formatting correction:

```text
$ uv run pytest tests/unit/test_foundation.py -q
................................. [100%]
33 passed in 0.12s
```

The tests cover:

- installed version parity with distribution metadata;
- exact `SourceInfo`, `SourceResponse`, and frozen `BoundRequest` collaborator contracts;
- API rate, timeout, body, cache, archive, ingest, page, chunk, and response-budget bounds;
- canonical HTTPS origins and safe absolute runtime paths;
- the `CLINPGX_` environment prefix;
- `SecretStr`, repr/JSON exclusion, and credential-free validation errors;
- the exact six error codes and required data-invalid/response-too-large subtypes;
- request correlation and removal of payload/query/URL/token fields from logs;
- byte identity of the actual OpenAPI and complete 34-operation accounting;
- all 120 download registry entries, the broken VIP route plus fallback evidence, and ten required website families without fabricated MCP availability.

## Verification evidence

```text
$ uv sync --group dev
Resolved 107 packages
Checked 104 packages

$ make check-fastmcp
<class 'fastmcp.server.server.FastMCP'>
<class 'fastmcp.client.client.Client'>
<class 'mcp.types.CallToolResult'>

$ uv run ruff format --check clinpgx_link tests/unit/test_foundation.py
8 files already formatted

$ uv run ruff check clinpgx_link tests/unit/test_foundation.py
All checks passed!

$ uv run mypy clinpgx_link
Success: no issues found in 7 source files

$ uv lock --check
Resolved 107 packages

$ make ci-local
9 files already formatted
All checks passed!
Success: no issues found in 7 source files
55 passed in 0.15s
FastMCP, Client, and CallToolResult imported successfully
```

Installed versions observed during verification were clinpgx-link 0.1.0, FastMCP 3.4.7, MCP 1.29.1, Pydantic 2.13.5, pydantic-settings 2.15.0, and structlog 26.1.0.

`uv build --wheel --out-dir /tmp/clinpgx-wheel-check` succeeded. Wheel inspection confirmed inclusion of `api/openapi.json`, `api/operations.json`, and `data/coverage.json`. The vendored OpenAPI SHA-256 exactly matches `/tmp/clinpgx_openapi.json`:

```text
d5945ffec00d99df4b10c8df605ff95ca1e1f360279541580c67097e93f06cc6
```

An independent registry assertion reported:

```text
PASS: 34 API operations, 120 downloads, 20 website refs accounted; no MCP retrieval claimed
```

## Assigned files

- `pyproject.toml`
- `uv.lock`
- `clinpgx_link/__init__.py`
- `clinpgx_link/config.py`
- `clinpgx_link/models.py`
- `clinpgx_link/exceptions.py`
- `clinpgx_link/logging_config.py`
- `clinpgx_link/api/openapi.json`
- `clinpgx_link/api/operations.json`
- `clinpgx_link/data/coverage.json`
- `tests/unit/test_foundation.py`
- `Makefile`
- `AGENTS.md`
- `.superpowers/sdd/2026-09-05-clinpgx-link/task-1-report.md`

No sibling repository was modified. Concurrent main-owned content-reader files and unrelated research/spec changes were not edited or staged.

## Self-review and concerns

- `SourceInfo`, `SourceResponse`, and `BoundRequest` match the frozen shared interface verbatim. `BoundRequest` is dataclass-frozen, but its dict members remain intentionally normal Python mappings; callers must not mutate a request after binding.
- `source_auth_token` is an optional operator/service secret placeholder, excluded from repr and serialization. No current code sends it. Task 2 must not attach it to public ClinPGx requests unless an explicit registry policy is added.
- Coverage is a source/accounting baseline, not a claim that the application already serves the records. Its root state is `foundation_inventory_not_runtime_coverage`; all API, website, and dataset `mcp_retrievable` values are false.
- Dataset `acquired`, `parsed`, and `searchable` are false because those dimensions describe an installed runtime release, not the prior throwaway research benchmark. Task 3/release manifests must record actual per-release states.
- Coverage references `api/website_operations.json`, which Task 2 owns. It also seals the current 27-operation research evidence hash and only cites operation IDs present in that evidence.
- The API OpenAPI is vendored byte-for-byte; `operations.json` adds stable `METHOD /path` identities without removing captured parameters or response declarations.
- Full `make ci-local` was run after the main agent's independently owned content-reader slice became green: repository formatting, Ruff, strict mypy, all 55 unit tests, and installed FastMCP symbol imports passed.

## Independent-review corrections

The first scoped review found that logging filtered field names but did not validate
the retained values, and that `allowed_hosts` accepted malformed host strings. Both
were reproduced with tests before changing application code.

RED evidence:

```text
$ uv run pytest tests/unit/test_foundation.py -q
13 failed, 35 passed in 0.16s
```

The failures covered secrets and newline-forged values in every retained direct and
context-bound log field, invalid scalar metric types/bounds, ten malformed DNS/IP
forms, and duplicate hosts. The earlier `req-123` assertion was changed to canonical
UUID `2eb4ae86-7f47-4be9-945a-36d1f103230c`; this preserves the correlation behavior
test while leaving invalid-request handling to the hostile-value regression.

GREEN evidence after the minimal fixes:

```text
$ uv run pytest tests/unit/test_foundation.py -q
48 passed in 0.15s

$ uv run ruff check clinpgx_link/config.py clinpgx_link/logging_config.py tests/unit/test_foundation.py
All checks passed!

$ uv run mypy clinpgx_link/config.py clinpgx_link/logging_config.py
Success: no issues found in 2 source files
```

The logging processor now validates every retained value against a fixed
developer/registry vocabulary or a strict bounded scalar/identifier contract. Invalid
events and request IDs become fixed markers; all other invalid values are dropped.
The host validator accepts only unique canonical lowercase ASCII DNS names (including
explicit punycode A-labels) or canonical unbracketed IPv4/IPv6 literals. Whitespace,
ports, brackets, Unicode U-labels, case normalization, malformed labels, and ambiguous
numeric dotted forms are rejected rather than normalized.
