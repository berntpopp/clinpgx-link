"""HTTP-only operator CLI contract."""

from __future__ import annotations

import json

import httpx
from typer.testing import CliRunner

runner = CliRunner()


def test_cli_exposes_only_current_http_spine_commands():
    from clinpgx_link.cli import app

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("serve", "version", "config", "health"):
        assert command in result.stdout
    for unsupported in ("stdio", "sse", "release", "refresh"):
        assert unsupported not in result.stdout


def test_version_uses_package_single_source():
    from clinpgx_link import __version__
    from clinpgx_link.cli import app

    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_config_is_json_and_never_displays_source_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("CLINPGX_SOURCE_AUTH_TOKEN", "do-not-print-this")
    monkeypatch.setenv("CLINPGX_CACHE_ROOT", str(tmp_path / "cache"))
    from clinpgx_link.cli import app

    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["mcp_path"] == "/mcp"
    assert payload["transport"] == "http"
    assert payload["cache_root"] == str(tmp_path / "cache")
    assert "do-not-print-this" not in result.stdout
    assert "source_auth_token" not in result.stdout


def test_serve_rejects_non_http_transport_without_reflection():
    from clinpgx_link.cli import app

    hostile = "stdio-secret-value"
    result = runner.invoke(app, ["serve", "--transport", hostile])
    assert result.exit_code == 2
    assert hostile not in result.stdout
    assert "HTTP transport only" in result.stdout


def test_serve_passes_validated_writable_cache_override(monkeypatch, tmp_path):
    captured = {}

    async def fake_serve(self):
        captured["settings"] = self.settings

    from clinpgx_link import server_manager
    from clinpgx_link.cli import app

    monkeypatch.setattr(server_manager.UnifiedServerManager, "serve", fake_serve)
    cache = tmp_path / "writable-cache"
    result = runner.invoke(
        app,
        [
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--cache-root",
            str(cache),
        ],
    )
    assert result.exit_code == 0
    assert captured["settings"].mcp_port == 8765
    assert captured["settings"].cache_root == cache


def test_serve_masks_startup_exception_details(monkeypatch, tmp_path):
    from clinpgx_link import server_manager
    from clinpgx_link.cli import app

    async def failed_serve(self):
        raise RuntimeError("secret filesystem target")

    monkeypatch.setattr(server_manager.UnifiedServerManager, "serve", failed_serve)
    result = runner.invoke(app, ["serve", "--cache-root", str(tmp_path / "writable-cache")])
    assert result.exit_code == 1
    assert "secret filesystem target" not in result.stdout
    assert "Server startup failed" in result.stdout


def test_health_checks_http_endpoint_and_emits_bounded_json(monkeypatch):
    from clinpgx_link.cli import app

    def healthy(url, **_kwargs):
        assert url == "http://127.0.0.1:8000/health"
        return httpx.Response(
            200,
            json={"status": "degraded", "ready": False},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", healthy)
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"ready": False, "status": "degraded"}


def test_health_failure_never_reflects_transport_exception(monkeypatch):
    from clinpgx_link.cli import app

    def unavailable(_url, **_kwargs):
        raise httpx.ConnectError("secret internal target")

    monkeypatch.setattr(httpx, "get", unavailable)
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 1
    assert "secret internal target" not in result.stdout
    assert "Health check failed" in result.stdout
