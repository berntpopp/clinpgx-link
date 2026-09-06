DOCKER_COMPOSE := $(shell if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; else echo "docker compose"; fi)
COMPOSE := $(DOCKER_COMPOSE) -f docker/docker-compose.yml $(shell [ -f docker/.env ] && echo "--env-file docker/.env")

.PHONY: install lock format format-check lint check-file-size typecheck test test-fast test-foundation check-fastmcp vendor-check ci-local dev docker-build docker-up docker-down docker-logs docker-url

install:
	uv sync --group dev

lock:
	uv lock

format:
	uv run ruff format clinpgx_link tests scripts
	uv run ruff check --fix clinpgx_link tests scripts

format-check:
	uv run ruff format --check clinpgx_link tests scripts

lint:
	uv run ruff check clinpgx_link tests scripts

check-file-size:
	uv run python scripts/check_file_size.py

typecheck:
	uv run mypy clinpgx_link

test:
	uv run pytest

test-fast:
	uv run pytest tests/unit -m "not slow and not integration" -q

test-foundation:
	uv run pytest tests/unit/test_foundation.py -q

check-fastmcp:
	uv run python -c "from fastmcp import Client, FastMCP; from mcp.types import CallToolResult; print(FastMCP, Client, CallToolResult)"

vendor-check:
	uv run --frozen python scripts/check_vendor_contract.py $(if $(GENEFOUNDRY_ROUTER_DIR),--router-dir "$(GENEFOUNDRY_ROUTER_DIR)")
	uv run --frozen python scripts/check_conformance_vendor.py $(if $(GENEFOUNDRY_ROUTER_DIR),--router-dir "$(GENEFOUNDRY_ROUTER_DIR)")

ci-local: format-check lint check-file-size vendor-check typecheck test-fast check-fastmcp

dev:
	uv run clinpgx-link serve --transport unified --host 127.0.0.1 --port 8000

docker-build:
	$(COMPOSE) build

docker-up:
	$(COMPOSE) up -d
	@$(MAKE) --no-print-directory docker-url

docker-down:
	$(COMPOSE) down

docker-logs:
	$(COMPOSE) logs -f

docker-url:
	@hostport=$$($(COMPOSE) port clinpgx-link 8000 2>/dev/null); \
	port=$${hostport##*:}; \
	if [ -n "$$port" ]; then \
	  echo "clinpgx-link MCP: http://127.0.0.1:$$port/mcp  (health: http://127.0.0.1:$$port/health)"; \
	  echo "Claude Code: claude mcp add --transport http clinpgx-link --scope user http://127.0.0.1:$$port/mcp"; \
	else \
	  echo "clinpgx-link container is not running. Start it with: make docker-up"; \
	fi
