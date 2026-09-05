# ClinPGx architecture decision inputs

Research checkpoint, 2026-09-05. This records design reasoning, not completion.

## Options

| Approach | Strengths | Costs and coverage limitations |
|---|---|---|
| API only | Current upstream records; minimal setup; rich relationships and reports | Two requests/second; no pagination declared by current OpenAPI; availability and schema changes affect every uncached query; expensive repeated broad searches |
| Downloads only | Fast repeatable local queries; explicit snapshot versions; low request volume | Monthly freshness; export shapes differ from API; reports and rich record fields need a coverage audit; download/import/storage operations required |
| Hybrid, with explicit source identity | Local full-dataset search plus current targeted API detail; low upstream traffic; complete discovery of API and export surfaces | More implementation and tests; sources cannot be silently substituted because their schemas and freshness differ |

Provisional candidate: hybrid, pending actual network and indexing benchmarks.
The user explicitly requires testing both options before deciding. Source selection
must be explicit in results, not inferred
from latency. A monthly TSV row is not interchangeable with a maximal API record.
Dedicated dataset tools expose exact export rows, while API tools expose upstream
objects. Convenient record tools may use an API response cache without claiming
that a partial TSV export is the full API representation.

## Comprehensiveness

Use the official OpenAPI as a bounded operation and parameter registry. Cover all
documented read operations through schema discovery and validated operation calls,
alongside focused gene/drug/variant and evidence traversal tools. Reject unknown
parameters rather than silently ignoring them. Do not expose arbitrary URLs.

Use the live public download-file registry as the discovery source. Preserve source
URL, publication date, size, licensing category, and a content digest when acquired.
Index tabular and JSON members without discarding unrecognized columns. Inventory
other members explicitly and expose source artifacts; never report an unparsed
member as indexed. Retain old snapshot identity during pagination and reject a
cursor when its underlying snapshot is no longer available.

## Performance experiment

Measure acquisition and build separately from query latency. Compare matched
gene/drug/variant lookups and evidence queries using live API, warm API cache,
and indexed exports. Report exact query semantics, returned identifiers, source
dates, and any field/coverage differences; superficially similar queries are not
proof of equivalent answers. No absolute performance promise before measurement.

## Implementation boundaries

1. Bounded upstream HTTP client and OpenAPI operation registry.
2. Download catalog, streaming acquisition, atomic index builder and read-only store.
3. Data services returning plain results and raising typed errors.
4. MCP boundary owning schema, envelope, fencing, pagination, errors and discovery.
5. FastAPI/Typer lifecycle, local HTTP server and hardened deployment artifacts.
6. Deterministic regression tests plus live local-MCP and real-agent benchmarks.

Independent components will be assigned distinct files and explicit interfaces.
The spec and detailed plan will receive an actual Fable review before production
implementation. Local testing and deployment preparation are in scope; publishing
a remote repository, changing production router configuration, and deploying to
the fleet are not prerequisites for proving this local implementation works.
