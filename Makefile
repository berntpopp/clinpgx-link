.PHONY: install lock format format-check lint typecheck test test-fast test-foundation check-fastmcp ci-local

install:
	uv sync --group dev

lock:
	uv lock

format:
	uv run ruff format clinpgx_link tests
	uv run ruff check --fix clinpgx_link tests

format-check:
	uv run ruff format --check clinpgx_link tests

lint:
	uv run ruff check clinpgx_link tests

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

ci-local: format-check lint typecheck test-fast check-fastmcp

