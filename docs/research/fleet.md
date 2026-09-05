# GeneFoundry fleet research for `clinpgx-link`

Status: implementation input, based on the current sibling repositories in
`/home/bernt-popp/development` and the router-owned fleet standards. No sibling repository was
modified.

## Recommendation

Start from the **current `clingen-link` HTTP/MCP shell**, but take only the infrastructure and
contracts, not its domain model. It is the most complete current exemplar for the FastMCP facade,
outer ASGI guards, HTTP-only CLI, data identity, code-only image, compose hardening, and reusable
router release workflows. Choose the data plane independently:

- If ClinPGx data can be redistributed as an immutable release artifact, use the `clingen-link`
  code-image plus digest-verified external-data pattern. This is the strongest production
  baseline.
- If ClinPGx must be queried live, keep the `clingen-link` MCP/server shell but use a thin
  `gnomad-link`/`panelapp-link`-style API client and set the release manifest to `data.mode=none`
  with data-independent tool definitions.
- Add a local mirror plus live fallback only if product requirements require it. `mavedb-link` is
  the nearest hybrid precedent, but it adds cache/state/failure modes that are unnecessary for a
  first implementation.

Do not copy older `stdio` entry points from `hgnc-link`, `panelapp-link`, or older documentation.
The current fleet contract and current `gnomad-link` CLI are HTTP-only.

## Reusable current baseline

Copy the *shape* of these `clingen-link` files and rename all product/domain identifiers:

| Concern | Baseline | Why it is the current useful exemplar |
| --- | --- | --- |
| Project metadata and quality gates | `../clingen-link/pyproject.toml:1-166`, `../clingen-link/Makefile:10-136` | Python 3.12+, hatchling, uv lock, strict Ruff/mypy, pytest, `ci-local`, CLI entry point |
| CLI | `../clingen-link/clingen_link/cli.py:1-67` | Typer app, `serve`, `config`, `health`, `version`; HTTP-only |
| Settings | `../clingen-link/clingen_link/config.py` | Pydantic settings, host/origin allowlists, data paths, upstream controls |
| FastMCP facade | `../clingen-link/clingen_link/mcp/facade.py:59-98` | masked errors, schema dereferencing disabled, guard/wrapper ordering, tool registration |
| Unified ASGI host | `../clingen-link/clingen_link/server_manager.py:109-179`, `:205-265` | outer Host/Origin guard, correlation IDs, CORS, `/health`, root-mounted `/mcp`, lifespan |
| Tool declaration | `../clingen-link/clingen_link/mcp/tools/genes.py:34-38`, `:74-131` | annotations, parameter descriptions/examples, compact-mode input, plain-dict service result |
| Error boundary | `../clingen-link/clingen_link/mcp/errors.py:35-53`, `:416-459`, `:501-545` | closed error taxonomy, safe error mapping, MCP `isError`, no query/PII logging |
| Unknown-name protection | `../clingen-link/clingen_link/mcp/notfound_guard.py:16-39`, `:59-168`, `:211-350` | fixed name-free errors plus middleware/protocol/log reflection defenses |
| Untrusted prose | `../clingen-link/clingen_link/mcp/untrusted_content.py:12-120` | typed provenance wrapper, byte/object limits, safe message sanitization |
| Data identity | `../clingen-link/clingen_link/runtime_data_identity.py`, `../clingen-link/clingen_link/server_manager.py:140-179` | expected/actual identity check and fail-closed health |
| Container | `../clingen-link/docker/Dockerfile:7-36`, `:69-134` | digest-pinned multi-stage build, fixed non-root UID, stripped code-only scratch runtime |
| NPM compose | `../clingen-link/docker/docker-compose.npm.yml:1-142` | offline init, digest verification, read-only app, no published port, resource/log limits |
| Release truth | `../clingen-link/container-release.json:1-56` | external immutable artifact, schema compatibility, runtime identity, deployed compose set |
| Conformance CI | `../clingen-link/.github/workflows/conformance.yml:1-58` | boots the real container and runs vendored transport and behaviour probes |
| Shared container release | `../clingen-link/.github/workflows/container-release.yml:1-20` | thin, pinned call into the router-owned reusable workflow |

