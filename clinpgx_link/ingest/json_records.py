"""Incremental JSON record discovery with stable RFC 6901 provenance pointers."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO

import ijson  # type: ignore[import-untyped]

from clinpgx_link.exceptions import DataValidationError

LOSSLESS_JSON_NUMBER_KEY = "$clinpgxJsonNumber"


class _RejectDuplicates(dict[str, Any]):
    def __setitem__(self, key: str, value: Any) -> None:
        if key in self:
            raise DataValidationError("JSON member contains a duplicate object key")
        super().__setitem__(key, value)


@dataclass(frozen=True)
class JsonRecord:
    pointer: str
    parent_pointer: str | None
    value: Any


def _escape(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def _nested_records(
    value: Any,
    *,
    base_pointer: str,
    parent_pointer: str,
    depth: int,
) -> Iterator[JsonRecord]:
    if depth > 128:
        raise DataValidationError("JSON nesting exceeds the supported depth")
    if isinstance(value, dict):
        for key, child in value.items():
            child_base = f"{base_pointer}/{_escape(key)}"
            if isinstance(child, list):
                for index, item in enumerate(child):
                    pointer = f"{child_base}/{index}"
                    yield JsonRecord(pointer=pointer, parent_pointer=parent_pointer, value=item)
                    yield from _nested_records(
                        item,
                        base_pointer=pointer,
                        parent_pointer=pointer,
                        depth=depth + 1,
                    )
            elif isinstance(child, dict):
                yield from _nested_records(
                    child,
                    base_pointer=child_base,
                    parent_pointer=parent_pointer,
                    depth=depth + 1,
                )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            pointer = f"{base_pointer}/{index}"
            yield JsonRecord(pointer=pointer, parent_pointer=parent_pointer, value=item)
            yield from _nested_records(
                item,
                base_pointer=pointer,
                parent_pointer=pointer,
                depth=depth + 1,
            )


def _root_kind(stream: BinaryIO) -> str:
    try:
        start = stream.tell()
        while byte := stream.read(1):
            if byte not in b" \t\r\n":
                stream.seek(start)
                if byte == b"[":
                    return "array"
                if byte == b"{":
                    return "object"
                raise DataValidationError("JSON member root must be an object or array")
        stream.seek(start)
    except (OSError, AttributeError) as exc:
        raise DataValidationError("JSON source stream must be seekable") from exc
    raise DataValidationError("JSON member is empty")


def _items(stream: BinaryIO, prefix: str) -> Iterator[Any]:
    try:
        yield from ijson.items(
            stream,
            prefix,
            map_type=_RejectDuplicates,
            use_float=False,
        )
    except DataValidationError:
        raise
    except (ijson.JSONError, UnicodeDecodeError, ValueError, OverflowError) as exc:
        raise DataValidationError("JSON member cannot be decoded strictly") from exc


def iter_json_records(stream: BinaryIO) -> Iterator[JsonRecord]:
    """Yield root/list records incrementally while retaining their parent pointers."""
    kind = _root_kind(stream)
    if kind == "array":
        for index, value in enumerate(_items(stream, "item")):
            pointer = f"/{index}"
            yield JsonRecord(pointer=pointer, parent_pointer="", value=value)
            yield from _nested_records(
                value,
                base_pointer=pointer,
                parent_pointer=pointer,
                depth=1,
            )
        return
    roots = _items(stream, "")
    try:
        root = next(roots)
    except StopIteration as exc:
        raise DataValidationError("JSON object root could not be decoded") from exc
    try:
        next(roots)
    except StopIteration:
        pass
    else:
        raise DataValidationError("JSON member contains multiple root values")
    yield JsonRecord(pointer="", parent_pointer=None, value=root)
    yield from _nested_records(root, base_pointer="", parent_pointer="", depth=1)


__all__ = ["LOSSLESS_JSON_NUMBER_KEY", "JsonRecord", "iter_json_records"]
