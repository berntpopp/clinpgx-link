"""Unified, guarded FastAPI host for stateless JSON MCP over HTTP."""

from __future__ import annotations

import ipaddress
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from clinpgx_link import __version__
from clinpgx_link.api.client import ClinPGxClient
from clinpgx_link.api.website import WebsiteClient
from clinpgx_link.config import Settings
from clinpgx_link.config import settings as default_settings
from clinpgx_link.content.store import ContentStore
from clinpgx_link.data.repository import DatasetRepository
from clinpgx_link.logging_config import (
    bind_request_id,
    clear_request_context,
    configure_logging,
)
from clinpgx_link.mcp.envelope import REQUEST_ID
from clinpgx_link.mcp.facade import create_mcp
from clinpgx_link.services.api import ApiService

_SNAPSHOT_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RELEASE_TAG = re.compile(r"data-clinpgx-(?:core|extended)-[0-9a-f]{16}\Z")
_MCP_CORS_HEADERS = [
    "accept",
    "content-type",
    "last-event-id",
    "mcp-protocol-version",
    "mcp-session-id",
    "x-request-id",
]


def _canonical_uuid4(value: str) -> str | None:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        return None
    canonical = str(parsed)
    return canonical if parsed.version == 4 and value == canonical else None


def _request_id(headers: list[tuple[bytes, bytes]]) -> str:
    supplied = [value.decode("latin-1") for key, value in headers if key.lower() == b"x-request-id"]
    if len(supplied) == 1:
        canonical = _canonical_uuid4(supplied[0])
        if canonical is not None:
            return canonical
    return str(uuid.uuid4())


def _replace_header(
    headers: list[tuple[bytes, bytes]], name: bytes, value: bytes
) -> list[tuple[bytes, bytes]]:
    lowered = name.lower()
    return [(key, item) for key, item in headers if key.lower() != lowered] + [(name, value)]