Suggested package skeleton:

```text
clinpgx_link/
  __init__.py                 # __version__ from importlib.metadata
  cli.py config.py logging_config.py server_manager.py exceptions.py
  api/                        # only if a live upstream is required
  etl/                        # only if building a local artifact
  store/                      # read-only queries over a local artifact
  services/                   # domain operations; no MCP envelopes
  models/
  mcp/
    annotations.py envelope.py errors.py facade.py filters.py
    next_commands.py notfound_guard.py output_validation.py
    resources.py shaping.py untrusted_content.py
    tools/
tests/{unit,integration,conformance}/
docker/{Dockerfile,docker-compose.yml,docker-compose.prod.yml,docker-compose.npm.yml}
.github/workflows/{ci,conformance,container-ci,container-release,container-security,release,security}.yml
container-release.json
pyproject.toml uv.lock Makefile README.md CITATION.cff LICENSE
```

Keep a strict two-plane boundary: API/store/service functions return typed domain values or raise
typed domain exceptions; only `mcp/` constructs MCP response envelopes and `CallToolResult` errors.
This organization is explicit in `../clingen-link/AGENTS.md:6-44` and is visible in
`../clingen-link/clingen_link/mcp/tools/genes.py:74-131`.

## Mandatory implementation contracts

### Stack and repository shape

- Python `>=3.12`, hatchling, a committed `uv.lock`, and `uv sync --frozen` in CI/containers.
- FastMCP 3.x, MCP Python SDK, FastAPI, Uvicorn, Pydantic v2/settings, httpx, structlog, and
  `asgi-correlation-id`; add `async-lru`, zstandard, or Prometheus only when the chosen data plane
  needs them. Current dependency evidence is `../clingen-link/pyproject.toml:1-67`.
- Use the single `<package>.cli:app` Typer entry point. Required operator surface is `serve`,
  `config`, `health`, and `version`; data-bearing servers add an explicit refresh/build command.
  The fleet CLI decision is `../genefoundry-router/docs/specs/2026-06-13-fleet-logging-cli-standard-design.md:37-72`.
- Use structured logging on stderr. Never emit PII, query values, raw upstream URLs/bodies, bearer
  tokens, or unknown MCP names. Carry/generate a request ID.
- The definition of done is `make ci-local`; current `clingen-link` runs formatting/lint, strict
  typing, unit tests, conformance/contract truth, and container checks
  (`../clingen-link/AGENTS.md:138-175`, `../clingen-link/Makefile:10-96`).
- CI must cover Python 3.12 and the current runtime line (currently 3.14), use pinned actions and
  pinned uv, and enforce coverage (`../clingen-link/.github/workflows/ci.yml:18-64`).

### MCP transport and health

The normative source is `../genefoundry-router/docs/MCP-TRANSPORT-STANDARD-v1.md`:

- Expose Streamable HTTP at **exactly `/mcp`**. A direct `POST /mcp` must return an MCP response,
  never a redirect (`:37-55`). There is no stdio production mode.
- Construct FastMCP with `stateless_http=True` and `json_response=True`; successful traffic must
  not depend on `Mcp-Session-Id` (`:57-62`).
- Support normal MCP protocol negotiation and return a valid protocol error for unsupported
  versions; honor compatible `Accept` headers (`:64-77`).
- Configure the FastMCP identity as `name="clinpgx-link"`, `version=__version__`, with a SemVer
  version coming from the package's single version source (`:88-96` and
  `../genefoundry-router/docs/VERSIONING-STANDARD-v1.md:21-62`).
- Expose unauthenticated `GET /health`, returning HTTP 200 when ready and at least `status`,
  `version`, and `transport` (`../genefoundry-router/docs/MCP-TRANSPORT-STANDARD-v1.md:100-125`).
  If an expected data identity is configured, readiness must fail closed on missing or mismatched
  artifacts, as in `../clingen-link/clingen_link/server_manager.py:140-179`.
