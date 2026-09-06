# Development Conventions

All code in `clinpgx-link` conforms to the standards of the GeneFoundry fleet.

## Code Standards & Style

- **Python Version**: Python 3.12+ managed with `uv`.
- **Formatting & Linting**: Ruff handles both code formatting and linting.
  ```bash
  make format
  make lint
  ```
- **Static Typing**: Strict Mypy type-checking with no untyped definitions allowed in package code.
  ```bash
  make typecheck
  ```
- **Module Line Budget**: Every Python module under `clinpgx_link/` must remain strictly under 600 nonblank, noncomment lines (`scripts/check_file_size.py`).

## Error Taxonomy

The public MCP error surface exposes exactly six normalized error codes:

1. `invalid_input`: Client provided unparseable or out-of-bounds parameters.
2. `not_found`: Requested identifier or resource does not exist.
3. `ambiguous_query`: Search term matched multiple distinct entities without a primary choice.
4. `upstream_unavailable`: Remote service or local snapshot could not be contacted or read.
5. `rate_limited`: Upstream quota or local admission capacity exceeded.
6. `internal`: Unexpected failure masked to prevent information disclosure.

## Testing & Quality Gate

`make ci-local` is the required local quality gate:
```bash
make ci-local
```
It verifies formatting, linting, file size budgets, vendor contracts, strict typing, unit tests, and FastMCP importability.
