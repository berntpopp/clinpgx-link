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

Every HTTP MCP tool success and error now reports measured `_meta.elapsed_ms` and
`_meta.timing_scope="tool_boundary"`, identically in text and structured content.
Timing starts at tool-boundary receipt, includes validation, admission and result
construction, and excludes HTTP serialization and network transit. A measured
submillisecond duration can round to zero. Standalone envelope constructors have no
boundary timer and report timing as unavailable unless a caller supplies a measured
tool-body duration; constructor defaults must not be interpreted as execution time.

`CLINPGX_MAX_ACTIVE_CALLS` defaults to 16 and accepts 2–128 per process. Half the
capacity, rounded up, is reserved for local metadata, installed datasets and retained
content; the remainder is reserved for upstream work. Pools never borrow. Validated
source contracts determine the pool; uncertain automatic routes reserve upstream.
`get_diagnostics(probe_upstream=true)` uses upstream capacity. Excess requests are
rejected promptly with `rate_limited/admission_capacity` and a fixed one-second retry
interval. Upstream HTTP throttling uses `rate_limited/upstream_throttle` with a fixed
30-second retry interval. Normal outbound scheduler waiting is part of active
upstream work and preserves the existing maximum of two outbound requests per second.

Active tool work has a 60-second deadline, including outbound scheduler waiting.
Expiry reports `upstream_unavailable/execution_deadline`, which describes execution
expiry and does not establish an upstream outage. Configure clients to allow more
than 60 seconds plus transport overhead if they need to receive this response.
Cancelling a client await alone may not notify the server; closing the HTTP request
is observed as a disconnect. Neither cancellation nor a deadline can forcibly kill
an arbitrary Python worker thread. Its slot remains occupied until the worker
finishes; repository lock waits and SQLite execution cooperate with the deadline.
Diagnostics and health expose pending termination and degrade readiness while such
work remains. A worker that cannot terminate requires completion or a supervised
process restart. A cancelled call receives no cancellation-status envelope.
Graceful shutdown drains admitted work before closing the repository or retained
content store; it can therefore wait for a worker that has not terminated.

Local free-text `query` uses literal ANDed tokens: ASCII `*` is rejected in this
field. Exact `gene` and `name` filters preserve star-allele spellings such as
`CYP2C19*2`; other query punctuation still separates tokens. Exact zero-result
diagnostics may be unavailable when their bounded work budget expires. Their
observed examples are distinct stored values, never aliases or equivalence claims.

The agent benchmark records reported boundary duration and known local/cache/live
source classification separately from success/error status. Claude completion traces
do not establish precise monotonic send/result times or scheduler/execution spans;
these remain `null` with an explicit reason, rather than reconstructed timestamps.

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