- Put `/health` on the outer FastAPI app, then mount the FastMCP app at root so FastMCP owns `/mcp`.
  Apply Host/Origin protection at both the outer ASGI layer and FastMCP native layer.

The vendored `tests/conformance/test_transport_v1.py` must come from the router's canonical source,
not be independently rewritten. The required assertions are enumerated in the transport standard
at `:164-220`.

### Tool names, descriptions, schemas, and discovery

- Leaf tools are unprefixed and named `verb_noun`; the router adds the `clinpgx_` namespace.
  Use canonical verbs and canonical parameter names (`gene_symbol`, `variant_id`, `transcript_id`,
  `pmid`, `response_mode`, `limit`, `offset`) where applicable. Names are at most 50 characters.
  See `../genefoundry-router/docs/TOOL-NAMING-STANDARD-v1.md:11-45`.
- Every input property has a useful description; every required property and array has examples;
  closed vocabularies use enums/Literals; bounds and patterns must be truthful. Evidence:
  `../genefoundry-router/docs/TOOL-SCHEMA-DOCUMENTATION-STANDARD-v1.md:45-115`.
- Annotate read-only tools with `readOnlyHint=true`, `destructiveHint=false`,
  `idempotentHint=true`. Use `openWorldHint=false` only for a wholly local immutable snapshot;
  tools that can call a live service are open-world. Current constants are in
  `../clingen-link/clingen_link/mcp/annotations.py:7-19`.
- Keep schemas small: maximum 1,200 serialized schema tokens per tool and 10,000 per server.
  First suppress generated `outputSchema` (`output_schema=None`), preserve the response envelope
  as a plain `dict`, and set `dereference_schemas=False`; do not shorten useful descriptions.
  This later rule supersedes the older response-standard sentence requiring output schemas.
  See `../genefoundry-router/docs/TOOL-SURFACE-BUDGET-STANDARD-v1.md:46-112` and the current use in
  `../clingen-link/clingen_link/mcp/tools/genes.py:34-38`.
- Provide `get_server_capabilities` and stable resources documenting capabilities, workflows,
  provenance/freshness, and citations. Treat tool names declared in capabilities and names
  registered by FastMCP as a tested equality. Every `next_commands` suggestion must name an
  actually registered tool and provide valid argument keys.

### Response and error boundary

The intended wire contract is `../genefoundry-router/docs/RESPONSE-ENVELOPE-STANDARD-v1.md`:

- Every tool returns `structuredContent` as an object and one `TextContent` item containing the
  JSON serialization of the same object (`:46-80`). Do not nest an extra envelope.
- Success includes `success: true`, a primary `result` or `results`, and `_meta`. Omit null/empty
  optional sections. `response_mode` is a closed enum (`minimal|compact|standard|full`), defaults
  to compact, and only changes representation, never query semantics (`:117-137`).
- Errors are normal MCP tool results with `isError=true` and a flat object containing
  `success:false`, `error_code`, `message`, `retryable`, `recovery_action`, and `_meta`; there is no
  nested `error` object (`:82-115`).
- The closed error taxonomy is exactly `invalid_input`, `not_found`, `ambiguous_query`,
  `upstream_unavailable`, `rate_limited`, and `internal` (`:102-104`; also the executable set in
  `../genefoundry-router/docs/conformance/behaviour.py:52-61`). Do not copy stale sibling text that
  says `data_unavailable` or `internal_error`.
- `_meta.request_id` and `_meta.elapsed_ms` are mandatory. Include source/provenance, data version
  or release identity, pagination, freshness/staleness, filtering, and truncation where applicable
  (`../genefoundry-router/docs/RESPONSE-ENVELOPE-STANDARD-v1.md:139-165`).
- Pagination must be honest: `limit`/`offset` plus opaque cursor when supported, correct
  `total_count`, `has_more`, and `next_cursor`; enforce a 25,000-token response cap with explicit
  truncation metadata (`:129-185`).
