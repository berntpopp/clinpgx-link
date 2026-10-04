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

### Production data contract (draft; no approved public bundle)

The existing release metadata still declares `data.mode=none`. It does not claim
that a production ClinPGx snapshot has been published or approved for public
distribution. Production startup requires three independent pins: the SQLite
`CLINPGX_EXPECTED_SNAPSHOT`, immutable `CLINPGX_EXPECTED_RELEASE_TAG`, and
`CLINPGX_EXPECTED_RUNTIME_DIGEST`. Missing or mismatched data prevents serving.

`docker/docker-compose.npm.data-draft.yml` is an **unpublished template** layered
after `docker/docker-compose.npm.yml`; it is absent from `container-release.json`.
It renders only with an immutable application image, all three data pins, a
separately verified manifest digest, an offline release-input directory, and a
unique external data volume name. Its app mounts that volume read-only; its
one-shot initializer uses the same image and mounts the volume writable. Both
run as UID:GID `999:999`, with a read-only root filesystem, no capabilities,
`no-new-privileges`, and resource limits. The initializer has no network access
or published port.

Provision the external volume **before** using the template. Its empty root must
be a private real directory owned by UID:GID `999:999` (mode `0700`); a newly
created default Docker volume is normally root-owned and does not satisfy the
installer. Use an operator-provisioned local-volume binding to a host directory
with verified numeric ownership, or an equivalent volume driver. The mounted
release-input files must also be readable by UID 999. Keep the
prior volume and exact prior bundle/manifest available for rollback. Do not
change the selected generation underneath a serving app; prepare replacements
in a distinct candidate volume and replace the container after verification.

The CLI accepts **local** release files only. The manifest digest must come
from independent release evidence, not from the file being installed:

```text
clinpgx-link data install --manifest /release-input/manifest.json \
  --manifest-sha256 <independently-verified-sha256> \
  --artifact /release-input/release.tar.zst --data-root /data
```

For a replacement in a fresh volume, add `--previous-manifest`,
`--previous-manifest-sha256`, and `--previous-artifact` with the exact retained
predecessor release. The native installer verifies manifest compatibility,
bundle bytes, licenses, database semantics, runtime identity, and predecessor
chain before selecting `current`. These paths are operator inputs, never MCP
parameters. No current private candidate is publication-approved, and this
template is not a deploy instruction for the live fleet.

### Nginx Proxy Manager (NPM) Overlay

For the existing Nginx Proxy Manager deployment overlay:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.npm.yml up -d
```

The standalone overlay above lacks a production snapshot binding. It remains
held from production activation until a rights-reviewed immutable bundle and
matching application release have passed the fleet controller's data contract.
