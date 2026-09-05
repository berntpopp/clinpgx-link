# Runtime snapshot integration review

Scope: `server_manager.py`, MCP facade/guard registration and their HTTP tests.
This is not approval of all Task 4 tools or Task 5 release identity validation.

Independent reviewer: existing `clinpgx_research` agent, GPT-5.6 Sol.

Initial findings:

1. Rejected production snapshot admission still permitted API/site calls, cached
   content retrieval and diagnostic probes. **Addressed:** the MCP guard blocks
   every nonmetadata tool when production admission fails. Diagnostic probes have
   no source adapters in that state. Liveness and metadata remain available.
2. Opening the repository in the app factory pinned it before startup and could
   leak a handle if startup failed. **Addressed:** admission, MCP construction and
   the mounted MCP lifespan now run inside the host lifespan, with cleanup on
   failure and shutdown. Health and MCP share that single handle.

The reviewer inspected the revised diff and approved both spec compliance and
quality for these findings. Nonblocking follow-up: distinguish missing, malformed,
mismatched and inaccessible snapshots with fixed safe diagnostic reasons. They
currently all appear as an unconfigured local repository.

Root verification on 2026-09-05:

- RED: missing HTTP repository wiring failed the pinned-identity and shutdown tests.
- RED: the rejected-production cached-content test unexpectedly succeeded before
  the source-access guard was added.
- RED: replacing a snapshot between app construction and lifespan startup returned
  the old identity before admission moved into lifespan.
- GREEN: 27 HTTP tests, including startup failure cleanup, zero upstream sends
  when unready, snapshot replacement/restart, and catalog → member → exact bytes.
- Combined HTTP/catalog/asset checks: 38 passed. Two third-party TestClient
  deprecation warnings remain; they were not suppressed.
- Strict typing, Ruff and all tracked-file pre-commit checks passed.
- Actual loopback TCP smoke after commit `0c6a930`: Uvicorn started, a FastMCP
  HTTP client listed eight tools and followed `list_datasets` → `get_dataset` →
  `get_source_content`. The returned base64 reconstructed the sourced `genes.tsv`
  fixture byte-for-byte, with matching structured/text envelopes and
  `offline_available=true`. Snapshot identity was
  `sha256:2035f90648774e41a0f260563c7852b2a61e3855107a8f753599077507c1d911`.
  The server shut down cleanly. This fixture smoke is not the real-agent benchmark.

At the reviewed revision, the public server registered eight of thirteen tools.
Commit `f9522bb` subsequently wires all thirteen. The expanded HTTP fixture test
also exercises dataset pagination, selected record fields, entity search/detail,
exact advertised field filtering, and related evidence. Before that commit,
`make ci-local` passed 442 tests, Ruff, strict mypy, module budgets and vendored
schema verification. This is integration evidence, not independent approval of
every tool or the real-agent benchmark.

An actual loopback TCP smoke at `1a15266` used Uvicorn and the FastMCP HTTP client:
all thirteen tools were discovered; `search_records` resolved CYP2C19 to PA124;
`get_dataset` → `get_source_content` reconstructed the original gene TSV bytes;
and `search_dataset` using the advertised annotation ID field →
`get_related_records` returned the expected single evidence row. Structured and
text envelopes agreed and shutdown completed. The snapshot identity was the same
fixture identity recorded above. The first smoke invocation used an incorrect
harness field (`asset_ref` instead of `content_ref`) and failed; correcting the
harness to the documented contract produced exit status 0. No production change
was needed for that harness error. This remains fixture evidence, not agent-benchmark
or current-upstream-coverage acceptance.

The identity check still validates the repository snapshot identity, not the
future trusted release-manifest/runtime identity. Remote CI and release acceptance
have not been run.