- Every biomedical result needs a recommended citation and an explicit
  `unsafe_for_clinical_use: true` safety marker (`:187-199`).

Use one wrapper such as `run_mcp_tool` for timing, exception mapping, validation, JSON mirroring,
and `isError`. Domain tools should only validate typed inputs, call a service, shape the chosen
response mode, and supply provenance/next steps. `../clingen-link/clingen_link/mcp/errors.py:501-545`
is the closest implementation skeleton, but add the normative request ID and elapsed time rather
than cloning it unchanged.

### Behaviour conformance

Vendor the router's canonical `docs/conformance/behaviour.py` and wrap it with
`tests/conformance/test_behaviour_v1.py`. The probe discovers live tools/schemas and uses declared
examples (`../genefoundry-router/docs/MCP-BEHAVIOUR-STANDARD-v1.md:43-155`):

- invalid enum values and missing/wrong required arguments produce actionable `invalid_input` and
  name the parameter;
- closed-vocabulary invalid values never silently become an empty successful result;
- `response_mode` cannot change result identity/count;
- totals, `has_more`, and truncation metadata are mathematically honest;
- error results set MCP `isError=true` and use only the six allowed codes;
- an unavailable required upstream cannot be reported as a passing empty result.

Run both behaviour and transport probes against the built container in CI. Keep the vendored probe
byte-identical to the router's source; update it only by deliberate re-vendoring.

### Security and untrusted upstream data

- Set `mask_error_details=True` on FastMCP. Configure exact Host and Origin allowlists; reject
  wildcard allowlist patterns. Do not enable credentialed wildcard CORS. Current layering:
  `../clingen-link/clingen_link/server_manager.py:109-138` and
  `../clingen-link/clingen_link/mcp/facade.py:59-98`.
- Backend containers are not public and do not authenticate end users. Authentication terminates
  at the router/edge. The router deliberately disables incoming-header forwarding and, if needed,
  creates its own service credential (`../genefoundry-router/genefoundry_router/composition.py:23-54`,
  `:65-83`). The backend must never expect or propagate a caller `Authorization` header.
- Treat every upstream free-text field as untrusted evidence, not instructions. Wrap it as
  `{kind:"untrusted_text", text, provenance:{source,record_id,retrieved_at}, raw_sha256}` and apply
  Unicode normalization plus byte/object limits. The governing rules are
  `../genefoundry-router/docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md:7-140`; the reusable code is
  `../clingen-link/clingen_link/mcp/untrusted_content.py:12-120`.
- Unknown tool/resource/prompt names must never be reflected in errors, structured output, or logs.
  Use the layered fixed-message guard in
  `../clingen-link/clingen_link/mcp/notfound_guard.py:59-168`, `:211-350`.
- If arbitrary or upstream-provided URLs are followed, enforce the router HTTP policy: HTTPS only;
  reject syntactic userinfo; allow only the configured normalized `(hostname,effective port)`;
  at most five redirects with validation at every hop; stream responses with a decoded-byte cap;
  retry only transient failures with bounded jitter/backoff; never reveal raw URLs or bodies.
  Normative source: `../genefoundry-router/docs/HTTP-POLICY-STANDARD-v1.md:11-49`; concrete client
  patterns: `../panelapp-link/panelapp_link/api/url_guard.py:26-64` and
  `../panelapp-link/panelapp_link/api/client.py:40-156`, `:166-246`.
- Add bounded concurrency, hard timeouts/deadlines, `Retry-After` handling, and deterministic close
  semantics to live clients. Test HTTP behavior with `respx` rather than real network calls.

## Container, data, and release contracts

The normative source is `../genefoundry-router/docs/CONTAINER-HARDENING-STANDARD-v1.md`.

