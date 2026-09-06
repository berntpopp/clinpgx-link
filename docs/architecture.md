# ClinPGx-Link Architecture

ClinPGx Link separates HTTP transport, FastMCP boundary, domain services, and dataset storage.

## Architectural Layers

1. **CLI & Entrypoint** (`clinpgx_link/cli.py`):
   Typer-based CLI for server execution, configuration inspection, and health probing.
   Startup exceptions are masked to prevent leaking filesystem paths or credentials.

2. **Server Manager** (`clinpgx_link/server_manager.py`):
   Orchestrates FastAPI and FastMCP lifespans. Enforces Host and Origin guards, configures structured logging to stderr, and initializes the shared `ContentStore` and `DatasetRepository`.

3. **MCP Facade & Boundary Guard** (`clinpgx_link/mcp/`):
   Exposes 13 model-oriented tools. The `BoundaryGuard` middleware intercepts all incoming tool calls to enforce:
   - Request admission pool (`Admission`, default 16 concurrent calls).
   - Execution deadlines (60-second cutoff).
   - Output payload size ceilings (maximum envelope bytes).
   - Untrusted source text fencing.

4. **Domain Services** (`clinpgx_link/services/`, `clinpgx_link/api/`):
   - `ApiService`: Interacts with ClinPGx and CPIC REST APIs via an outbound token-bucket limiter (2 req/s).
   - `WebsiteClient`: Fetches verified HTML/JSON routes from the ClinPGx web interface.
   - `DatasetRepository`: Manages queries against the immutable SQLite release snapshot.

5. **Content Store** (`clinpgx_link/content/store.py`):
   Maintains a private SQLite-backed cache of raw and parsed responses. References (`content:<digest>`) survive restarts and support JSON pointer slicing.

## Safety Invariants

- **Research Use Only**: No clinical interpretation, dosing calculation, or phenotype inference.
- **Untrusted Source Payloads**: Upstream evidence is treated strictly as data, never executed as model instructions.
- **Fail-Closed Security**: Unrecognized Host headers, invalid origins, or unverified paths return immediate typed errors.
