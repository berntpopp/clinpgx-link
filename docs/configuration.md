# ClinPGx-Link Configuration

Every setting is read from the environment or a `.env` file, using the `CLINPGX_` prefix.

Inspect the effective configuration at any time:

```bash
uv run clinpgx-link config
```

## Server and Runtime

| Variable | Default | Description |
|----------|---------|-------------|
| `CLINPGX_HOST` | `127.0.0.1` | Bind address for the unified HTTP/REST server. |
| `CLINPGX_PORT` | `8000` | Port for the HTTP server. |
| `CLINPGX_MCP_PORT` | `None` | Optional separate port for the MCP mount if decoupled. |
| `CLINPGX_TRANSPORT` | `unified` | Server transport: `unified` (REST + MCP at `/mcp`). |
| `CLINPGX_RUNTIME_MODE` | `development` | `development` (permits running with degraded snapshot) or `production` (strictly requires pinned, exact snapshot). |
| `CLINPGX_MAX_ACTIVE_CALLS` | `16` | Maximum concurrent active calls allowed per process (2–128). |
| `CLINPGX_LOG_LEVEL` | `info` | Structured logging level: `debug`, `info`, `warning`, `error`, `critical`. |
| `CLINPGX_LOG_FORMAT` | `json` | Log format: `json` or `plain`. |

## Data and Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `CLINPGX_CACHE_ROOT` | `/cache/clinpgx-api` | Private directory for SQLite content store. Automatically falls back to `/tmp/clinpgx-cache` if on a read-only rootfs. |
| `CLINPGX_DATA_ROOT` | `/data` | Root directory for installed releases and datasets. |
| `CLINPGX_SNAPSHOT_PATH` | `/data/current/clinpgx.sqlite` | Path to the active immutable SQLite dataset snapshot. |
| `CLINPGX_EXPECTED_SNAPSHOT` | `None` | Optional required SHA-256 digest (`sha256:...`) of the pinned snapshot. Required in production mode. |
| `CLINPGX_CACHE_MAX_BYTES` | `268435456` | Maximum cache capacity in bytes (default 256 MiB). |
| `CLINPGX_CACHE_MAX_ENTRIES` | `10000` | Maximum number of stored content records. |
| `CLINPGX_CACHE_TTL_SECONDS` | `86400` | Wall-clock time-to-live for cached upstream responses (default 24h). |

## Upstream Sources

| Variable | Default | Description |
|----------|---------|-------------|
| `CLINPGX_API_BASE_URL` | `https://api.clinpgx.org/v1` | ClinPGx REST API base endpoint. |
| `CLINPGX_CPIC_API_BASE_URL` | `https://api.cpicpgx.org/v1` | CPIC REST API base endpoint. |
| `CLINPGX_WEBSITE_BASE_URL` | `https://clinpgx.org` | ClinPGx website base URL for verified route fetching. |
| `CLINPGX_DOWNLOAD_BASE_URL` | `https://s3.pgkb.org` | Origin URL for bulk dataset downloads. |

## Request Boundary (Host and Origin Security)

Every HTTP route (REST and MCP) is enforced by exact Host and Origin guards:

| Variable | Default | Description |
|----------|---------|-------------|
| `CLINPGX_ALLOWED_HOSTS` | `["localhost","127.0.0.1","::1"]` | Exact JSON list of allowed `Host` header values. |
| `CLINPGX_ALLOWED_ORIGINS` | `[]` | Exact JSON list of permitted browser `Origin` values (must be canonical HTTPS origins). |