The image must use digest-pinned multi-stage bases, install with `uv sync --frozen --no-dev`, contain
no secrets, run under a fixed high numeric UID, and have a read-only root filesystem at runtime
(`:41-84`). Runtime compose must drop all capabilities, set `no-new-privileges`, use `init`, avoid
privileged/host networking/Docker socket access, provide CPU/memory/PID limits, expose only to the
private proxy network (no host `ports`), configure health checks and bounded log rotation, and use
an explicit restart policy (`:76-130`). Produce vulnerability scanning, an SBOM, and immutable
release evidence (`:132-142`, `:242-306`).

For data-bearing ClinPGx, prefer the `clingen-link` release shape:

1. The application image contains code only.
2. A separately identified immutable data bundle is fetched/materialized by an init service.
3. Digest/signature and schema compatibility are checked before serving.
4. The app mounts the materialized data read-only and reports expected and actual identity.
5. Readiness fails closed when data is absent or mismatched.

`../clingen-link/container-release.json:1-56` declares those facts and
`../clingen-link/docker/docker-compose.npm.yml:1-142` implements them. If licensing prohibits a
redistributable bundle, use the live-only alternative and document rights/citation in both the MCP
resources and OCI labels.

Compose has a non-obvious split:

- `docker/docker-compose.npm.yml` is the controller-deployed file. Every service in it must have an
  explicit numeric `user` matching its image UID.
- Release compose files listed in `container-release.json.service.compose_files` must **not** have a
  `user` key; the release validator forbids it.
- `service.deployed_compose_files` declares the actual overlay set and is checked for digest image
  references, health/start period, exact restart semantics, required volumes/binds, no published
  ports, read-only root, caps, NNP, and absence of ignored `x-*` controls.

The router deployment contract and rule table are
`../genefoundry-router/docs/deployment.md:40-137`. Pick one fixed UID for `clinpgx-link` (10001 is a
sensible current baseline) and use it consistently; do not copy a sibling UID accidentally.

Release workflows should remain thin wrappers around router-owned reusable workflows, pinned to an
exact reviewed router ref. Copy the structure of
`../clingen-link/.github/workflows/container-ci.yml` and
`../clingen-link/.github/workflows/container-release.yml:1-20`, but resolve the **current** router
release/ref at implementation time instead of copying an older sibling's pin. Releases use protected
`v*` tags, least-privilege permissions, immutable image digests, SBOM/provenance/attestation, and the
release manifest as the source of truth.

At research time the local router is `v0.8.7` at
`6568a0ad7d68925440aba550a2a678282ea5bb6b`; its canonical called workflows are
`../genefoundry-router/.github/workflows/_container-ci.yml` and
`../genefoundry-router/.github/workflows/_container-release.yml`. The current `clingen-link` wrappers
still pin router `v0.8.6` (`3d3cc20477828ddbd8a0c980b5b4f709e2612c02`), which is why copying that SHA
without review would be stale. Re-confirm the tag and immutable commit when implementation starts.

## Router integration contract

The router is a thin FastMCP federation layer. It mounts leaf tools as
`<namespace>_<leaf_tool>` and does not repair or reshape backend responses
(`../genefoundry-router/genefoundry_router/composition.py:86-139`). Therefore all response,
security, and schema conformance must be complete in `clinpgx-link` before registration.

Use this rollout order:

1. Build and release/deploy `clinpgx-link`; confirm `/health`, `/mcp`, transport conformance, and
   behaviour conformance against its real endpoint.
2. Add a `servers.yaml` entry with `name: clinpgx-link`, `repo`, `url_env: GF_CLINPGX_URL`,
   `namespace: clinpgx`, description, source name/URL, tags, and a short list of canonical
   entry-point tools. Do not add a response transform. Registry fields and namespace validation are
   defined at `../genefoundry-router/genefoundry_router/registry.py:10-90`; examples are
   `../genefoundry-router/servers.yaml:33-41`, `:63-71`, `:192-200`.
3. Add `GF_CLINPGX_URL` to the relevant `.env.example`, `.env.docker.example`, development env,
   production compose/profile, and `ci/fleet-urls.env`. The fleet URL test requires exact parity.
