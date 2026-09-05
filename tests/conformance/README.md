# Fleet conformance probes

These five Python files are byte-identical copies of `docs/conformance/` from
`berntpopp/genefoundry-router` commit
`6568a0ad7d68925440aba550a2a678282ea5bb6b`. Their immutable source/destination
inventory and SHA-256 digests are recorded in
`vendor/genefoundry/CONFORMANCE_SHA256`. The upstream MIT license is retained at
`vendor/genefoundry/LICENSE`.

The wrappers target an already-running server. They do not start a container:

```bash
CONFORMANCE_MCP_URL=http://127.0.0.1:8000 \
CONFORMANCE_NAME=clinpgx-link \
CONFORMANCE_TIER=stateless \
uv run pytest tests/conformance/test_transport_v1.py tests/conformance/test_behaviour_v1.py -q
```

Without `CONFORMANCE_MCP_URL`, pytest collects both wrappers and reports them as
skipped. That skip is intentional for ordinary local unit runs; it is not a passing
live conformance result.

These are the fleet transport and schema-derived behavior probes. They are not the
container acceptance suite and not the separate 18-case agent benchmark.

Run `make vendor-check` to validate local captured bytes. To additionally require
the exact pinned router checkout and source-byte parity, run:

```bash
make vendor-check GENEFOUNDRY_ROUTER_DIR=../genefoundry-router
```
