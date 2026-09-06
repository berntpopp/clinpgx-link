# clinpgx-link

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![CI](https://github.com/berntpopp/clinpgx-link/actions/workflows/ci.yml/badge.svg)](https://github.com/berntpopp/clinpgx-link/actions/workflows/ci.yml)
[![Conformance](https://github.com/berntpopp/clinpgx-link/actions/workflows/conformance.yml/badge.svg)](https://github.com/berntpopp/clinpgx-link/actions/workflows/conformance.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An MCP server over the [ClinPGx](https://clinpgx.org/) resource (aggregating CPIC,
PharmGKB, and PharmCAT evidence), serving curated gene-drug interactions, clinical
guidelines, dosing recommendations, and variant annotations to AI assistants over
Streamable HTTP. It also serves REST health and diagnostics endpoints from the same process.

> [!IMPORTANT]
> Research use only. Not clinical decision support. Do not use for diagnosis,
> treatment, triage, or patient management.

## Why

ClinPGx aggregates vital pharmacogenomics knowledge, but its upstream data surfaces are
split across a live REST API with specific query parameter requirements, curated bulk
dataset downloads (~120 datasets across genes, drugs, variants, guidelines, and
annotations) that are too large for an agent to re-fetch on every query, and web-only
reference paths for CPIC guideline attachments and publications.

Worse, raw upstream responses contain deep nested records and unstructured text that
consume model context, while unthrottled queries easily exceed upstream capacity.

`clinpgx-link` solves this in one unified service:
- **Local SQLite snapshots** index dataset rows with fast, deterministic search so agents
  discover gene-drug relationships without burning network bandwidth.
- **A retained content store** caches complete source bodies under immutable references
  (`content:<sha256>`), allowing models to inspect JSON pointers or slice large payloads
  progressively without refetching.
- **Live adapters** safely reach fresh ClinPGx and CPIC API records and verified website
  routes when richer real-time details or guideline attachments are needed.
- **Admission control and token-bucket rate limiting** protect upstream services (2 req/s)
  and isolate local capacity from upstream latency.
- **Model-oriented output** shapes responses in compact/minimal/standard/full modes, stamps
  exact `_meta` timing, suggests next-step commands, and fences untrusted source text.

## Quick start

Hosted — nothing to install, no data to build:

```bash
claude mcp add --transport http clinpgx-link https://clinpgx-link.genefoundry.org/mcp
```

Run it yourself (Python 3.12+, [uv](https://github.com/astral-sh/uv)):

```bash
uv sync --group dev
make dev                                    # unified REST + MCP on 127.0.0.1:8000
curl http://127.0.0.1:8000/health
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. Streamable HTTP is the only transport —
there is no stdio transport. See [deployment.md](docs/deployment.md).

## Tools

| Tool | Purpose |
|------|---------|
| `get_server_capabilities` | Discover tools, source limits, detail identifier contracts, and relationship capabilities |
| `get_diagnostics` | Inspect source configuration, snapshot status, worker admission, and cache telemetry |
| `get_api_schema` | Discover allowlisted upstream API routes, parameters, and operation contracts |
| `search_records` | Unified entity search across genes, drugs, guidelines, and variants (live API or local dataset) |
| `get_record` | Retrieve a single exact entity by identifier with source-anchored provenance |
| `get_related_records` | Query joined and related entities (e.g. gene-drug guidelines, variant annotations) |
| `list_datasets` | List curated snapshot datasets available in the installed catalog |
| `get_dataset` | Inspect an installed dataset's metadata, schema, and member files |
| `search_dataset` | Search indexed rows within a snapshot dataset with structured filtering |
| `get_dataset_record` | Retrieve a single indexed row by record ID with optional JSON pointer projection |
| `get_api_data` | Direct bounded read of allowlisted ClinPGx and CPIC REST API operations |
| `get_website_data` | Retrieve verified structured records from ClinPGx and CPIC web routes |
| `get_source_content` | Retrieve retained raw or structured source bytes by immutable content reference |

**Namespace.** Leaf tool names are unprefixed, per Tool-Naming Standard v1. Behind the
[`genefoundry-router`](https://github.com/berntpopp/genefoundry-router) gateway, which
mounts this server with `mount(namespace="clinpgx")`, they surface as `clinpgx_<tool>` —
`search_records` becomes `clinpgx_search_records`. A leaf-level `clinpgx_` prefix
would double-prefix to `clinpgx_clinpgx_…`, so do not add one.

## Data & provenance

| | |
|---|---|
| **Source** | [ClinPGx](https://clinpgx.org/) and CPIC REST APIs (`https://api.clinpgx.org/v1`, `https://api.cpicpgx.org/v1`), website routes, and S3 bulk downloads (`https://s3.pgkb.org`). Public, no authentication required |
| **Datasets** | 120 captured catalog entries across genes, drugs, guidelines, variants, and annotations indexed in an immutable SQLite repository with retained archive and member bytes |
| **Provenance** | Every tool carries measured `_meta` timing (`_meta.elapsed_ms` at `_meta.timing_scope="tool_boundary"`), source URLs, SHA-256 digests, and snapshot identities. Source text is strictly fenced as untrusted data ([data.md](docs/data.md)) |
| **Refresh** | Calls proxy the live API or local SQLite snapshot behind a wall-clock TTL content cache; freshness tracks upstream releases |
| **Rate limit** | Outbound token bucket (2 req/s) with active admission pool (16 concurrent calls) to prevent overwhelming upstream servers |
| **Data licence** | Public ClinPGx and CPIC source data. Research use only |

Required citation:

> Whirl-Carrillo M, et al. Pharmacogenomics Knowledge for Personalized Medicine: 2021 Update. Clin Pharmacol Ther. 2021;110(4):883-891. doi:10.1002/cpt.2356

More: [data.md](docs/data.md).

## Documentation

- [Data & provenance](docs/data.md) — datasets, cache model, licence, citation, response modes, and timing contracts.
- [Configuration](docs/configuration.md) — every `CLINPGX_*` variable and the Host/Origin request boundary.
- [Deployment](docs/deployment.md) — transports, the CLI, Docker and Compose overlays, and health checks.
- [Architecture](docs/architecture.md) — layering, domain services, content store, and untrusted text fencing.
- [Conventions](docs/conventions.md) — code style, module line budget, error taxonomy, and testing gates.
- [AGENTS.md](AGENTS.md) — engineering conventions for humans and coding agents.
- [SECURITY.md](SECURITY.md) — the trust boundary and how to report a vulnerability.
- [CHANGELOG.md](CHANGELOG.md) — release history.

## Contributing

Read [AGENTS.md](AGENTS.md) first — it is the engineering guide. `make ci-local` is the
definition-of-done gate: format, lint, line budget, README standard, type check, and
tests. It must be green before handoff.

## License

Code: [MIT](LICENSE). Data: ClinPGx public data remain subject to upstream terms and
carry the citation requirement above.
