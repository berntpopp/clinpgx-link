"""Foundation contracts shared by every ClinPGx Link component."""

from __future__ import annotations

import dataclasses
import importlib.metadata
import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

PROJECT_ROOT = Path(__file__).parents[2]
OFFICIAL_OPERATIONS = PROJECT_ROOT / "docs/research/clinpgx-openapi-operations-2026-09-05.json"
OFFICIAL_DOWNLOADS = PROJECT_ROOT / "docs/research/clinpgx-download-registry-2026-09-05.json"
PACKAGE_OPERATIONS = PROJECT_ROOT / "clinpgx_link/api/operations.json"
PACKAGE_OPENAPI = PROJECT_ROOT / "clinpgx_link/api/openapi.json"
PACKAGE_COVERAGE = PROJECT_ROOT / "clinpgx_link/data/coverage.json"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_installed_version_matches_package_metadata() -> None:
    """Catch a hard-coded runtime version that diverges from installed metadata."""
    from clinpgx_link import __version__

    assert __version__ == importlib.metadata.version("clinpgx-link")


def test_shared_dataclasses_match_the_frozen_collaborator_contract() -> None:
    """Catch missing fields or mutability that would break downstream adapters."""
    from clinpgx_link.models import BoundRequest, SourceInfo, SourceResponse

    assert [field.name for field in dataclasses.fields(SourceInfo)] == [
        "source",
        "url",
        "retrieved_at",
        "sha256",
        "data_source",
        "published_at",
        "release_tag",
        "coverage",
        "warnings",
    ]
    assert [field.name for field in dataclasses.fields(SourceResponse)] == [
        "value",
        "source",
        "details",
    ]
    assert [field.name for field in dataclasses.fields(BoundRequest)] == [
        "method",
        "path",
        "params",
        "form",
        "representation",
    ]

    source = SourceInfo(
        source="ClinPGx REST API",
        url="https://api.clinpgx.org/v1/data/gene/PA124",
        retrieved_at="2026-09-05T10:00:00Z",
        sha256="a" * 64,
        data_source="api",
    )
    response_one = SourceResponse(value={"id": "PA124"}, source=source)
    response_two = SourceResponse(value=[], source=source)
    request = BoundRequest(method="GET", path="/data/gene/PA124", params={})

    response_one.details["complete"] = True
    assert response_two.details == {}
    assert source.coverage == "unknown"
    assert source.warnings == ()
    assert request.form is None
    assert request.representation == "json"
    with pytest.raises(dataclasses.FrozenInstanceError):
        source.coverage = "complete"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.path = "/different"  # type: ignore[misc]


def test_settings_defaults_encode_measured_source_and_size_limits() -> None:
    """Catch unsafe defaults that exceed the measured API or binding limits."""
    from clinpgx_link.config import Settings

    settings = Settings(_env_file=None)

    assert settings.api_base_url == "https://api.clinpgx.org/v1"
    assert settings.api_allowed_origins == ("https://api.clinpgx.org",)
    assert settings.download_allowed_origins == (
        "https://api.clinpgx.org",
        "https://s3.pgkb.org",
    )
    assert settings.website_allowed_origins == ("https://api.clinpgx.org",)
    assert settings.attachment_allowed_origins == (
        "https://api.clinpgx.org",
        "https://s3.pgkb.org",
    )
    assert settings.api_requests_per_second == 2.0
    assert settings.max_response_bytes == 128 * 1024 * 1024
    assert settings.max_archive_bytes == 256 * 1024 * 1024
    assert settings.max_expanded_archive_bytes == 2 * 1024 * 1024 * 1024
    assert settings.max_archive_member_bytes == 128 * 1024 * 1024
    assert settings.cache_ttl_seconds == 3600
    assert settings.response_token_budget == 25_000


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_requests_per_second", 0),
        ("api_requests_per_second", 2.0001),
        ("request_timeout_seconds", 0),
        ("request_timeout_seconds", 301),
        ("max_response_bytes", 0),
        ("max_response_bytes", 128 * 1024 * 1024 + 1),
        ("cache_ttl_seconds", 0),
        ("cache_max_entries", 0),
        ("max_archive_bytes", 0),
        ("max_expanded_archive_bytes", 0),
        ("max_archive_member_bytes", 0),
        ("max_archive_members", 0),
        ("max_ingest_rows", 0),
        ("response_token_budget", 0),
        ("response_token_budget", 25_001),
    ],
)
def test_settings_reject_values_outside_operator_safety_bounds(field: str, value: object) -> None:
    """Catch disabled or excessive network, cache, ingest, and response limits."""
    from clinpgx_link.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize(
    "origin",
    [
        "http://api.clinpgx.org",
        "https://*.clinpgx.org",
        "https://api.clinpgx.org/v1",
        "https://api.clinpgx.org?redirect=https://example.org",
        "https://user@example.org",
        "https://example.org:443",
    ],
)
def test_origin_allowlists_require_canonical_https_origins(origin: str) -> None:
    """Catch wildcard, credential-bearing, non-origin, and noncanonical entries."""
    from clinpgx_link.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_allowed_origins=(origin,))


