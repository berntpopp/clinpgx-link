# ClinPGx-Link Deployment

`clinpgx-link` is deployed as a stateless, read-only HTTP service running FastAPI and FastMCP.
The only supported client transport is **Streamable HTTP** (`/mcp`); there is no stdio transport.

## Quick Local Run

Run locally with Python 3.12+ and uv:

```bash
uv sync --group dev
make dev
```

The unified server listens on `127.0.0.1:8000`:
- REST & Health: `http://127.0.0.1:8000/health`
- Liveness Probe: `http://127.0.0.1:8000/api/live`
- Streamable HTTP MCP: `http://127.0.0.1:8000/mcp`

Check status:

```bash
uv run clinpgx-link health --url http://127.0.0.1:8000
```

## Docker Container Deployment

The container builds a multi-stage, hardened image based on `python:3.14-slim`:
- Runs as non-root user `app:999`.
- Root filesystem is mounted read-only (`read_only: true`).
- All Linux capabilities dropped (`cap_drop: [ALL]`).
- Security options set `no-new-privileges:true`.
- Memory and PIDs bounds enforced.

### Local Container Run

```bash
make docker-build
make docker-up
make docker-url
make docker-logs
make docker-down
```

### Production data contract

Production is bound to the approved immutable ClinPGx core release `data-clinpgx-core-cfde21bfec473b35` (schema `1.0.0`, 453,102 records). The selected source set contains 15 official ClinPGx bulk archives retrieved on 2026-10-03. The public release preserves the archives' original notices and the publisher-policy discrepancy; owner authorization permits this exact distribution but does not reclassify the source terms or assert an independent legal conclusion.

The application release binds the snapshot ID `sha256:cbc4bba16380e8badfb05eb402bfbd47e631aba74a83f83dc239d75b75113e3b`, runtime-v1 identity `sha256:ef590d6eba25423eb562405edfa7ac41862172bc689f496c2ff5346ddd3958ca`, and separate outer-manifest and compressed-artifact SHA-256 pins. Production startup refuses missing or mismatched data.

The production NPM stack is the ordered pair `docker/docker-compose.npm.yml` and `docker/docker-compose.npm.data.yml`. The initializer uses the same exact application image as the service, reads only the host seed at `/srv/genefoundry/clinpgx-seed`, and installs `data-release-manifest.json` plus `clinpgx-core.tar.zst` without network access. The application sees the external data volume read-only and is reachable only on the proxy network; Compose does not publish an application port. Both containers run as UID:GID `999:999` with read-only root filesystems, no capabilities, `no-new-privileges`, and resource limits.

Provision the new external data volume before activation. Its root must be a private real directory owned by UID:GID `999:999` with mode `0700`; the native installer rejects any other mode. Stage the two exact public release assets in the fixed seed directory with permissions readable by UID 999. Preserve the prior volume and exact prior application/data release tuple for rollback. Install replacements into a distinct candidate volume, validate its runtime identity, then replace the application container; never change the selected generation under a serving app.

The offline command accepts local files only. Its manifest digest and artifact digest are independently pinned controller inputs, not values trusted from a neighboring checksum file:

```text
clinpgx-link data install --manifest /release-input/data-release-manifest.json \
  --manifest-sha256 <independently-verified-manifest-sha256> \
  --artifact /release-input/clinpgx-core.tar.zst --data-root /data
```

The initializer authenticates the exact manifest digest and artifact bytes, then checks the bundle semantics, schema, runtime identity, and destination volume permissions before selecting `current`. At application startup, the three `CLINPGX_EXPECTED_*` pins bind the mounted snapshot, release tag, and runtime identity to that installed bundle. These paths and pins are operator inputs, never MCP parameters.

### Nginx Proxy Manager (NPM) Overlay

For local proxy-stack rendering, use the same two deployment files and provide the exact release pins, seed, and pre-provisioned external data volume:

```bash
docker compose -f docker/docker-compose.npm.yml \
  -f docker/docker-compose.npm.data.yml --env-file .env.docker up -d
```

The application is a production data-bound service; a missing or incorrect pin prevents startup. The local-development Compose files remain separate and do not provide the production data binding.
