"""Small bounded cache for decoded API results backed by pinned content references."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from clinpgx_link.models import SourceResponse


def request_cache_key(
    namespace: str,
    method: str,
    path: str,
    params: dict[str, Any] | None,
    form: dict[str, Any] | None,
    representation: str,
) -> str:
    """Hash a complete validated request without rendering its arguments in diagnostics."""
    serialized = json.dumps(
        [namespace, method, path, params or {}, form or {}, representation],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


@dataclass
class _Entry:
    response: SourceResponse
    size: int
    expires: float


class ResponseCache:
    """TTL/LRU cache that never owns or evicts the exact source bytes."""

    def __init__(
        self,
        *,
        max_entries: int,
        max_bytes: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._ttl = ttl_seconds
        self._clock = clock
        self._size = 0

    def _expire(self) -> None:
        now = self._clock()
        for key in [key for key, entry in self._entries.items() if entry.expires <= now]:
            self._size -= self._entries.pop(key).size

    def get(self, key: str) -> SourceResponse | None:
        self._expire()
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        response = copy.deepcopy(entry.response)
        response.source = replace(response.source, data_source="cache")
        response.details["cache_hit"] = True
        return response

    def put(self, key: str, response: SourceResponse, *, size: int) -> bool:
        self._expire()
        if size > self._max_bytes:
            return False
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._size -= previous.size
        while self._entries and (
            len(self._entries) >= self._max_entries or self._size + size > self._max_bytes
        ):
            _, removed = self._entries.popitem(last=False)
            self._size -= removed.size
        self._entries[key] = _Entry(copy.deepcopy(response), size, self._clock() + self._ttl)
        self._size += size
        return True


__all__ = ["ResponseCache", "request_cache_key"]