4. Add a `clinpgx` entry to `fleet-metadata.yaml`, including provenance/acquisition mode, source URL,
   license/rights, citation, data class/topic tags, and update controlled vocabulary if a new
   pharmacogenomics topic is needed. Metadata coverage must exactly match the registry.
5. Add an explicit row to `docs/conformance/untrusted-text-inventory.yml`, even if the result is
   “no untrusted text.” Exact registry parity is enforced by
   `../genefoundry-router/tests/unit/test_untrusted_content_standard.py:68-86`.
6. Update the controlled release/rollout inventories that apply:
   `ci/fleet-application-releases.json`, `ci/release-candidate-inventory.json`,
   `ci/release-candidate-fleet.json`, `ci/container-controls.json`, and
   `ci/data-identity-rollout-v1.json`.
7. After the endpoint is live, create a reviewed release-candidate snapshot and update
   `genefoundry_router/data/fleet-baseline.json`. Do not invent or hand-author tool definitions. The
   baseline must cover exactly enabled backends and every backend must expose tools
   (`../genefoundry-router/tests/unit/test_ci_fleet_baseline.py:17-46`).
8. Update any tests with hard-coded fleet cardinality, notably
   `tests/unit/test_servers_yaml.py` and `tests/unit/test_packaged_baseline.py`, or convert them to a
   derived assertion if that is the intended router change.
9. Regenerate router README/inventory content from the registry plus reviewed baseline, then run the
   router's full local and fleet gates.

The backend should not publish itself directly to the public MCP Registry. Fleet policy makes the
router the official public remote; backend metadata still must be complete. See
`../genefoundry-router/docs/REPO-METADATA-STANDARD-v1.md:116-146`, `:170-186`.

## `genefoundry` website integration

The landing site is a separate Vue 3/Vite/Tailwind/PWA application
(`../genefoundry/package.json:1-40`) and currently mirrors fleet information manually:

- Add the server record, tool count, category/tags, source URL, and sample tool to
  `../genefoundry/src/data/servers.ts`; its header explicitly says it is manually updated and its
  aggregate counts are derived (`:1-8`, `:54-269`).
- Update hard-coded inventory/count text in `../genefoundry/public/llms.txt:3-38`.
- Correct the PWA description/count in `../genefoundry/vite.config.js:38-40`; it is already stale
  relative to the current router, so derive this value if practical.
- Run type-check, lint without leaving unintended formatter changes, and production build.

The router's `servers.yaml`/baseline remains authoritative; the landing site is presentation only.

## Known fleet drift to resolve deliberately

Do not silently inherit these inconsistencies:

1. **HTTP-only versus legacy stdio.** Current transport policy and current `gnomad-link` CLI are
   HTTP-only; some older sibling CLIs/AGENTS still expose stdio. Use HTTP-only.
2. **Error names.** Older repository prose mentions `data_unavailable`/`internal_error`; the
   canonical response standard and executable behaviour probe use the six-code set above.
3. **Output schemas.** Response Envelope v1 originally required an output schema, while the later
   Tool Surface Budget standard and current `clingen-link` suppress it. Use `output_schema=None`.
4. **Envelope metadata.** The normative standard requires both `request_id` and `elapsed_ms`, but
   sampled current wrappers do not all emit both. Implement the normative fields and test them.
5. **Payload/citation placement.** The central response document says `result`/`results` and a
   top-level safety marker, while some current domain tools use domain-specific payload keys and put
   safety in `_meta`. Specify one canonical ClinPGx shape before coding; the least surprising new
   implementation follows the central standard while retaining current `_meta` provenance and
   `next_commands`. Avoid duplicate fields.
6. **Document status labels.** Some router standards still say “proposed” although current repos and
   CI vendor their tests. For a new backend, the executable conformance gates and current router
   integration tests are release requirements.
7. **Data identity adoption.** Some live/hybrid siblings declare identity “unadopted.” A new
   immutable-data server should use the stronger `clingen-link` runtime identity contract instead.

These choices should be frozen in the ClinPGx requirements/spec before implementation so unit,
conformance, container, and router-baseline tests all assert the same wire truth.
