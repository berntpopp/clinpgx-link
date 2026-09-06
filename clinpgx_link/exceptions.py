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

# Only code-owned values in this registry may cross the public MCP boundary.
PUBLIC_ERROR_SUBTYPES: frozenset[str] = frozenset(
    {
        "admission_capacity",
        "array_index_invalid",
        "asset_invalid",
        "asset_missing",
        "base64_pointer_unsupported",
        "conflicting_api_filters",
        "content_capacity",
        "content_expired",
        "content_missing",
        "cursor_expired",
        "data_invalid",
        "unsupported_dataset_filters",
        "dataset_unavailable",
        "documented_broken_operation",
        "execution_deadline",
        "field_selection_unsupported",
        "invalid_cursor",
        "json_pointer_required",
        "json_selection_required",
        "lock_timeout",
        "manifest_digest",
        "manifest_invalid",
        "membership_profile_mismatch",
        "missing_criteria",
        "numeric_detail_id_required",
        "operation_unavailable",
        "pointer_selection_invalid",
        "pointer_syntax_invalid",
        "profile_drift",
        "publication_not_allowed",
        "release_collision",
        "release_identity_mismatch",
        "release_key_invalid",
        "resource_limit",
        "response_too_large",
        "rights_invalid",
        "runtime_identity_changed",
        "runtime_identity_invalid",
        "runtime_identity_mismatch",
        "runtime_identity_unsafe",
        "runtime_inventory",
        "runtime_manifest_invalid",
        "scalar_selection_required",
        "selection_commit_failed",
        "selection_recovery_failed",
        "snapshot_invalid",
        "snapshot_mismatch",
        "snapshot_not_ready",
        "text_selection_required",
        "unsupported_api_filter_value",
        "unsupported_api_filters",
        "unsupported_related_mode",
        "unsupported_search_source",
        "upstream_throttle",
        "wildcard_query_unsupported",
    }
)

SELECTION_FAILURE_REASONS: frozenset[str] = frozenset(
    {
        "container_selected",
        "duplicate_pointer",
        "invalid_array_index",
        "malformed_rfc6901_pointer",
        "pointer_not_string",
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
        selection_index: int | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.hint = hint
        self.subtype = subtype if subtype is not None else self.default_subtype
        self.selection_index = selection_index
        self.reason = reason


class InvalidInputError(ClinPGxError):
    """The caller supplied a value outside the supported contract."""

    error_code = "invalid_input"


class DatasetFilterError(InvalidInputError):
    """A validated installed dataset has no contract for the supplied filter."""

    default_subtype = "unsupported_dataset_filters"

    def __init__(self, *, dataset_id: str, known_filters: tuple[str, ...]) -> None:
        super().__init__(
            "A dataset filter is outside the selected dataset/member contract.",
            field="filters",
        )
        self.dataset_id = dataset_id
        self.known_filters = known_filters


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


class RecoverableContentError(InvalidInputError):
    """A content failure with a server-validated exact-byte alternative."""

    def __init__(self, *, content_ref: str) -> None:
        super().__init__(
            "Exact original bytes require an empty pointer.",
            field="pointer",
            hint="Use an empty pointer with base64 to retrieve the original body bytes.",
            subtype="base64_pointer_unsupported",
        )
        self.content_ref = content_ref


class RecoverableSelectionError(InvalidInputError):
    """A selection failure with a server-validated retained JSON alternative."""

    def __init__(
        self,
        *,
        content_ref: str,
        subtype: str | None,
        selection_index: int | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(
            "Scalar selection requires a scalar source value.",
            field="pointers",
            hint="Inspect the retained JSON structure and select scalar children.",
            subtype=subtype,
            selection_index=selection_index,
            reason=reason,
        )
        self.content_ref = content_ref
        self.recovery_pointer = ""


__all__ = [
    "ERROR_CODES",
    "PUBLIC_ERROR_SUBTYPES",
    "SELECTION_FAILURE_REASONS",
    "AmbiguousQueryError",
    "ClinPGxError",
    "DataValidationError",
    "DatasetFilterError",
    "ErrorCode",
    "InvalidInputError",
    "NotFoundError",
    "RateLimitedError",
    "RecoverableContentError",
    "RecoverableSelectionError",
    "ResponseTooLargeError",
    "UpstreamUnavailableError",
]
