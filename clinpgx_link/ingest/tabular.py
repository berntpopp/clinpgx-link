"""Strict streaming TSV/CSV reader preserving source field names and values."""

from __future__ import annotations

import csv
import io
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO

from clinpgx_link.exceptions import DataValidationError, InvalidInputError

_CSV_LIMIT_LOCK = threading.Lock()


@contextmanager
def _field_limit(limit: int) -> Iterator[None]:
    with _CSV_LIMIT_LOCK:
        previous = csv.field_size_limit()
        csv.field_size_limit(limit)
        try:
            yield
        finally:
            csv.field_size_limit(previous)


@dataclass(frozen=True)
class TabularRow:
    ordinal: int
    fields: dict[str, str]


class TabularReader:
    """One-pass strict reader for a UTF-8 delimited source member."""

    def __init__(
        self,
        stream: BinaryIO,
        *,
        delimiter: str,
        max_field_chars: int = 1_000_000,
    ) -> None:
        if delimiter not in {"\t", ","}:
            raise InvalidInputError("Delimiter must be tab or comma", field="delimiter")
        if max_field_chars <= 0:
            raise InvalidInputError("Field limit must be positive", field="max_field_chars")
        self._max_field_chars = max_field_chars
        self._text = io.TextIOWrapper(stream, encoding="utf-8-sig", errors="strict", newline="")
        self._reader = csv.reader(self._text, delimiter=delimiter, strict=True)
        try:
            with _field_limit(max_field_chars):
                header = next(self._reader)
        except (StopIteration, UnicodeDecodeError, csv.Error) as exc:
            raise DataValidationError("Tabular member has no valid UTF-8 header") from exc
        if not header or any(not field for field in header):
            raise DataValidationError("Tabular member has an empty header")
        if len(header) != len(set(header)):
            raise DataValidationError("Tabular member has duplicate headers")
        self.headers = tuple(header)

    def __iter__(self) -> Iterator[TabularRow]:
        try:
            with _field_limit(self._max_field_chars):
                for ordinal, values in enumerate(self._reader, start=1):
                    if len(values) != len(self.headers):
                        raise DataValidationError(
                            "Tabular row width differs from the source header"
                        )
                    yield TabularRow(
                        ordinal=ordinal, fields=dict(zip(self.headers, values, strict=True))
                    )
        except (UnicodeDecodeError, csv.Error) as exc:
            raise DataValidationError("Tabular member cannot be decoded strictly") from exc


__all__ = ["TabularReader", "TabularRow"]
