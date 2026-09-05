"""Payload-free structured logging with request correlation."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any, TextIO

import structlog

from . import __version__
from .config import settings

if TYPE_CHECKING:
    from structlog.typing import FilteringBoundLogger

_SERVICE_NAME = "clinpgx-link"
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


def _payload_free(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Retain only bounded, developer-owned operational fields."""
    if event_dict.get("exc_info"):
        exc_info = event_dict["exc_info"]
        if isinstance(exc_info, tuple) and exc_info and isinstance(exc_info[0], type):
            event_dict["exception_type"] = exc_info[0].__name__
    return {key: value for key, value in event_dict.items() if key in _SAFE_EVENT_FIELDS}


def _add_static_fields(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict["service"] = _SERVICE_NAME
    event_dict["version"] = __version__
    return event_dict


def bind_request_id(request_id: str) -> None:
    """Bind a validated request identifier to subsequent events in this context."""
    structlog.contextvars.bind_contextvars(request_id=request_id)


def clear_request_context() -> None:
    """Clear correlation fields after a request finishes."""
    structlog.contextvars.clear_contextvars()


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
    root.handlers.clear()
    root.setLevel(getattr(logging, resolved_level))
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)

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
