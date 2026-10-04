"""HTTP-only operator CLI for ClinPGx Link."""

from __future__ import annotations

import asyncio
import ipaddress
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
import typer
from pydantic import ValidationError
from rich.console import Console

from clinpgx_link import __version__
from clinpgx_link.exceptions import DataValidationError

if TYPE_CHECKING:
    from clinpgx_link.config import Settings

app = typer.Typer(
    name="clinpgx-link",
    add_completion=False,
    no_args_is_help=True,
    help="Read-only ClinPGx source evidence over stateless HTTP MCP.",
)
console = Console()
data_app = typer.Typer(help="Offline operator-only release installation.")
app.add_typer(data_app, name="data")


def _runtime_settings(
    *,
    host: str | None = None,
    port: int | None = None,
    cache_root: Path | None = None,
    log_level: str | None = None,
) -> Settings:
    from clinpgx_link.config import Settings

    current = Settings()
    values = current.model_dump()
    if host is not None:
        values["mcp_host"] = host
    if port is not None:
        values["mcp_port"] = port
    if cache_root is not None:
        values["cache_root"] = cache_root
    if log_level is not None:
        values["log_level"] = log_level.upper()
    return Settings(**values)


def _safe_config(configured: Settings) -> dict[str, Any]:
    return {
        "transport": "http",
        "mcp_host": configured.mcp_host,
        "mcp_port": configured.mcp_port,
        "mcp_path": configured.mcp_path,
        "runtime_mode": configured.runtime_mode,
        "expected_snapshot": configured.expected_snapshot,
        "expected_release_tag": configured.expected_release_tag,
        "expected_runtime_digest": configured.expected_runtime_digest,
        "snapshot_path": str(configured.snapshot_path),
        "cache_root": str(configured.cache_root),
        "allowed_hosts": list(configured.allowed_hosts),
        "allowed_origins": list(configured.allowed_origins),
        "log_level": configured.log_level,
        "log_format": configured.log_format,
    }


@data_app.command("install")
def data_install(
    manifest: Path = typer.Option(..., "--manifest", help="Local immutable manifest file."),
    manifest_sha256: str = typer.Option(
        ..., "--manifest-sha256", help="Independently pinned manifest SHA-256."
    ),
    artifact: Path = typer.Option(..., "--artifact", help="Local bundle file."),
    data_root: Path = typer.Option(..., "--data-root", help="Private installation volume root."),
    previous_manifest: Path | None = typer.Option(None, "--previous-manifest"),
    previous_manifest_sha256: str | None = typer.Option(None, "--previous-manifest-sha256"),
    previous_artifact: Path | None = typer.Option(None, "--previous-artifact"),
) -> None:
    """Verify and select an offline immutable release (not callable over MCP)."""
    from clinpgx_link.operator_install import install_offline

    try:
        receipt = install_offline(
            manifest_path=manifest,
            manifest_sha256=manifest_sha256,
            artifact_path=artifact,
            data_root=data_root,
            previous_manifest_path=previous_manifest,
            previous_manifest_sha256=previous_manifest_sha256,
            previous_artifact_path=previous_artifact,
        )
    except (DataValidationError, ValueError, OSError):
        console.print("[red]Offline release installation failed.[/red]")
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "release_tag": receipt.release_tag,
                "artifact_sha256": receipt.artifact_sha256,
                "runtime_digest": receipt.runtime_identity_sha256,
                "snapshot_id": receipt.snapshot_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _local_health_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.hostname is None
        ):
            return None
        host = parsed.hostname
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback or parsed.port is None:
            return None
    except ValueError:
        return None
    return value.rstrip("/") + "/health"


@app.command()
def serve(
    transport: str = typer.Option("http", "--transport", help="Transport mode; HTTP only."),
    host: str | None = typer.Option(None, "--host", help="Host to bind."),
    port: int | None = typer.Option(None, "--port", help="TCP port to bind."),
    cache_root: Path | None = typer.Option(
        None, "--cache-root", help="Writable directory for retained source content."
    ),
    log_level: str | None = typer.Option(None, "--log-level", help="Configured log level."),
) -> None:
    """Serve health and stateless JSON MCP on one HTTP port."""
    if transport not in {"http", "unified"}:
        console.print("[red]HTTP transport only.[/red]")
        raise typer.Exit(code=2)
    try:
        configured = _runtime_settings(
            host=host,
            port=port,
            cache_root=cache_root,
            log_level=log_level,
        )
    except (ValidationError, ValueError):
        console.print("[red]Invalid server configuration.[/red]")
        raise typer.Exit(code=2) from None

    from clinpgx_link.server_manager import UnifiedServerManager

    try:
        asyncio.run(UnifiedServerManager(configured).serve())
    except KeyboardInterrupt:
        raise typer.Exit(code=0) from None
    except Exception:
        console.print("[red]Server startup failed.[/red]")
        raise typer.Exit(code=1) from None


@app.command("config")
def show_config() -> None:
    """Print validated, non-secret configuration as JSON."""
    try:
        configured = _runtime_settings()
    except (ValidationError, ValueError):
        console.print("[red]Invalid server configuration.[/red]")
        raise typer.Exit(code=1) from None
    typer.echo(json.dumps(_safe_config(configured), sort_keys=True, separators=(",", ":")))


@app.command()
def health(
    url: str = typer.Option(
        "http://127.0.0.1:8000", "--url", help="Loopback HTTP server base URL."
    ),
) -> None:
    """Check the running server readiness endpoint over loopback HTTP."""
    target = _local_health_url(url)
    if target is None:
        console.print("[red]Health URL must be a loopback HTTP origin with an explicit port.[/red]")
        raise typer.Exit(code=2)
    try:
        response = httpx.get(target, timeout=5, follow_redirects=False)
        body = response.json()
        status = body.get("status") if isinstance(body, dict) else None
        ready = body.get("ready") if isinstance(body, dict) else None
        if (
            response.status_code != 200
            or status not in {"healthy", "degraded"}
            or not isinstance(ready, bool)
        ):
            raise ValueError("invalid health response")
    except (httpx.HTTPError, ValueError, json.JSONDecodeError):
        console.print("[red]Health check failed.[/red]")
        raise typer.Exit(code=1) from None
    typer.echo(json.dumps({"ready": ready, "status": status}, sort_keys=True))


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
