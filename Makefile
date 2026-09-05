.PHONY: install lock format format-check lint check-file-size typecheck test test-fast test-foundation check-fastmcp vendor-check ci-local

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

ci-local: format-check lint check-file-size vendor-check typecheck test-fast check-fastmcp
