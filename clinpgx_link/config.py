"""Bounded environment-backed settings for ClinPGx Link."""

from __future__ import annotations

import json
from pathlib import Path, PurePath
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_DEFAULT_API_ORIGIN = "https://api.clinpgx.org"
_DEFAULT_DOWNLOAD_ORIGIN = "https://s3.pgkb.org"

OriginTuple = Annotated[tuple[str, ...], NoDecode]


def _canonical_https_origin(value: str) -> str:
    """Validate and return an exact, userinfo-free HTTPS origin."""
    if any(marker in value for marker in "*?[]"):
        raise ValueError("origin patterns are not allowed")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise ValueError("origin must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("origin must not contain user information")
    if parsed.port is not None:
        raise ValueError("origin must not contain an explicit port")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("origin must not contain a path, query, or fragment")
    canonical = f"https://{parsed.hostname}"
    if value != canonical:
        raise ValueError("origin must be canonical lowercase HTTPS origin")
    return canonical


class Settings(BaseSettings):
    """Application settings loaded from ``CLINPGX_`` environment variables."""

    api_base_url: str = "https://api.clinpgx.org/v1"
    api_allowed_origins: OriginTuple = (_DEFAULT_API_ORIGIN,)
    download_allowed_origins: OriginTuple = (_DEFAULT_API_ORIGIN, _DEFAULT_DOWNLOAD_ORIGIN)
    website_allowed_origins: OriginTuple = (_DEFAULT_API_ORIGIN,)
    attachment_allowed_origins: OriginTuple = (_DEFAULT_API_ORIGIN, _DEFAULT_DOWNLOAD_ORIGIN)
    source_auth_token: SecretStr | None = Field(default=None, exclude=True, repr=False)

    api_requests_per_second: float = Field(default=2.0, gt=0, le=2.0)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=300.0)
    request_deadline_seconds: float = Field(default=60.0, gt=0, le=600.0)
    max_redirects: int = Field(default=5, ge=0, le=5)
    max_response_bytes: int = Field(default=128 * _MIB, gt=0, le=128 * _MIB)

    cache_root: Path = Path("/cache/clinpgx-api")
    cache_ttl_seconds: int = Field(default=3600, gt=0, le=86_400)
    cache_max_entries: int = Field(default=512, gt=0, le=100_000)
    cache_max_bytes: int = Field(default=512 * _MIB, gt=0, le=4 * _GIB)

    data_root: Path = Path("/data")
    snapshot_path: Path = Path("/data/current/clinpgx.sqlite")
    staging_root: Path = Path("/var/lib/clinpgx-link/staging")
    max_archive_bytes: int = Field(default=256 * _MIB, gt=0, le=256 * _MIB)
    max_expanded_archive_bytes: int = Field(default=2 * _GIB, gt=0, le=2 * _GIB)
    max_archive_member_bytes: int = Field(default=128 * _MIB, gt=0, le=128 * _MIB)
    max_archive_members: int = Field(default=10_000, gt=0, le=100_000)
    max_ingest_rows: int = Field(default=5_000_000, gt=0, le=20_000_000)

    response_token_budget: int = Field(default=25_000, gt=0, le=25_000)
    content_chunk_bytes: int = Field(default=4096, gt=0, le=8192)
    max_page_size: int = Field(default=100, gt=0, le=100)

    runtime_mode: Literal["development", "production"] = "development"
    expected_snapshot: str | None = None
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8000, ge=1, le=65_535)
    mcp_path: str = "/mcp"
    allowed_hosts: OriginTuple = ("localhost", "127.0.0.1", "::1")
    allowed_origins: OriginTuple = ()
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    model_config = SettingsConfigDict(
        env_prefix="CLINPGX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        hide_input_in_errors=True,
        case_sensitive=False,
    )

    @field_validator(
        "api_allowed_origins",
        "download_allowed_origins",
        "website_allowed_origins",
        "attachment_allowed_origins",
        "allowed_hosts",
        "allowed_origins",
        mode="before",
    )
    @classmethod
    def _parse_string_tuple(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return ()
        if stripped.startswith("["):
            decoded = json.loads(stripped)
            if not isinstance(decoded, list):
                raise ValueError("allowlist JSON must be an array")
            return tuple(decoded)
        return tuple(item.strip() for item in stripped.split(",") if item.strip())

    @field_validator(
        "api_allowed_origins",
        "download_allowed_origins",
        "website_allowed_origins",
        "attachment_allowed_origins",
    )
    @classmethod
    def _validate_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        validated = tuple(_canonical_https_origin(value) for value in values)
        if len(validated) != len(set(validated)):
            raise ValueError("origin allowlist entries must be unique")
        return validated

    @field_validator("allowed_hosts")
    @classmethod
    def _validate_hosts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or any(marker in value for marker in "*?[]/@") for value in values):
            raise ValueError("host allowlist entries must be exact host names")
        return values

    @field_validator("allowed_origins")
    @classmethod
    def _validate_client_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_canonical_https_origin(value) for value in values)

    @field_validator("data_root", "snapshot_path", "cache_root", "staging_root", mode="before")
    @classmethod
    def _validate_runtime_path(cls, value: object) -> Path:
        raw = str(value)
        path = PurePath(raw)
        if not path.is_absolute() or raw == "/" or ".." in path.parts:
            raise ValueError(
                "runtime path must be absolute, specific, and contain no parent traversal"
            )
        if str(Path(raw)) != raw.rstrip("/"):
            raise ValueError("runtime path must be lexically normalized")
        return Path(raw)

    @field_validator("api_base_url")
    @classmethod
    def _validate_api_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.path != "/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("API base URL must be an exact HTTPS /v1 URL")
        return value

    @field_validator("mcp_path")
    @classmethod
    def _validate_mcp_path(cls, value: str) -> str:
        if value != "/mcp":
            raise ValueError("MCP path must be /mcp")
        return value

    @model_validator(mode="after")
    def _validate_cross_field_limits(self) -> Settings:
        api_origin = f"https://{urlsplit(self.api_base_url).hostname}"
        if api_origin not in self.api_allowed_origins:
            raise ValueError("API base origin must be present in api_allowed_origins")
        if self.request_deadline_seconds < self.request_timeout_seconds:
            raise ValueError("request deadline must be at least the per-request timeout")
        if self.max_archive_member_bytes > self.max_expanded_archive_bytes:
            raise ValueError("member limit must not exceed expanded archive limit")
        if self.runtime_mode == "production" and not self.expected_snapshot:
            raise ValueError("production mode requires expected_snapshot")
        return self


settings = Settings()

__all__ = ["Settings", "settings"]
