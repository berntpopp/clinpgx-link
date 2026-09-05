"""Payload-free structured logging with request correlation."""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

import structlog

from . import __version__
from .config import settings

if TYPE_CHECKING:
    from structlog.typing import FilteringBoundLogger

_SERVICE_NAME = "clinpgx-link"
_INVALID_EVENT = "invalid_event"
_INVALID_REQUEST_ID = "invalid"
_SAFE_EVENT_FIELDS = frozenset(
    {
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
)
_EVENTS = frozenset(
    {
        _INVALID_EVENT,
        "cache_lookup",
        "dependency_event",
        "request_complete",
        "request_failed",
        "request_started",
        "server_started",
        "server_stopped",
        "snapshot_opened",
        "snapshot_validation_failed",
        "upstream_complete",
        "upstream_failed",
        "upstream_started",
    }
)
_DEVELOPER_OPERATIONS = frozenset(
    {
        "api.call",
        "api.describe",
        "content.get",
        "content.put",
        "dataset.describe",
        "dataset.get_record",
        "dataset.list",
        "dataset.related",
        "dataset.search",
        "server.health",
    }
)
_DATA_SOURCES = frozenset({"api", "cache", "download", "snapshot", "website"})
_SOURCES = frozenset(
    {
        "clinpgx_api",
        "clinpgx_download",
        "clinpgx_website",
        "content_store",
        "local_snapshot",
        "mcp",
    }
)
_ERROR_CODES = frozenset(
    {
        "ambiguous_query",
        "internal",
        "invalid_input",
        "not_found",
        "rate_limited",
        "upstream_unavailable",
    }
)
_EXCEPTION_TYPES = frozenset(
    {
        "AmbiguousQueryError",
        "ClinPGxError",
        "DataValidationError",
        "InvalidInputError",
        "NotFoundError",
        "RateLimitedError",
        "ResponseTooLargeError",
        "UpstreamUnavailableError",
        "other_exception",
    }
)
_LOG_LEVELS = frozenset({"critical", "debug", "error", "info", "warning"})
_METHODS = frozenset({"GET", "HEAD", "POST"})
_STATUSES = frozenset(
    {"cache_hit", "cache_miss", "error", "failed", "ready", "success", "unavailable"}
)
_RELEASE_TAG = re.compile(r"data-clinpgx-(?:core|extended)-[0-9a-f]{16}\Z")
_SNAPSHOT_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z\Z")
_VERSION = re.compile(r"\d+\.\d+\.\d+(?:[+-][0-9A-Za-z.-]+)?\Z")
_DROP = object()
_DEPENDENCY_LOGGER_ROOTS = (
    "fastapi",
    "fastmcp",
    "httpcore",
    "httpcore2",
    "httpx",
    "httpx2",
    "mcp",
    "starlette",
    "uvicorn",
)
_BASE_LOG_RECORD_FACTORY = logging.getLogRecordFactory()


def _registry_operations() -> frozenset[str]:
    """Load the developer-vendored operation vocabulary, failing closed."""
    path = Path(__file__).with_name("api") / "operations.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        operations = document["operations"]
        return frozenset(
            entry["operation"]
            for entry in operations
            if isinstance(entry, dict) and isinstance(entry.get("operation"), str)
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return frozenset()


_OPERATIONS = _registry_operations() | _DEVELOPER_OPERATIONS


def _canonical_request_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return None
    canonical = str(parsed)
    return canonical if value == canonical else None


def _bounded_number(value: object, minimum: float, maximum: float) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return minimum <= value <= maximum
    if isinstance(value, float):
        return math.isfinite(value) and minimum <= value <= maximum
    return False


def _safe_value(key: str, value: object) -> object:
    """Return a safe scalar for one known field, or a private drop marker."""
    vocabularies = {
        "data_source": _DATA_SOURCES,
        "error_code": _ERROR_CODES,
        "exception_type": _EXCEPTION_TYPES,
        "log_level": _LOG_LEVELS,
        "method": _METHODS,
        "operation": _OPERATIONS,
        "source": _SOURCES,
        "status": _STATUSES,
    }
    if key == "event":
        return value if isinstance(value, str) and value in _EVENTS else _INVALID_EVENT
    if key == "request_id":
        return _canonical_request_id(value) or _INVALID_REQUEST_ID
    if key in vocabularies:
        return value if isinstance(value, str) and value in vocabularies[key] else _DROP
    if key == "release_tag":
        return value if isinstance(value, str) and _RELEASE_TAG.fullmatch(value) else _DROP
    if key == "snapshot_id":
        return value if isinstance(value, str) and _SNAPSHOT_ID.fullmatch(value) else _DROP
    if key == "service":
        return value if value == _SERVICE_NAME else _DROP
    if key == "timestamp":
        return value if isinstance(value, str) and _TIMESTAMP.fullmatch(value) else _DROP
    if key == "version":
        return value if isinstance(value, str) and _VERSION.fullmatch(value) else _DROP
    if key == "cache_hit":
        return value if isinstance(value, bool) else _DROP
    if key == "elapsed_ms":
        return value if _bounded_number(value, 0, 86_400_000) else _DROP
    if key == "record_count":
        return value if _bounded_number(value, 0, 20_000_000) and isinstance(value, int) else _DROP
    if key == "retry_count":
        return value if _bounded_number(value, 0, 10) and isinstance(value, int) else _DROP
    if key == "status_code":
        return value if _bounded_number(value, 100, 599) and isinstance(value, int) else _DROP
    return _DROP


def _payload_free(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Retain only bounded, developer-owned operational fields."""
    if event_dict.get("exc_info"):
        exc_info = event_dict["exc_info"]
        if isinstance(exc_info, tuple) and exc_info and isinstance(exc_info[0], type):
            exception_name = exc_info[0].__name__
            event_dict["exception_type"] = (
                exception_name if exception_name in _EXCEPTION_TYPES else "other_exception"
            )
    retained: dict[str, Any] = {}
    for key, value in event_dict.items():
        if key not in _SAFE_EVENT_FIELDS:
            continue
        safe = _safe_value(key, value)
        if safe is not _DROP:
            retained[key] = safe
    return retained


def _add_static_fields(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict["service"] = _SERVICE_NAME
    event_dict["version"] = __version__
    return event_dict


def bind_request_id(request_id: str) -> None:
    """Bind a validated request identifier to subsequent events in this context."""
    structlog.contextvars.bind_contextvars(
        request_id=_canonical_request_id(request_id) or _INVALID_REQUEST_ID
    )


def clear_request_context() -> None:
    """Clear correlation fields after a request finishes."""
    structlog.contextvars.clear_contextvars()


class _PayloadFreeStdlibFormatter(logging.Formatter):
    """Replace dependency-owned messages and tracebacks with fixed operational data."""

    def __init__(self, log_format: str) -> None:
        super().__init__()
        self._log_format = log_format

    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname.lower()
        if level not in _LOG_LEVELS:
            level = "info"
        event: dict[str, str] = {
            "event": "dependency_event",
            "log_level": level,
            "service": _SERVICE_NAME,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "version": __version__,
        }
        request_id = _canonical_request_id(
            structlog.contextvars.get_contextvars().get("request_id")
        )
        if request_id is not None:
            event["request_id"] = request_id
        if self._log_format == "json":
            return json.dumps(event, sort_keys=True, separators=(",", ":"))
        fields = " ".join(f"{key}={value}" for key, value in sorted(event.items()))
        return fields


def _payload_free_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    """Erase dependency messages before any attached handler or telemetry can observe them."""
    record = _BASE_LOG_RECORD_FACTORY(*args, **kwargs)
    if any(
        record.name == root or record.name.startswith(root + ".")
        for root in _DEPENDENCY_LOGGER_ROOTS
    ):
        record.msg = "dependency_event"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
    return record


def _route_dependency_loggers_to_root() -> None:
    """Remove dependency handlers that could bypass the payload-free root formatter."""
    names = set(_DEPENDENCY_LOGGER_ROOTS)
    names.update(
        name
        for name in logging.Logger.manager.loggerDict
        if any(name == root or name.startswith(root + ".") for root in _DEPENDENCY_LOGGER_ROOTS)
    )
    for name in names:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


def configure_logging(
    level: str | None = None,
    log_format: str | None = None,
    *,
    stream: TextIO | None = None,
) -> FilteringBoundLogger:
    """Configure stdlib and structlog, returning the package logger."""
    resolved_level = (level or settings.log_level).upper()
    resolved_format = (log_format or settings.log_format).lower()
    output = stream or sys.stderr

    root = logging.getLogger()
    logging.setLogRecordFactory(_payload_free_record_factory)
    root.handlers.clear()
    root.setLevel(getattr(logging, resolved_level))
    handler = logging.StreamHandler(output)
    handler.setFormatter(_PayloadFreeStdlibFormatter(resolved_format))
    root.addHandler(handler)
    _route_dependency_loggers_to_root()

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_static_fields,
        _payload_free,
    ]
    if resolved_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, resolved_level)),
        logger_factory=structlog.PrintLoggerFactory(file=output),
        cache_logger_on_first_use=False,
    )
    return get_server_logger()


def get_server_logger() -> FilteringBoundLogger:
    """Return the configured package logger."""
    return structlog.get_logger("clinpgx_link")  # type: ignore[no-any-return]


__all__ = ["bind_request_id", "clear_request_context", "configure_logging", "get_server_logger"]