class RequestContextMiddleware:
    """Bind and echo one canonical request identifier around every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_scope = dict(scope)
        headers = list(scope.get("headers", []))
        request_id = _request_id(headers)
        request_scope["headers"] = _replace_header(
            headers, b"x-request-id", request_id.encode("ascii")
        )
        token = REQUEST_ID.set(request_id)
        bind_request_id(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                message["headers"] = _replace_header(
                    response_headers, b"x-request-id", request_id.encode("ascii")
                )
            await send(message)

        try:
            await self.app(request_scope, receive, send_with_id)
        finally:
            clear_request_context()
            REQUEST_ID.reset(token)


def _canonical_request_host(value: str) -> str | None:
    if not value or value != value.strip() or any(ord(char) < 33 for char in value):
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    host = value
    port: str | None = None
    if value.startswith("["):
        close = value.find("]")
        if close < 0:
            return None
        host = value[1:close]
        suffix = value[close + 1 :]
        if suffix:
            if not suffix.startswith(":"):
                return None
            port = suffix[1:]
    elif value.count(":") == 1:
        host, port = value.rsplit(":", 1)
    if port is not None and (
        not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65_535
    ):
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if (
            not host
            or not host.isascii()
            or host != host.lower()
            or host.endswith(".")
            or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-." for char in host)
        ):
            return None
        return host


class ExactHostOriginMiddleware:
    """Reject any Host or present Origin outside the exact configured allowlists."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: tuple[str, ...],
        allowed_origins: tuple[str, ...],
    ) -> None:
        self.app = app
        self._allowed_hosts = frozenset(allowed_hosts)
        self._allowed_origins = frozenset(allowed_origins)
        self._logger = structlog.get_logger("clinpgx_link")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw_headers = list(scope.get("headers", []))
        hosts = [value.decode("latin-1") for key, value in raw_headers if key.lower() == b"host"]
        host = _canonical_request_host(hosts[0]) if len(hosts) == 1 else None
        if host not in self._allowed_hosts:
            self._logger.warning("request_failed", status="failed", status_code=421, source="mcp")
            await JSONResponse({"error": "misdirected_request"}, status_code=421)(
                scope, receive, send
            )
            return
        origins = [
            value.decode("latin-1") for key, value in raw_headers if key.lower() == b"origin"
        ]
        if len(origins) > 1 or (origins and origins[0] not in self._allowed_origins):
            self._logger.warning("request_failed", status="failed", status_code=403, source="mcp")
            await JSONResponse({"error": "forbidden_origin"}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


class ProtocolVersionMiddleware:
    """Fail closed before dispatch when a transport protocol header is unsupported."""

    def __init__(self, app: ASGIApp, *, path: str) -> None:
        self.app = app
        self._path = path
        self._supported = frozenset(SUPPORTED_PROTOCOL_VERSIONS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") in {self._path, self._path + "/"}:
            headers = Headers(scope=scope)
            supplied = headers.getlist("mcp-protocol-version")
            if len(supplied) > 1 or (supplied and supplied[0] not in self._supported):
                await JSONResponse({"error": "unsupported_mcp_protocol_version"}, status_code=400)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def _snapshot_status(runtime_settings: Settings) -> dict[str, Any] | None:
    repository: DatasetRepository | None = None
    try:
        repository = DatasetRepository(runtime_settings.snapshot_path)
        status = repository.status()
        datasets = repository.list_datasets().value
        identity = status.get("snapshot_id")
        release_tag = status.get("release_tag")
        if not isinstance(identity, str) or _SNAPSHOT_ID.fullmatch(identity) is None:
            return None
        if not isinstance(release_tag, str) or _RELEASE_TAG.fullmatch(release_tag) is None:
            return None
        if not isinstance(datasets, list) or not datasets:
            return None
        return {
            "snapshot_id": identity,
            "release_tag": release_tag,
            "dataset_count": len(datasets),
        }
    except Exception:
        return None
    finally:
        if repository is not None:
            repository.close()


def _health(runtime_settings: Settings) -> tuple[dict[str, Any], int]:
    snapshot = _snapshot_status(runtime_settings)
    exact = snapshot is not None and (
        runtime_settings.expected_snapshot is None
        or snapshot["snapshot_id"] == runtime_settings.expected_snapshot
    )
    payload: dict[str, Any] = {
        "status": "healthy" if exact else "degraded",
        "service": "clinpgx-link",
        "version": __version__,
        "transport": "streamable-http-stateless",
        "runtime_mode": runtime_settings.runtime_mode,
        "ready": exact,
        "data_available": exact,
    }
    if exact and snapshot is not None:
        payload.update(snapshot)
    status_code = 503 if runtime_settings.runtime_mode == "production" and not exact else 200
    return payload, status_code


def _compose_lifespan(app: FastAPI, mcp_app: Any) -> None:
    host_lifespan = app.router.lifespan_context
    mcp_lifespan = mcp_app.router.lifespan_context

    @asynccontextmanager
    async def combined(parent: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(host_lifespan(parent))
            await stack.enter_async_context(mcp_lifespan(parent))
            yield

    app.router.lifespan_context = combined


def create_app(runtime_settings: Settings | None = None) -> FastAPI:
    """Create the HTTP-only application with health routes before the MCP mount."""
    selected = runtime_settings or default_settings
    configure_logging(selected.log_level, selected.log_format)
    store = ContentStore(
        selected.cache_root / "content.sqlite",
        max_bytes=selected.cache_max_bytes,
        max_entries=selected.cache_max_entries,
        ttl_seconds=selected.cache_ttl_seconds,
    )
    source_client = ClinPGxClient(selected, content_store=store)
    api_service = ApiService(source_client)
    website_client = WebsiteClient(source_client)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger = structlog.get_logger("clinpgx_link")
        logger.info("server_started", status="success", source="mcp")
        try:
            yield
        finally:
            await source_client.close()
            logger.info("server_stopped", status="success", source="mcp")

    app = FastAPI(
        title="clinpgx-link",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        lifespan=lifespan,
    )
    app.state.content_store = store
    app.state.source_client = source_client
    app.state.settings = selected

    @app.get("/api/live")
    async def live() -> dict[str, Any]:
        return {
            "status": "alive",
            "service": "clinpgx-link",
            "version": __version__,
            "transport": "streamable-http-stateless",
        }

    @app.get("/health")
    @app.get("/api/health")
    async def health() -> JSONResponse:
        payload, status_code = _health(selected)
        return JSONResponse(payload, status_code=status_code)

    mcp = create_mcp(
        content_store=store,
        api_service=api_service,
        website_client=website_client,
    )
    mcp_app = mcp.http_app(
        path=selected.mcp_path,
        stateless_http=True,
        json_response=True,
        host_origin_protection=False,
    )
    mcp_app.router.redirect_slashes = False
    _compose_lifespan(app, mcp_app)
    app.state.mcp = mcp
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(selected.allowed_origins),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=_MCP_CORS_HEADERS,
        allow_credentials=False,
        expose_headers=["x-request-id"],
        max_age=600,
    )
    app.add_middleware(ProtocolVersionMiddleware, path=selected.mcp_path)
    app.add_middleware(
        ExactHostOriginMiddleware,
        allowed_hosts=selected.allowed_hosts,
        allowed_origins=selected.allowed_origins,
    )
    app.add_middleware(RequestContextMiddleware)
    app.mount("/", mcp_app)
    return app


class UnifiedServerManager:
    """Own one configured Uvicorn HTTP process and its shutdown signal."""

    def __init__(self, runtime_settings: Settings | None = None) -> None:
        self.settings = runtime_settings or default_settings
        self._server: uvicorn.Server | None = None

    async def serve(self) -> None:
        configure_logging(self.settings.log_level, self.settings.log_format)
        app = create_app(self.settings)
        config = uvicorn.Config(
            app=app,
            host=self.settings.mcp_host,
            port=self.settings.mcp_port,
            log_config=None,
            access_log=False,
            lifespan="on",
        )
        self._server = uvicorn.Server(config)
        await self._server.serve()

    async def shutdown(self) -> None:
        if self._server is not None:
            self._server.should_exit = True


__all__ = ["UnifiedServerManager", "create_app"]