@pytest.mark.parametrize("path", ["relative", "/", "/data/../tmp"])
def test_runtime_paths_are_absolute_specific_and_normalized(path: str) -> None:
    """Catch ambiguous or broad filesystem targets in operator configuration."""
    from clinpgx_link.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, data_root=path)


def test_settings_use_clinpgx_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catch an environment prefix mismatch that would silently ignore deployment settings."""
    from clinpgx_link.config import Settings

    monkeypatch.setenv("CLINPGX_API_REQUESTS_PER_SECOND", "1.25")
    assert Settings(_env_file=None).api_requests_per_second == 1.25


def test_sensitive_configuration_never_appears_in_repr_json_or_validation_errors() -> None:
    """Catch secret or credential-bearing URL reflection through common diagnostics."""
    from clinpgx_link.config import Settings

    secret = "never-reflect-this-value"
    settings = Settings(_env_file=None, source_auth_token=secret)

    assert isinstance(settings.source_auth_token, SecretStr)
    assert secret not in repr(settings)
    assert secret not in settings.model_dump_json()

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, api_allowed_origins=(f"https://user:{secret}@example.org",))
    assert secret not in str(caught.value)


def test_domain_errors_expose_only_the_six_code_taxonomy_and_fixed_subtypes() -> None:
    """Catch taxonomy drift before domain errors reach the MCP boundary."""
    from clinpgx_link.exceptions import (
        ERROR_CODES,
        AmbiguousQueryError,
        ClinPGxError,
        DataValidationError,
        InvalidInputError,
        NotFoundError,
        RateLimitedError,
        ResponseTooLargeError,
        UpstreamUnavailableError,
    )

    expected = frozenset(
        {
            "invalid_input",
            "not_found",
            "ambiguous_query",
            "upstream_unavailable",
            "rate_limited",
            "internal",
        }
    )
    assert expected == ERROR_CODES
    assert {
        InvalidInputError.error_code,
        NotFoundError.error_code,
        AmbiguousQueryError.error_code,
        UpstreamUnavailableError.error_code,
        RateLimitedError.error_code,
    } == expected - {"internal"}

    generic = ClinPGxError("Safe developer message", field="record_id", hint="Use a PA ID")
    assert str(generic) == "Safe developer message"
    assert generic.field == "record_id"
    assert generic.hint == "Use a PA ID"
    assert generic.subtype is None
    assert DataValidationError("Invalid source data").subtype == "data_invalid"
    assert DataValidationError.error_code == "upstream_unavailable"
    assert ResponseTooLargeError("Use a pointer").subtype == "response_too_large"
    assert ResponseTooLargeError.error_code == "invalid_input"


def test_correlated_logging_drops_payloads_queries_urls_and_secrets() -> None:
    """Catch accidental upstream or caller-data leakage through structured logs."""
    from clinpgx_link.logging_config import (
        bind_request_id,
        clear_request_context,
        configure_logging,
    )

    stream = StringIO()
    logger = configure_logging(level="INFO", log_format="json", stream=stream)
    request_id = "2eb4ae86-7f47-4be9-945a-36d1f103230c"
    bind_request_id(request_id)
    logger.info(
        "request_complete",
        record_count=2,
        payload={"gene": "sensitive-payload"},
        query="sensitive-query",
        url="https://api.clinpgx.org/v1/data/gene?symbol=sensitive-url",
        source_auth_token="sensitive-token",
    )
    clear_request_context()

    event = json.loads(stream.getvalue())
    rendered = json.dumps(event)
    assert event["event"] == "request_complete"
    assert event["request_id"] == request_id
    assert event["record_count"] == 2
    assert event["service"] == "clinpgx-link"
    assert "sensitive" not in rendered
    assert not {"payload", "query", "url", "source_auth_token"} & event.keys()


def test_stdlib_dependency_logs_never_render_request_or_exception_payloads() -> None:
    """Catch http/framework log records bypassing the structured payload filter."""
    import logging

    import httpx

    from clinpgx_link.logging_config import configure_logging

    sentinel = "hostile-query-never-log"
    stream = StringIO()
    configure_logging(level="INFO", log_format="json", stream=stream)
    records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = CaptureHandler()
    logging.getLogger().addHandler(capture)

    try:
        with httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={}))
        ) as client:
            client.get("https://api.clinpgx.org/v1/data/gene", params={"symbol": sentinel})
        try:
            raise RuntimeError(sentinel)
        except RuntimeError:
            logging.getLogger("fastmcp.server").exception("unsafe dependency message %s", sentinel)
    finally:
        logging.getLogger().removeHandler(capture)

    rendered = stream.getvalue()
    assert sentinel not in rendered
    assert "api.clinpgx.org" not in rendered
    assert records
    assert all(record.getMessage() == "dependency_event" for record in records)
    assert all(record.exc_info is None for record in records)
    events = [json.loads(line) for line in rendered.splitlines()]
    assert events
    assert all(event["event"] == "dependency_event" for event in events)
    assert all(event["service"] == "clinpgx-link" for event in events)


@pytest.mark.parametrize("unsafe", ["never-reflect-this-value", "hostile\nforged=value"])
def test_logging_validates_every_retained_string_value_and_bound_context(unsafe: str) -> None:
    """Catch a sensitive value smuggled through an otherwise allowlisted log field."""
    import structlog

    from clinpgx_link.logging_config import clear_request_context, configure_logging

    retained_fields = {
        "cache_hit",
        "data_source",
        "elapsed_ms",
        "error_code",
        "event",
        "exception_type",
        "log_level",
        "method",
        "operation",
        "record_count",
        "release_tag",
        "request_id",
        "retry_count",
        "service",
        "snapshot_id",
        "source",
        "status",
        "status_code",
        "timestamp",
        "version",
    }
    rendered_events: list[str] = []
    for field in sorted(retained_fields):
        stream = StringIO()
        logger = configure_logging(level="INFO", log_format="json", stream=stream)
        if field == "event":
            logger.info(unsafe)
        else:
            logger.info("request_complete", **{field: unsafe})
        rendered_events.append(stream.getvalue())

        stream = StringIO()
        logger = configure_logging(level="INFO", log_format="json", stream=stream)
        structlog.contextvars.bind_contextvars(**{field: unsafe})
        logger.info("request_complete")
        rendered_events.append(stream.getvalue())
        clear_request_context()

    assert unsafe not in "".join(rendered_events)


def test_logging_keeps_only_bounded_typed_operational_values() -> None:
    """Catch string coercion, unbounded counters, or accidental removal of valid metrics."""
    from clinpgx_link.logging_config import configure_logging

    stream = StringIO()
    logger = configure_logging(level="INFO", log_format="json", stream=stream)
    logger.info(
        "request_complete",
        cache_hit=True,
        elapsed_ms=12.5,
        method="GET",
        record_count=2,
        retry_count=1,
        status="success",
        status_code=200,
    )
    valid = json.loads(stream.getvalue())
    assert valid["cache_hit"] is True
    assert valid["elapsed_ms"] == 12.5
    assert valid["method"] == "GET"
    assert valid["record_count"] == 2
    assert valid["retry_count"] == 1
    assert valid["status"] == "success"
    assert valid["status_code"] == 200

    stream = StringIO()
    logger = configure_logging(level="INFO", log_format="json", stream=stream)
    logger.info(
        "request_complete",
        cache_hit="true",
        elapsed_ms=-1,
        record_count=10**1000,
        retry_count=True,
        status_code=999,
    )
    invalid = json.loads(stream.getvalue())
    assert (
        not {
            "cache_hit",
            "elapsed_ms",
            "record_count",
            "retry_count",
            "status_code",
        }
        & invalid.keys()
    )


@pytest.mark.parametrize(
    "host",
    [
        " example.org ",
        "example.org:443",
        "example\\evil.org",
        "a..b",
        "Example.org",
        "example.org.",
        "-example.org",
        "example-.org",
        "café.example",
        "[::1]",
        "127.000.000.001",
        "fe80::1%eth0",
        "fe80::1%evil host",
        "fe80::1%evil\\host",
        "fe80::1%evil\nhost",
    ],
)
def test_allowed_hosts_reject_noncanonical_dns_and_ip_literals(host: str) -> None:
    """Catch host values that are not exact canonical DNS names or IP literals."""
    from clinpgx_link.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, allowed_hosts=(host,))


def test_allowed_hosts_accept_unique_canonical_dns_and_ip_literals() -> None:
    """Catch validation that rejects middleware-ready DNS, IPv4, or bare IPv6 hosts."""
    from clinpgx_link.config import Settings

    hosts = ("localhost", "api.clinpgx.org", "127.0.0.1", "2001:db8::1", "xn--caf-dma.example")
    assert Settings(_env_file=None, allowed_hosts=hosts).allowed_hosts == hosts

    with pytest.raises(ValidationError):
        Settings(_env_file=None, allowed_hosts=("localhost", "localhost"))


def test_vendored_openapi_and_operation_inventory_match_actual_source() -> None:
    """Catch missing documented operations or mutation of the captured OpenAPI bytes."""
    import hashlib

    official = _load_json(OFFICIAL_OPERATIONS)
    vendored = _load_json(PACKAGE_OPERATIONS)
    expected = {(entry["method"], entry["path"]) for entry in official["operations"]}
    implemented = {(entry["method"], entry["path"]) for entry in vendored["operations"]}

    assert official["operation_count"] == 34
    assert vendored["operation_count"] == 34
    assert expected == implemented
    assert (
        vendored["source_openapi_sha256"]
        == hashlib.sha256(PACKAGE_OPENAPI.read_bytes()).hexdigest()
    )
    assert vendored["source_openapi_sha256"] == (
        "d5945ffec00d99df4b10c8df605ff95ca1e1f360279541580c67097e93f06cc6"
    )


def test_coverage_accounts_for_every_operation_and_download_without_claiming_implementation() -> (
    None
):
    """Catch inventory omissions and fabricated usable-coverage states."""
    operations = _load_json(PACKAGE_OPERATIONS)
    downloads = _load_json(OFFICIAL_DOWNLOADS)
    coverage = _load_json(PACKAGE_COVERAGE)

    expected_operations = {
        f"{entry['method']} {entry['path']}" for entry in operations["operations"]
    }
    covered_operations = {entry["operation"] for entry in coverage["api_operations"]}
    assert covered_operations == expected_operations

    vip = next(
        entry for entry in coverage["api_operations"] if entry["operation"] == "GET /data/vip/{id}"
    )
    assert vip["status"] == "observed_broken"
    assert vip["fallbacks"]
    assert all(fallback["evidence"] for fallback in vip["fallbacks"])

    expected_datasets = {entry["path"] for entry in downloads["files"]}
    covered_datasets = {entry["dataset_id"] for entry in coverage["datasets"]}
    assert len(expected_datasets) == downloads["file_count"] == 120
    assert covered_datasets == expected_datasets

    dimensions = {
        "discovered",
        "acquired",
        "parsed",
        "searchable",
        "field_complete",
        "mcp_retrievable",
    }
    for entry in coverage["datasets"]:
        assert dimensions <= entry.keys()
        assert entry["source_date"]
        assert entry["evidence"]
        assert entry["mcp_retrievable"] is False

    required_families = {
        "allele_haplotype",
        "allele_function",
        "allele_frequency",
        "cpic_gene",
        "prescribing",
        "vip",
        "labels_fda",
        "amp",
        "publications",
        "pathways",
    }
    assert {entry["family"] for entry in coverage["website_families"]} == required_families
    assert coverage["website_operation_registry"] == "api/website_operations.json"
