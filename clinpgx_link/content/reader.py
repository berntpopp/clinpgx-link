"""Lossless source slicing; wire sanitization belongs to the MCP boundary.

Inputs have already passed acquisition limits. No network or filesystem access
occurs here. Base64 without a pointer ALWAYS represents the original body bytes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections.abc import Iterator
from itertools import islice
from typing import Any

from clinpgx_link.exceptions import DataValidationError, InvalidInputError, ResponseTooLargeError

_MAX_POINTER_CHARACTERS = 4096
_MAX_POINTER_SEGMENTS = 128
_MAX_STRUCTURE_DESCRIPTOR_BYTES = 32 * 1024


def _invalid(field: str, hint: str, *, subtype: str | None = None) -> InvalidInputError:
    return InvalidInputError(
        "Unsupported content selection.", field=field, hint=hint, subtype=subtype
    )


def _descriptor_too_large() -> ResponseTooLargeError:
    return ResponseTooLargeError(
        "Content structure descriptor exceeds the supported size.",
        field="pointer",
        hint="Use an empty pointer with representation='base64' to retrieve original body bytes.",
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        key.encode("utf-8")
        result[key] = value
    return result


def _decode_json(raw: bytes) -> Any:
    value: Any = json.loads(raw, object_pairs_hook=_unique_object)
    pending: list[Iterator[Any]] = [iter((value,))]
    while pending:
        try:
            item = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        if isinstance(item, str):
            item.encode("utf-8")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Nonfinite JSON number.")
        elif isinstance(item, dict):
            pending.append(iter(item.values()))
        elif isinstance(item, list):
            pending.append(iter(item))
    return value


def select_value(value: Any, pointer: str) -> Any:
    """Select a bounded RFC 6901 path from an already decoded JSON value."""
    if not pointer:
        return value
    if (
        not pointer.startswith("/")
        or len(pointer) > _MAX_POINTER_CHARACTERS
        or pointer.count("/") > _MAX_POINTER_SEGMENTS
    ):
        raise _invalid(
            "pointer",
            "Use a bounded RFC 6901 pointer from structure discovery.",
            subtype="pointer_syntax_invalid",
        )
    for token in pointer[1:].split("/"):
        if re.search(r"~(?![01])", token):
            raise _invalid(
                "pointer",
                "Escape slash as ~1 and tilde as ~0.",
                subtype="pointer_syntax_invalid",
            )
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list):
            if re.fullmatch(r"0|[1-9][0-9]*", key) is None or len(key) > 12:
                raise _invalid(
                    "pointer",
                    "Use a canonical nonnegative decimal array index.",
                    subtype="array_index_invalid",
                )
            if int(key) >= len(value):
                raise _invalid("pointer", "Choose an existing array index.")
            value = value[int(key)]
        else:
            raise _invalid("pointer", "Choose an existing child from structure discovery.")
    return value


def _describe(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"type": "object", "length": len(value)}
    if isinstance(value, list):
        return {"type": "array", "length": len(value)}
    if isinstance(value, str):
        selected = value.encode("utf-8")
        return {
            "type": "string",
            "length": len(value),
            "unit": "characters",
            "digest_representation": "utf8_decoded_string",
            "sha256": hashlib.sha256(selected).hexdigest(),
        }
    selected = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    if value is None:
        scalar_type = "null"
    elif isinstance(value, bool):
        scalar_type = "boolean"
    else:
        scalar_type = "number"
    return {
        "type": scalar_type,
        "length": len(selected),
        "unit": "bytes",
        "digest_representation": "canonical_json_scalar",
        "sha256": hashlib.sha256(selected).hexdigest(),
    }


def _page(total: int, start: int, length: int) -> dict[str, Any]:
    if start > total:
        raise _invalid("start", "Start must not exceed the reported total length.")
    stop = min(start + length, total)
    return {
        "start": start,
        "returned": stop - start,
        "total": total,
        "has_more": stop < total,
        "next_start": stop if stop < total else None,
    }


def _page_for_returned(total: int, start: int, returned: int) -> dict[str, Any]:
    next_start = start + returned
    return {
        "start": start,
        "returned": returned,
        "total": total,
        "has_more": next_start < total,
        "next_start": next_start if next_start < total else None,
    }


def _serialized_size(value: dict[str, Any]) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )


def _child_pointer(pointer: str, key: str | int) -> str:
    escaped = str(key).replace("~", "~0").replace("/", "~1")
    child = f"{pointer}/{escaped}"
    if len(child) > _MAX_POINTER_CHARACTERS or child.count("/") > _MAX_POINTER_SEGMENTS:
        raise _descriptor_too_large()
    return child


def _structure_page(
    metadata: dict[str, Any],
    descriptor: dict[str, Any],
    value: dict[str, Any] | list[Any],
    *,
    pointer: str,
    source_sha256: str,
    start: int,
    length: int,
) -> dict[str, Any]:
    total = len(value)
    if start > total:
        raise _invalid("start", "Start must not exceed the reported total length.")

    entries: Iterator[tuple[str | int, Any]]
    if isinstance(value, dict):
        entries = islice(value.items(), start, min(start + length, total))
    else:
        entries = ((index, value[index]) for index in range(start, min(start + length, total)))

    items: list[dict[str, Any]] = []
    for key, child_value in entries:
        child = _child_pointer(pointer, key)
        item = {"key": key, "pointer": child, **_describe(child_value)}
        candidate_items = [*items, item]
        candidate = {
            **metadata,
            **descriptor,
            **_page_for_returned(total, start, len(candidate_items)),
            "sha256": source_sha256,
            "items": candidate_items,
        }
        if _serialized_size(candidate) > _MAX_STRUCTURE_DESCRIPTOR_BYTES:
            if not items:
                raise _descriptor_too_large()
            break
        items.append(item)

    result = {
        **metadata,
        **descriptor,
        **_page_for_returned(total, start, len(items)),
        "sha256": source_sha256,
        "items": items,
    }
    if _serialized_size(result) > _MAX_STRUCTURE_DESCRIPTOR_BYTES:
        raise _descriptor_too_large()
    return result


def read_content(
    raw: bytes,
    *,
    media_type: str,
    pointer: str = "",
    representation: str = "structure",
    start: int = 0,
    length: int = 4096,
) -> dict[str, Any]:
    """Read a bounded structure, text or exact-byte slice of one acquired source.

    Text output is intentionally raw here: the caller must fence/sanitize it. Base64
    only accepts the empty pointer and always returns original body bytes.
    """
    if type(start) is not int or type(length) is not int or start < 0 or not 1 <= length <= 8192:
        raise _invalid("start", "Use a nonnegative start and a length from 1 through 8192.")
    if representation not in {"structure", "text", "base64"}:
        raise _invalid("representation", "Choose structure, text or base64.")
    raw_digest = hashlib.sha256(raw).hexdigest()
    metadata: dict[str, Any] = {
        "source_sha256": raw_digest,
        "media_type": media_type,
        "pointer": pointer,
        "representation": representation,
    }
    if representation == "base64":
        if pointer:
            raise _invalid(
                "pointer",
                "Use an empty pointer with base64 to retrieve the original body bytes.",
                subtype="base64_pointer_unsupported",
            )
        return {
            **metadata,
            **_page(len(raw), start, length),
            "sha256": raw_digest,
            "unit": "bytes",
            "derived": False,
            "base64": base64.b64encode(raw[start : start + length]).decode("ascii"),
        }
    mime = media_type.split(";", 1)[0].strip().lower()
    is_json = mime == "application/json" or mime.endswith("+json")
    if not is_json and not mime.startswith("text/"):
        raise _invalid("representation", "Use base64 for original binary bytes.")
    try:
        value: Any = _decode_json(raw) if is_json else raw.decode("utf-8")
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DataValidationError("Source content cannot be decoded.") from exc
    if pointer and not is_json:
        raise _invalid(
            "pointer",
            "Pointers require a JSON source representation.",
            subtype="json_pointer_required",
        )
    value = select_value(value, pointer)
    if representation == "structure":
        descriptor = _describe(value)
        if isinstance(value, (dict, list)):
            return _structure_page(
                metadata,
                descriptor,
                value,
                pointer=pointer,
                source_sha256=raw_digest,
                start=start,
                length=length,
            )
        if start:
            raise _invalid("start", "Scalar structure descriptors have no continuation.")
        return {**metadata, **descriptor}
    if not isinstance(value, str):
        raise _invalid(
            "pointer",
            "Select a string value using structure discovery.",
            subtype="text_selection_required",
        )
    return {
        **metadata,
        **_page(len(value), start, length),
        "unit": "characters",
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "text": value[start : start + length],
    }
