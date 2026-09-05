"""Bounded scalar projection primitives for MCP source responses.

Entries returned here are internal values. Source-authored pointers and strings
still require fencing at the MCP wire boundary.

Array traversal accepts only RFC 6901 array indices (``0`` or a nonzero decimal
integer without leading zeroes). A syntactically valid index beyond the array is
absent; ``-``, leading-zero, and other non-index tokens are invalid for arrays,
matching the strict content reader rather than applying JSON Patch semantics.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeGuard

from clinpgx_link.exceptions import DataValidationError, InvalidInputError

_MAX_POINTERS = 12
_MAX_POINTER_CHARACTERS = 4096
_MAX_POINTER_SEGMENTS = 128
_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")
_MALFORMED_ESCAPE = re.compile(r"~(?![01])")
_MISSING = object()

type JsonScalar = str | int | float | bool | None
type SelectionStatus = Literal["value", "absent", "deferred"]


@dataclass(frozen=True, slots=True)
class ScalarSelection:
    """One ordered scalar result; status distinguishes stored null from absence."""

    pointer: str
    status: SelectionStatus
    value: JsonScalar = None
    byte_length: int | None = None
    sha256: str | None = None


def _invalid_pointers(hint: str) -> InvalidInputError:
    return InvalidInputError("Invalid scalar pointer selection.", field="pointers", hint=hint)


def _tokens(pointer: str) -> tuple[str, ...]:
    if (
        (pointer and not pointer.startswith("/"))
        or len(pointer) > _MAX_POINTER_CHARACTERS
        or pointer.count("/") > _MAX_POINTER_SEGMENTS
    ):
        raise _invalid_pointers("Use bounded RFC 6901 pointers from structure discovery.")
    try:
        pointer.encode("utf-8")
    except UnicodeError as exc:
        raise _invalid_pointers("Use bounded RFC 6901 pointers encoded as UTF-8.") from exc
    if not pointer:
        return ()
    encoded_tokens = pointer[1:].split("/")
    if any(_MALFORMED_ESCAPE.search(token) for token in encoded_tokens):
        raise _invalid_pointers("Use RFC 6901 escapes: slash is ~1 and tilde is ~0.")
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in encoded_tokens)


def validate_pointers(
    pointers: Sequence[str] | None,
    *,
    pointer: str = "",
    cursor: str | None = None,
    offset: int = 0,
    include_fields: Sequence[str] | None = None,
) -> tuple[str, ...] | None:
    """Validate one ordered multi-pointer request before source acquisition.

    ``None`` means no scalar selection. An explicit selection is mutually
    exclusive with legacy single-pointer, continuation, offset, and field-profile
    selection options.
    """
    if pointers is None:
        return None
    if isinstance(pointers, (str, bytes)) or not isinstance(pointers, Sequence):
        raise _invalid_pointers("Provide an ordered list of 1 through 12 unique pointers.")
    if not 1 <= len(pointers) <= _MAX_POINTERS:
        raise _invalid_pointers("Provide an ordered list of 1 through 12 unique pointers.")

    validated: list[str] = []
    seen: set[str] = set()
    for candidate in pointers:
        if not isinstance(candidate, str):
            raise _invalid_pointers("Every selection must be an RFC 6901 pointer string.")
        _tokens(candidate)
        if candidate in seen:
            raise _invalid_pointers("Pointers must be unique.")
        seen.add(candidate)
        validated.append(candidate)

    if pointer or cursor or offset != 0 or include_fields:
        raise _invalid_pointers(
            "Do not combine pointers with pointer, cursor, offset, or include_fields."
        )
    return tuple(validated)


def finite_json_bytes(value: Any) -> bytes:
    """Return deterministic finite UTF-8 JSON or a typed source-data failure."""
    try:
        _validate_json_domain(value)
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise DataValidationError("Source value is not finite UTF-8 JSON.") from exc


def _is_json_scalar(value: Any) -> TypeGuard[JsonScalar]:
    return value is None or type(value) in {str, int, float, bool}


def _validate_json_domain(value: Any) -> None:
    """Reject Python values that json.dumps would coerce outside the JSON domain."""
    stack: list[tuple[Any, bool]] = [(value, False)]
    active_containers: set[int] = set()
    while stack:
        item, leaving = stack.pop()
        if leaving:
            active_containers.remove(id(item))
            continue
        if _is_json_scalar(item):
            if isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError("nonfinite JSON number")
            continue
        if type(item) not in {dict, list}:
            raise TypeError("value is outside the JSON domain")
        identity = id(item)
        if identity in active_containers:
            raise ValueError("cyclic JSON value")
        active_containers.add(identity)
        stack.append((item, True))
        if isinstance(item, dict):
            for key, child in item.items():
                if type(key) is not str:
                    raise TypeError("JSON object key is not a string")
                key.encode("utf-8")
                stack.append((child, False))
        else:
            stack.extend((child, False) for child in item)


def _resolve(value: Any, tokens: tuple[str, ...]) -> Any:
    current = value
    for token in tokens:
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
            continue
        if isinstance(current, list):
            if not _ARRAY_INDEX.fullmatch(token) or len(token) > 12:
                raise _invalid_pointers(
                    "Array tokens must be canonical nonnegative decimal indices."
                )
            index = int(token)
            if index >= len(current):
                return _MISSING
            current = current[index]
            continue
        return _MISSING
    return current


def resolve_scalars(
    value: Any,
    pointers: Sequence[str],
    *,
    max_inline_bytes: int = 12000,
) -> tuple[ScalarSelection, ...]:
    """Resolve selected scalars and greedily spend a finite-JSON byte budget.

    Every pointer is syntax-checked before traversal. Resolution and finite JSON
    validation finish for the entire request before any ordered result is built,
    so selecting one object or array rejects the whole call.
    """
    validated = validate_pointers(pointers)
    assert validated is not None
    if type(max_inline_bytes) is not int or max_inline_bytes < 0:
        raise InvalidInputError(
            "Invalid scalar inline budget.",
            field="max_inline_bytes",
            hint="Use a nonnegative integer byte budget.",
        )

    resolved: list[tuple[str, Any]] = []
    parsed = tuple((pointer, _tokens(pointer)) for pointer in validated)
    for pointer, tokens in parsed:
        selected = _resolve(value, tokens)
        if isinstance(selected, (dict, list)):
            raise InvalidInputError(
                "Scalar selection cannot return objects or arrays.",
                field="pointers",
                hint="Select scalar children discovered through structure retrieval.",
                subtype="scalar_selection_required",
            )
        if selected is not _MISSING and not _is_json_scalar(selected):
            raise DataValidationError("Source value is not finite UTF-8 JSON.")
        resolved.append((pointer, selected))

    serialized = tuple(
        (pointer, selected, None if selected is _MISSING else finite_json_bytes(selected))
        for pointer, selected in resolved
    )

    remaining = max_inline_bytes
    entries: list[ScalarSelection] = []
    for pointer, selected, raw in serialized:
        if selected is _MISSING:
            entries.append(ScalarSelection(pointer=pointer, status="absent"))
        elif raw is not None and len(raw) <= remaining:
            entries.append(ScalarSelection(pointer=pointer, status="value", value=selected))
            remaining -= len(raw)
        else:
            assert raw is not None
            entries.append(
                ScalarSelection(
                    pointer=pointer,
                    status="deferred",
                    byte_length=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest(),
                )
            )
    return tuple(entries)


__all__ = [
    "JsonScalar",
    "ScalarSelection",
    "SelectionStatus",
    "finite_json_bytes",
    "resolve_scalars",
    "validate_pointers",
]
