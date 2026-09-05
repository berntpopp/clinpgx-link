"""Unified HTTP-only host, transport, safety and readiness contracts."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "clinpgx-host-test", "version": "1.0.0"},
    },
}
_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
    "host": "testserver",
}
_MODERN_VERSION = "2026-07-28"
_MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": _MODERN_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "clinpgx-host-test", "version": "1.0.0"},
}


def _modern_headers(method: str, *, name: str | None = None) -> dict[str, str]:
    headers = {
        **_HEADERS,
        "mcp-protocol-version": _MODERN_VERSION,
        "mcp-method": method,
    }
    if name is not None:
        headers["mcp-name"] = name
    return headers


def _settings(tmp_path: Path, **overrides):
    from clinpgx_link.config import Settings

    return Settings(
        _env_file=None,
        cache_root=tmp_path / "cache",
        data_root=tmp_path / "data",
        snapshot_path=tmp_path / "data/current/clinpgx.sqlite",
        allowed_hosts=("testserver", "localhost", "127.0.0.1", "::1"),
        allowed_origins=("https://client.example",),
        **overrides,
    )


def _snapshot(path: Path, identity: str) -> None:
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [("snapshot_id", identity), ("release_tag", "data-clinpgx-core-0123456789abcdef")],
        )
        connection.execute(
            "CREATE TABLE dataset (dataset_id TEXT, file_name TEXT, published_at TEXT, "
            "byte_count INTEGER, sha256 TEXT, license_id TEXT, tier TEXT, record_count INTEGER, "
            "limitations_json TEXT, warnings_json TEXT, source_url TEXT, retrieved_at TEXT)"
        )
        connection.execute(
            "INSERT INTO dataset VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "data/genes.zip",
                "genes.zip",
                "2026-09-05T00:00:00Z",
                1,
                "a" * 64,
                "CC-BY-SA-4.0",
                "core",
                1,
                "[]",
                "[]",
                "https://api.clinpgx.org/v1/download/file/data/genes.zip",
                "2026-09-05T08:00:00Z",
            ),
        )


def _uuid4(value: str) -> bool:
    parsed = uuid.UUID(value)
    return parsed.version == 4 and str(parsed) == value


def test_development_health_is_live_but_explicitly_not_ready_without_snapshot(tmp_path):
    from clinpgx_link.server_manager import create_app

    with TestClient(create_app(_settings(tmp_path))) as client:
        health = client.get("/health", headers={"host": "testserver"})
        alias = client.get("/api/health", headers={"host": "testserver"})
        live = client.get("/api/live", headers={"host": "testserver"})

    assert health.status_code == 200
    assert health.json() == alias.json()
    assert health.json()["status"] == "degraded"
    assert health.json()["ready"] is False
    assert health.json()["data_available"] is False
    assert health.json()["transport"] == "streamable-http-stateless"
    assert live.status_code == 200
    assert live.json()["status"] == "alive"


@pytest.mark.parametrize("matching", [True, False])
def test_production_health_requires_exact_pinned_snapshot(tmp_path, matching):
    from clinpgx_link.server_manager import create_app

    actual = "sha256:" + "a" * 64
    expected = actual if matching else "sha256:" + "b" * 64
    settings = _settings(tmp_path, runtime_mode="production", expected_snapshot=expected)
    _snapshot(settings.snapshot_path, actual)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health", headers={"host": "testserver"})

    assert response.status_code == (200 if matching else 503)
    assert response.json()["ready"] is matching
    assert response.json()["data_available"] is matching
    if matching:
        assert response.json()["snapshot_id"] == actual
        assert response.json()["dataset_count"] == 1
    else:
        assert actual not in response.text and expected not in response.text


def test_production_health_rejects_identity_only_database(tmp_path):
    from clinpgx_link.server_manager import create_app

    identity = "sha256:" + "a" * 64
    settings = _settings(tmp_path, runtime_mode="production", expected_snapshot=identity)
    settings.snapshot_path.parent.mkdir(parents=True)
    with sqlite3.connect(settings.snapshot_path) as connection:
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [("snapshot_id", identity), ("release_tag", "data-clinpgx-core-0123456789abcdef")],
        )

    with TestClient(create_app(settings)) as client:
        response = client.get("/health", headers={"host": "testserver"})
        live = client.get("/api/live", headers={"host": "testserver"})

    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert live.status_code == 200


@pytest.mark.parametrize("path", ["/health", "/api/live", "/mcp"])
def test_exact_host_guard_rejects_lookalikes_on_every_route(tmp_path, path):
    from clinpgx_link.server_manager import create_app

    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get(path, headers={"host": "testserver.evil"})

    assert response.status_code == 421
    assert response.json() == {"error": "misdirected_request"}
    assert _uuid4(response.headers["x-request-id"])


def test_rejected_host_is_not_reflected_and_structured_log_stays_on_stderr(tmp_path, capsys):
    from clinpgx_link.server_manager import create_app

    hostile = "secret.attacker.example"
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/health", headers={"host": hostile})

    captured = capsys.readouterr()
    assert response.status_code == 421
    assert hostile not in response.text + captured.out + captured.err
    assert captured.out == ""
    assert "request_failed" in captured.err


@pytest.mark.parametrize("path", ["/health", "/api/live", "/mcp"])
def test_exact_origin_guard_rejects_unlisted_origin_on_every_route(tmp_path, path):
    from clinpgx_link.server_manager import create_app

    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get(
            path,
            headers={"host": "testserver", "origin": "https://client.example.evil"},
        )

    assert response.status_code == 403
    assert response.json() == {"error": "forbidden_origin"}
    assert _uuid4(response.headers["x-request-id"])


def test_allowed_origin_preflight_is_granted_without_credentials(tmp_path):
    from clinpgx_link.server_manager import create_app

    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.options(
            "/mcp",
            headers={
                "host": "testserver",
                "origin": "https://client.example",
                "access-control-request-method": "POST",
                "access-control-request-headers": (
                    "content-type,mcp-protocol-version,mcp-method,mcp-name"
                ),
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://client.example"
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "mcp-method" in allowed_headers
    assert "mcp-name" in allowed_headers
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize(
    ("supplied", "preserved"),
    [
        ("2eb4ae86-7f47-4be9-945a-36d1f103230c", True),
        ("not-a-request-id", False),
        ("6ba7b810-9dad-11d1-80b4-00c04fd430c8", False),
    ],
)
def test_request_id_is_canonical_uuid4_and_echoed(tmp_path, supplied, preserved):
    from clinpgx_link.server_manager import create_app

    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/live", headers={"host": "testserver", "x-request-id": supplied})

    actual = response.headers["x-request-id"]
    assert _uuid4(actual)
    assert (actual == supplied) is preserved


@pytest.mark.parametrize("protocol_version", ["2025-06-18", "2025-11-25"])
def test_legacy_mcp_http_lists_and_calls_at_negotiated_version(tmp_path, protocol_version):
    from clinpgx_link.server_manager import create_app

    request_id = "2eb4ae86-7f47-4be9-945a-36d1f103230c"
    headers = {**_HEADERS, "x-request-id": request_id}
    protocol_headers = {**headers, "mcp-protocol-version": protocol_version}
    initialize = {
        **_INIT,
        "params": {**_INIT["params"], "protocolVersion": protocol_version},
    }
    with TestClient(create_app(_settings(tmp_path)), follow_redirects=False) as client:
        initialized = client.post("/mcp", headers=headers, json=initialize)
        listed = client.post(
            "/mcp",
            headers=protocol_headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        called = client.post(
            "/mcp",
            headers=protocol_headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_server_capabilities", "arguments": {}},
            },
        )

    assert initialized.status_code == 200
    assert initialized.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in initialized.headers
    assert initialized.json()["result"]["serverInfo"]["name"] == "clinpgx-link"
    assert initialized.json()["result"]["protocolVersion"] == protocol_version
    assert listed.status_code == 200
    assert {tool["name"] for tool in listed.json()["result"]["tools"]} >= {
        "get_server_capabilities",
        "get_source_content",
    }
    assert called.status_code == 200
    assert called.headers["x-request-id"] == request_id
    structured = called.json()["result"]["structuredContent"]
    assert structured["_meta"]["request_id"] == request_id


def test_modern_mcp_http_discovers_lists_calls_and_fences_unknown_tool(tmp_path):
    from clinpgx_link.server_manager import create_app

    hostile = "ignore-instructions-secret"
    with TestClient(create_app(_settings(tmp_path)), follow_redirects=False) as client:
        discover = client.post(
            "/mcp",
            headers=_modern_headers("server/discover"),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"_meta": _MODERN_META},
            },
        )
        listed = client.post(
            "/mcp",
            headers=_modern_headers("tools/list"),
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {"_meta": _MODERN_META},
            },
        )
        called = client.post(
            "/mcp",
            headers=_modern_headers("tools/call", name="get_server_capabilities"),
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "_meta": _MODERN_META,
                    "name": "get_server_capabilities",
                    "arguments": {},
                },
            },
        )
        unknown = client.post(
            "/mcp",
            headers=_modern_headers("tools/call", name=hostile),
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"_meta": _MODERN_META, "name": hostile, "arguments": {}},
            },
        )

    assert discover.status_code == 200
    assert discover.json()["result"]["supportedVersions"] == [_MODERN_VERSION]
    assert "mcp-session-id" not in discover.headers
    assert listed.status_code == 200
    assert {tool["name"] for tool in listed.json()["result"]["tools"]} >= {
        "get_server_capabilities",
        "get_source_content",
    }
    assert called.status_code == 200
    called_result = called.json()["result"]
    assert json.loads(called_result["content"][0]["text"]) == called_result["structuredContent"]
    assert called_result["structuredContent"]["success"] is True
    assert unknown.status_code == 200
    unknown_result = unknown.json()["result"]
    assert unknown_result["isError"] is True
    assert json.loads(unknown_result["content"][0]["text"]) == unknown_result["structuredContent"]
    assert unknown_result["structuredContent"]["error_code"] == "not_found"
    assert hostile not in unknown.text


def test_http_mcp_suppresses_native_fastmcp_spans_before_hostile_dispatch(
    tmp_path, monkeypatch, capsys
):
    import fastmcp.telemetry as fastmcp_telemetry
    from opentelemetry.trace import INVALID_SPAN, NoOpTracer, TracerProvider

    from clinpgx_link.server_manager import create_app

    span_starts: list[tuple[str, object]] = []

    class RecordingTracer(NoOpTracer):
        @contextmanager
        def start_as_current_span(self, name, **kwargs):
            span_starts.append((name, kwargs.get("attributes")))
            yield INVALID_SPAN

    tracer = RecordingTracer()

    class RecordingProvider(TracerProvider):
        def get_tracer(self, *_args, **_kwargs):
            return tracer

    provider = RecordingProvider()
    monkeypatch.setattr(fastmcp_telemetry, "otel_get_tracer", provider.get_tracer)
    hostile_name = "hostile-tool-name-never-export"
    hostile_error = "hostile-error-never-export"
    with TestClient(create_app(_settings(tmp_path))) as client:
        unknown = client.post(
            "/mcp",
            headers=_modern_headers("tools/call", name=hostile_name),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"_meta": _MODERN_META, "name": hostile_name, "arguments": {}},
            },
        )
        invalid = client.post(
            "/mcp",
            headers=_modern_headers("tools/call", name=hostile_name),
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "_meta": _MODERN_META,
                    "name": hostile_name,
                    "arguments": hostile_error,
                },
            },
        )

    captured = capsys.readouterr()
    assert unknown.status_code == 200
    assert unknown.json()["result"]["structuredContent"]["error_code"] == "not_found"
    assert invalid.status_code == 400
    assert hostile_name not in unknown.text + invalid.text + captured.out + captured.err
    assert hostile_error not in unknown.text + invalid.text + captured.out + captured.err
    assert span_starts == []


def test_mcp_trailing_slash_does_not_redirect(tmp_path):
    from clinpgx_link.server_manager import create_app

    with TestClient(create_app(_settings(tmp_path)), follow_redirects=False) as client:
        response = client.post("/mcp/", headers=_HEADERS, json=_INIT)

    assert response.status_code == 404
    assert "location" not in response.headers


def test_unsupported_post_initialize_protocol_header_is_fixed_400(tmp_path):
    from clinpgx_link.server_manager import create_app

    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.post(
            "/mcp",
            headers={**_HEADERS, "mcp-protocol-version": "1999-01-01"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )

    assert response.status_code == 400
    assert response.json() == {"error": "unsupported_mcp_protocol_version"}
    assert "1999" not in json.dumps(response.json())


def test_content_store_is_closed_by_application_lifespan(tmp_path):
    from clinpgx_link.server_manager import create_app

    app = create_app(_settings(tmp_path))
    store = app.state.content_store
    with TestClient(app):
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        store.get("content:" + "0" * 64)


def test_source_client_and_shared_store_have_one_application_lifetime(tmp_path):
    from clinpgx_link.exceptions import UpstreamUnavailableError
    from clinpgx_link.server_manager import create_app

    app = create_app(_settings(tmp_path))
    source_client = app.state.source_client
    assert source_client.content_store is app.state.content_store

    with TestClient(app):
        pass

    with pytest.raises(UpstreamUnavailableError, match="closed"):
        asyncio.run(source_client.request("GET", "/report/stats"))


def _diagnostics(client):
    response = client.post(
        "/mcp",
        headers=_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_diagnostics", "arguments": {}},
        },
    )
    assert response.status_code == 200
    return response.json()["result"]["structuredContent"]["result"]["local_snapshot"]


def test_http_tools_and_health_share_snapshot_until_restart(tmp_path):
    from clinpgx_link.server_manager import create_app

    original = "sha256:" + "a" * 64
    replacement = "sha256:" + "b" * 64
    settings = _settings(tmp_path)
    _snapshot(settings.snapshot_path, original)
    candidate = tmp_path / "candidate" / "clinpgx.sqlite"
    _snapshot(candidate, replacement)

    with TestClient(create_app(settings)) as client:
        assert _diagnostics(client)["snapshot_id"] == original
        candidate.replace(settings.snapshot_path)
        assert client.get("/health").json()["snapshot_id"] == original
        assert _diagnostics(client)["snapshot_id"] == original

    with TestClient(create_app(settings)) as restarted:
        assert _diagnostics(restarted)["snapshot_id"] == replacement
        assert restarted.get("/health").json()["snapshot_id"] == replacement


def test_snapshot_is_admitted_at_lifespan_start_not_app_construction(tmp_path):
    from clinpgx_link.server_manager import create_app

    settings = _settings(tmp_path)
    _snapshot(settings.snapshot_path, "sha256:" + "a" * 64)
    candidate = tmp_path / "candidate" / "clinpgx.sqlite"
    _snapshot(candidate, "sha256:" + "b" * 64)
    app = create_app(settings)
    candidate.replace(settings.snapshot_path)
    with TestClient(app) as client:
        assert _diagnostics(client)["snapshot_id"] == "sha256:" + "b" * 64


def test_mcp_startup_failure_closes_admitted_snapshot(tmp_path, monkeypatch):
    from clinpgx_link import server_manager

    settings = _settings(tmp_path)
    _snapshot(settings.snapshot_path, "sha256:" + "a" * 64)
    app = server_manager.create_app(settings)

    def failed_factory(**kwargs):
        raise RuntimeError("Synthetic MCP construction failure")

    monkeypatch.setattr(server_manager, "create_mcp", failed_factory)
    with pytest.raises(RuntimeError, match="Synthetic MCP construction failure"), TestClient(app):
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        app.state.repository.list_datasets()
    with pytest.raises(sqlite3.ProgrammingError):
        app.state.content_store.get("content:" + "0" * 64)


def test_mismatched_production_snapshot_is_unavailable_to_http_tools(tmp_path):
    from clinpgx_link.models import SourceInfo
    from clinpgx_link.server_manager import create_app

    settings = _settings(
        tmp_path, runtime_mode="production", expected_snapshot="sha256:" + "b" * 64
    )
    _snapshot(settings.snapshot_path, "sha256:" + "a" * 64)
    app = create_app(settings)
    raw = b"retained source evidence"
    reference = app.state.content_store.put(
        raw,
        SourceInfo(
            "test",
            "https://example.test/source",
            "2026-09-05T00:00:00Z",
            hashlib.sha256(raw).hexdigest(),
            "api",
        ),
        "text/plain",
    )
    with TestClient(app) as client:
        assert _diagnostics(client)["ready"] is False
        assert client.get("/health").status_code == 503
        response = client.post(
            "/mcp",
            headers=_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "get_source_content",
                    "arguments": {"content_ref": reference, "representation": "base64"},
                },
            },
        )
        result = response.json()["result"]
        assert result["isError"] is True
        assert result["structuredContent"]["error_code"] == "upstream_unavailable"
        assert result["structuredContent"]["subtype"] == "snapshot_not_ready"


def test_snapshot_handle_is_closed_with_http_application(tmp_path):
    from clinpgx_link.server_manager import create_app

    settings = _settings(tmp_path)
    _snapshot(settings.snapshot_path, "sha256:" + "a" * 64)
    app = create_app(settings)
    with TestClient(app) as client:
        assert _diagnostics(client)["ready"] is True
        repository = app.state.repository
    with pytest.raises(sqlite3.ProgrammingError):
        repository.list_datasets()


def test_unready_production_makes_no_source_requests(tmp_path, monkeypatch):
    from clinpgx_link.server_manager import create_app

    settings = _settings(
        tmp_path, runtime_mode="production", expected_snapshot="sha256:" + "b" * 64
    )
    app = create_app(settings)
    sent = []

    async def unexpected_send(request, **kwargs):
        sent.append(request)
        raise AssertionError("Unready production must not contact upstream")

    monkeypatch.setattr(app.state.source_client._http, "send", unexpected_send)
    with TestClient(app) as client:
        for name, arguments in (
            (
                "get_api_data",
                {"operation": "GET /data/gene/{id}", "path_parameters": {"id": "PA124"}},
            ),
            (
                "get_website_data",
                {
                    "operation": "GET /site/alleleFunction/{geneId}",
                    "path_parameters": {"geneId": "PA128"},
                },
            ),
        ):
            response = client.post(
                "/mcp",
                headers=_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            result = response.json()["result"]
            assert result["isError"] is True
            assert result["structuredContent"]["subtype"] == "snapshot_not_ready"
        probe = client.post(
            "/mcp",
            headers=_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "get_diagnostics", "arguments": {"probe_upstream": True}},
            },
        )
        assert not probe.json()["result"]["isError"]
        assert (
            probe.json()["result"]["structuredContent"]["result"]["upstream_probe"]["status"]
            == "not_configured"
        )
        assert client.get("/api/live").status_code == 200
    assert sent == []


def test_http_dataset_discovery_reaches_exact_installed_bytes(tmp_path):
    from clinpgx_link.server_manager import create_app
    from tests.unit.test_repository import FIXTURES, _repository

    repository, built = _repository(tmp_path)
    repository.close()
    settings = _settings(tmp_path).model_copy(update={"snapshot_path": built.database})
    with TestClient(create_app(settings)) as client:

        def call(name, arguments):
            response = client.post(
                "/mcp",
                headers=_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            assert response.status_code == 200
            result = response.json()["result"]
            assert not result.get("isError", False), result
            assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
            return result["structuredContent"]

        entry = call(
            "search_records",
            {
                "entity_type": "gene",
                "source": "download",
                "filters": {"id": "PA124"},
            },
        )
        assert entry["results"][0]["id"] == "PA124"
        catalog = call("list_datasets", {"query": "genes", "limit": 1})
        assert catalog["results"][0]["dataset_id"] == "data/genes.zip"
        dataset = call("get_dataset", {"dataset_id": "data/genes.zip"})
        assert dataset["_meta"]["pagination"]["snapshot_id"] == built.snapshot_id
        assert dataset["_meta"]["snapshot_id"] == built.snapshot_id
        member = dataset["result"]["members"][0]
        content = call(
            "get_source_content",
            {"content_ref": member["content_ref"], "representation": "base64", "length": 8192},
        )["result"]
        assert content["offline_available"] is True
        assert content["has_more"] is False
        assert base64.b64decode(content["base64"]) == (FIXTURES / "genes.tsv").read_bytes()
        first = call("search_dataset", {"dataset_id": "data/genes.zip", "limit": 1})
        page = first["_meta"]["pagination"]
        assert page["total_count"] == 2
        assert first["results"][0]["id"] == "PA124"
        second = call(
            "search_dataset",
            {
                "dataset_id": "data/genes.zip",
                "cursor": page["next_cursor"],
                "limit": 1,
            },
        )
        assert second["results"][0]["id"] == "PA165884561"
        assert second["_meta"]["pagination"]["has_more"] is False
        record = call(
            "get_dataset_record",
            {
                "record_id": first["results"][0]["record_id"],
                "pointer": "/fields/Symbol",
            },
        )
        assert record["_meta"]["snapshot_id"] == built.snapshot_id
        assert json.loads(record["result"]["selected"]["data"]["text"]) == "CYP2C19"
        entities = call(
            "search_records",
            {
                "entity_type": "gene",
                "source": "download",
                "filters": {"name": "CYP2C19"},
            },
        )
        assert entities["results"][0]["id"] == "PA124"
        detail = call(
            "get_record",
            {
                "entity_type": "gene",
                "record_id": "PA124",
                "source": "download",
                "pointer": "/fields/Symbol",
            },
        )
        assert detail["_meta"]["snapshot_id"] == built.snapshot_id
        assert json.loads(detail["result"]["selected"]["data"]["text"]) == "CYP2C19"
        annotations = call(
            "search_dataset",
            {
                "dataset_id": "data/summaryAnnotations.zip",
                "member": "summary_annotations.tsv",
                "filters": {"Summary Annotation ID": "655384602"},
            },
        )
        related = call(
            "get_related_records",
            {
                "record_id": annotations["results"][0]["record_id"],
                "result_type": "evidence",
                "source": "download",
            },
        )
        assert related["_meta"]["pagination"]["total_count"] == 1
        assert related["results"][0]["join"]["relation_kind"] == "evidence"
