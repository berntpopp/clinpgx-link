# Vendored conformance verification

Verified on 2026-09-05 at implementation commit
`d5accf9f98a9d8e9866dc845d6d6e478224e8701`.

The five captured probe files introduced in `c0b8e08` are unchanged copies of
GeneFoundry Router commit `6568a0ad7d68925440aba550a2a678282ea5bb6b`.
Both manifest digest checks and an explicit comparison with that sibling checkout
passed:

```text
make vendor-check GENEFOUNDRY_ROUTER_DIR=../genefoundry-router
vendor-check: digest verified; pinned router bytes verified
vendor-check: conformance digests verified; pinned router bytes verified
```

## Actual local-server run

A temporary snapshot was built from the four sourced fixture archives using
`tests.unit.test_repository._repository`. A real Uvicorn server ran the production
ASGI application in development mode on an ephemeral loopback TCP port, using that
snapshot, a temporary cache, and the normal live API/website adapters.

The two unmodified vendored pytest wrappers ran in a separate process with
`CONFORMANCE_MCP_URL` set to the server's base URL, `CONFORMANCE_NAME=clinpgx-link`,
and `CONFORMANCE_TIER=stateless`:

```text
python -m pytest tests/conformance/test_transport_v1.py tests/conformance/test_behaviour_v1.py -q
2 passed in 6.34s
probe_exit=0
clean_shutdown=true
```

The harness enforced a 10-second startup deadline and a 240-second probe-process
deadline, then shut down the server and removed only its temporary fixture/cache
directory. No container was involved. Unlike ordinary no-URL collection, neither
wrapper was skipped.

## Foundation checks and limits

Fresh `make ci-local` at the same commit passed format, lint, package file-size,
both vendor digest checks, strict mypy over 46 source modules, and the FastMCP
import check. Unit results: **574 passed**, with two third-party deprecation
warnings from the Starlette test-client stack.

Passing these wrappers does not establish exhaustive data coverage, production
release installation, container acceptance, or the separate 18-case real-agent
benchmark. The behavior probe permits explicitly inconclusive checks; the detailed
earlier run's five inconclusive checks remain recorded in
[the behavior review](behaviour-probe-round1.md). No stronger completeness claim
is inferred from this wrapper run.

Downloader failure-path review was still open at this checkpoint. Passing unit
tests does not supersede that review.
