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

The public server now registers eight of the thirteen specified tools. Dataset
record search/retrieval and entity/relationship tools remain incomplete. The
identity check still validates the repository snapshot identity, not the future
trusted release-manifest/runtime identity. Remote CI and release acceptance have
not been run.
