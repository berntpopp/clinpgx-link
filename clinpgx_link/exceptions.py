"""Safe domain exceptions mapped by the future MCP boundary."""

from __future__ import annotations

from typing import ClassVar, Literal

ErrorCode = Literal[
    "invalid_input",
    "not_found",
    "ambiguous_query",
    "upstream_unavailable",
    "rate_limited",
    "internal",
]

ERROR_CODES: frozenset[str] = frozenset(
    {
        "invalid_input",
        "not_found",
        "ambiguous_query",
        "upstream_unavailable",
        "rate_limited",
        "internal",
    }
)


class ClinPGxError(Exception):
    """Base for developer-authored, payload-free domain failures."""

    error_code: ClassVar[ErrorCode] = "internal"
    retryable: ClassVar[bool] = False
    default_subtype: ClassVar[str | None] = None

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        hint: str | None = None,
        subtype: str | None = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.hint = hint
        self.subtype = subtype if subtype is not None else self.default_subtype


class InvalidInputError(ClinPGxError):
    """The caller supplied a value outside the supported contract."""

    error_code = "invalid_input"


class NotFoundError(ClinPGxError):
    """A valid selector has no matching source record."""

    error_code = "not_found"


class AmbiguousQueryError(ClinPGxError):
    """A selector matched multiple records where exactly one was required."""

    error_code = "ambiguous_query"


class UpstreamUnavailableError(ClinPGxError):
    """A transient source or required local-data dependency is unavailable."""

    error_code = "upstream_unavailable"
    retryable = True


class RateLimitedError(ClinPGxError):
    """The upstream or local scheduler rejected work due to bounded capacity."""

    error_code = "rate_limited"
    retryable = True


class DataValidationError(UpstreamUnavailableError):
    """The source responded, but its bytes or structure failed validation."""

    default_subtype = "data_invalid"


class ResponseTooLargeError(InvalidInputError):
    """A complete response exceeds a bound and requires a narrower request."""

    default_subtype = "response_too_large"


__all__ = [
    "ERROR_CODES",
    "AmbiguousQueryError",
    "ClinPGxError",
    "DataValidationError",
    "ErrorCode",
    "InvalidInputError",
    "NotFoundError",
    "RateLimitedError",
    "ResponseTooLargeError",
    "UpstreamUnavailableError",
]
