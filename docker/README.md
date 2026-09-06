# Docker

The container files live here: local stack, the digest-pinned
`docker-compose.prod.yml` and Nginx-Proxy-Manager `docker-compose.npm.yml` overlays,
the Host/Origin boundary, and the release gate.

Build and run clinpgx-link in the unified transport (REST `/health` + MCP `/mcp`).

```bash
# from the repo root
make docker-build      # docker compose -f docker/docker-compose.yml build
make docker-up         # start (loopback-bound; CLINPGX_LINK_HOST_PORT, default 8014)
make docker-url        # print the MCP URL
make docker-logs
make docker-down
```

The MCP streamable-HTTP endpoint is served at `/mcp`; `GET /health` is the
liveness probe used by the container `HEALTHCHECK`.

HTTP requests use exact Host and Origin allowlists. Add the public proxy hostname
to `CLINPGX_ALLOWED_HOSTS`; browser deployments must set the same HTTPS origin
in `CLINPGX_ALLOWED_ORIGINS`.
