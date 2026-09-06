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

### Production Compose Stack

To deploy with the production hardening overlay:

```bash
export CLINPGX_LINK_IMAGE=ghcr.io/berntpopp/clinpgx-link:0.1.0
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d
```

### Nginx Proxy Manager (NPM) Overlay

For environments using Nginx Proxy Manager:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.npm.yml up -d
```

Remember to add the public proxy domain to `CLINPGX_ALLOWED_HOSTS` so Host-header guards accept the traffic.
