# ClinPGx MCP requirement and evidence ledger

Started 2026-09-05. The initial workspace was empty and was not a Git repository.
No prior implementation, test result, or review evidence was present to reuse.

## Objective

Build clinpgx-link end to end using GeneFoundry fleet standards and stack, expose
ClinPGx data comprehensively, compare API and downloads with measured evidence,
review the specification and plan using Fable 5 through Claude Code, implement
with parallel agents, and benchmark typical research questions through a local
MCP server and a real agent.

## Completion evidence required

| Requirement | Authoritative evidence | Current state |
|---|---|---|
| Deep fleet review | Source-referenced review of router and representative API/mirror backends | Reviewed `fleet.md`, current standards and sampled implementation |
| API versus download decision | Official inventory, live probes, timings, coverage and operational comparison | 45 timed HTTP transfers; actual 15-archive SQLite experiment; representation comparison; independent methodology review |
| Comprehensive data access | Dataset/endpoint inventory mapped to working tools; explicit inaccessible-source limitations | Not implemented |
| Architectural specification | `docs/superpowers/specs/2026-09-05-clinpgx-link-design.md` and binding source-access addendum | Written; round-one corrections incorporated |
| Parallel implementation plan | `docs/superpowers/plans/2026-09-05-clinpgx-link.md` with interfaces and verification | Six tasks written; shared signatures and acceptance corrected |
| Adversarial spec/plan review | Review output, model identity, addressed findings, spec/plan hashes | Independent gpt-6-astra CLI review completed, REVISE with 10 findings; fixes being checked; Fable quota failure preserved |
| Fleet stack | Python 3.12+, uv lock, FastAPI, FastMCP 3.x, Pydantic 2, httpx, CLI, structured logging | Not implemented |
| Fleet response contract | Real MCP results: result/results, metadata, six error codes, isError, pagination, provenance, fenced prose | Not implemented |
| Fleet transport/security | Local HTTP shared conformance probes; real HTTP MCP client; Origin/Host and outbound policy tests | Not implemented |
| Data ingestion and freshness | Actual exports imported, deterministic fixture tests, atomic refresh, source checksums/dates and coverage | Not implemented |
| Data releases (explicit follow-up) | Reproducible release CLI/workflow, versioned manifest, per-source dates/licenses/checksums, verified install and rollback tests | Design research in progress |
| Website/API/export parity (explicit follow-up) | Verified feature/field coverage matrix; working fallback for every uncovered public website/API data family; regression tests and honest gaps | Research in progress |
| Deployment artifacts | Hardened Docker/Compose, code-only image, health and runtime evidence, CI/release configuration | Not implemented |
| Effective testing | Unit, integration, contract, type and lint results with meaningful assertions | Not implemented |
| Typical agent questions | Local MCP tool traces, source-grounded answers, benchmark rubric and measured latency/token/error results | Not implemented |
| Complete handoff | Runnable instructions, tools table, data licenses/citations, configuration and remaining limitations | Not implemented |

## Confirmed constraints

- ClinPGx API documents a maximum of two requests per second and warns that
  parameters/responses can change: <https://api.clinpgx.org/>.
- The former api.pharmgkb.org hostname was scheduled for shutdown on 2026-07-20;
  use api.clinpgx.org.
- GeneFoundry defines a flat response envelope and structured errors, compact
  default responses, typed pagination/completeness, citations and research-use flags.
- External prose must use the v1.1 untrusted_text shape on the wire, including a
  digest of raw content and retrieval provenance; upstream error bodies must not leak.
- Fleet HTTP transport is stateless JSON at /mcp without redirects, with /health.
- The Logging & CLI standard prescribes a package Typer entrypoint and HTTP-only
  serving; the local agent benchmark will therefore use HTTP rather than stdio.
- Fleet input schemas require descriptions, examples, declared enums and bounds;
  tool definitions are bounded to 1,200 tokens each and 10,000 per backend.
- Source references are the current standards in ../genefoundry-router/docs and
  implemented sibling code; historical adoption tables are not proof of current behavior.

## Authorization and review setup

The user requested end-to-end implementation and parallel work explicitly.
Research agents use gpt-5.6-sol and gpt-5.6-luna. No sibling changes or external
publication have been made. Claude Code's authenticated `fable` alias answered a
read-only availability probe and reported canonical model `claude-fable-5-1`.
This is availability evidence only, not an adversarial review.
The actual Fable review was rejected before reading any input with a usage-limit
error. The user subsequently authorized an adversarial review using this agent's
own model or AGY 3.8 Flash. An independent GPT-6 reviewer will satisfy that amended
requirement; this is not represented as a completed Fable review.

## Goal-turn classification

Previous turn: progress. Created `berntpopp/clinpgx-link` on GitHub at the user's
explicit request, verified visibility PRIVATE, and configured local origin. No files
were pushed. Earlier work produced measured source experiments, website/auxiliary
format probes, independent review and concrete spec/plan corrections. Implementation
remains outstanding; neither repository creation nor planning completes the goal.
