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
from typing import Any

from clinpgx_link.exceptions import DataValidationError, InvalidInputError


def _invalid(field: str, hint: str) -> InvalidInputError:
    return InvalidInputError("Unsupported content selection.", field=field, hint=hint)


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
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            item.encode("utf-8")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Nonfinite JSON number.")
        elif isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return value


def _select(value: Any, pointer: str) -> Any:
    if not pointer:
        return value
    if not pointer.startswith("/") or len(pointer) > 4096 or pointer.count("/") > 128:
        raise _invalid("pointer", "Use a bounded RFC 6901 pointer from structure discovery.")
    for token in pointer[1:].split("/"):
        if re.search(r"~(?![01])", token):
            raise _invalid("pointer", "Escape slash as ~1 and tilde as ~0.")
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key):
            if len(key) > 12 or int(key) >= len(value):
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
        return {"type": "string", "length": len(value)}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    return {"type": "number"}


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

    Text output is intentionally raw here: the caller must fence/sanitize it. For a
    JSON pointer, base64 returns the UTF-8 string or canonical selected JSON value,
    explicitly marked derived; the original serialized member is available at root.
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
    if representation == "base64" and not pointer:
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
        raise _invalid("pointer", "Pointers require a JSON source representation.")
    value = _select(value, pointer)
    if representation == "structure":
        descriptor = _describe(value)
        if isinstance(value, (dict, list)):
            keys = list(value) if isinstance(value, dict) else list(range(len(value)))
            page = _page(len(keys), start, length)
            items = []
            for key in keys[start : start + length]:
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                items.append(
                    {"key": key, "pointer": f"{pointer}/{escaped}", **_describe(value[key])}
                )
            return {**metadata, **descriptor, **page, "sha256": raw_digest, "items": items}
        if start:
            raise _invalid("start", "Scalar structure descriptors have no continuation.")
        return {**metadata, **descriptor, "sha256": raw_digest}
    if representation == "text":
        if not isinstance(value, str):
            raise _invalid("pointer", "Select a string value using structure discovery.")
        return {
            **metadata,
            **_page(len(value), start, length),
            "unit": "characters",
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            "text": value[start : start + length],
        }
    selected = (
        value
        if isinstance(value, str)
        else json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    ).encode("utf-8")
    return {
        **metadata,
        **_page(len(selected), start, length),
        "unit": "bytes",
        "derived": True,
        "sha256": hashlib.sha256(selected).hexdigest(),
        "base64": base64.b64encode(selected[start : start + length]).decode("ascii"),
    }
