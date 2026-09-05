# clinpgx-link

Read-only ClinPGx source evidence over HTTP MCP, following the GeneFoundry fleet's
Python/uv, FastAPI and FastMCP stack.

Research use only. Not clinical decision support: no patient-specific interpretation,
genotype calling, phenotype inference, dose calculation or treatment recommendations.

## Implementation status

This repository is under active development, **not an end-to-end production release**.
The API/website adapters, local SQLite builder and repository, source-content reader,
and 13-tool MCP runtime are implemented. Release manifest admission and stable release
identities are being integrated. Verified packaging/install/upgrade/rollback, hardened
deployment and the real-agent benchmark remain outstanding. No hosted endpoint or
published data release is advertised.

## Why downloads plus live adapters

Measured local indexes make repeated discovery and search inexpensive, while retaining
exact archive/member bytes supports reproducible source inspection. Downloads are not
field-equivalent to the API or website: richer records and export gaps need explicit
live adapters. An unavailable source is reported as a limitation, not as complete
coverage or evidence of absence.

The recorded experiments and maintenance trade-offs are in the
[API/download decision](docs/research/api-vs-download-decision.md),
[representation comparisons](docs/research/representation-comparison.json) and
[website gap audit](docs/research/website-gap-audit.md). These are dated observations,
not guarantees of current upstream availability.

## Local development

Use Python 3.12+ and uv with the committed lockfile:

```bash
uv sync --frozen --group dev
uv run --frozen pre-commit install
make ci-local
uv run --frozen clinpgx-link serve --host 127.0.0.1 --port 8000 --cache-root ./data/cache
```

The server exposes HTTP MCP at `http://127.0.0.1:8000/mcp`, readiness at `/health`
and liveness at `/api/live`. There is no stdio transport. In another terminal:

```bash
uv run --frozen clinpgx-link health --url http://127.0.0.1:8000
```

The development command does **not** download or install a snapshot. Without one,
local dataset tools are unavailable and development health is degraded. An existing
builder-produced database can be selected using `CLINPGX_SNAPSHOT_PATH`; the default
is `/data/current/clinpgx.sqlite`. A running process pins its opened snapshot until
restart. Production deployment is not ready for use while the verified installer and
full release-identity startup checks are unfinished.

The implemented operator commands are `serve`, `config`, `health` and `version`.
The planned data commands are described in the release design, not yet available CLI
commands. `clinpgx-link config` prints validated, non-secret configuration.

## MCP tools

| Area | Tools |
|---|---|
| Discovery and status | `get_server_capabilities`, `get_diagnostics`, `get_api_schema` |
| Entity evidence | `search_records`, `get_record`, `get_related_records` |
| Installed datasets | `list_datasets`, `get_dataset`, `search_dataset`, `get_dataset_record` |
| Live sources | `get_api_data`, `get_website_data` |
| Retained source bytes/content | `get_source_content` |

Use discovery/schema tools for supported selectors and operations. Explicit source
selection does not silently switch providers. Local literature joins return original
citing evidence rows and PMIDs, not full bibliographic metadata; live API detail is
the richer-data path. Source payloads remain untrusted evidence, never instructions.

## Verification and contributor guide

`make ci-local` checks formatting, lint, module-size budgets, the pinned fleet schema,
strict typing, non-integration unit tests and FastMCP imports. Tests use local fixtures;
passing them does not establish current upstream completeness. The frozen
[18-case agent benchmark](tests/eval/cases.json) still requires an actual agent run.

Read [AGENTS.md](AGENTS.md), the binding
[source-access contract](docs/superpowers/specs/2026-09-05-source-access-contract.md),
[implementation plan](docs/superpowers/plans/2026-09-05-clinpgx-link.md),
[release design](docs/research/data-release-design.md) and
[hygiene status](docs/research/repo-hygiene.md) before extending the server.
Release activation and publication are operator responsibilities, never MCP tools.
