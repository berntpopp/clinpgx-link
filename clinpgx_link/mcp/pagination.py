"""Process-scoped authenticated cursors over immutable response/snapshot identities."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from clinpgx_link.exceptions import InvalidInputError


def _invalid(subtype: str = "invalid_cursor") -> InvalidInputError:
    return InvalidInputError(
        "The cursor is invalid for this request; restart from the first page.",
        field="cursor",
        subtype=subtype,
    )


def _query_hash(selectors: dict[str, Any]) -> str:
    # Callers include all data selectors, but exclude response mode and page size.
    raw = json.dumps(selectors, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class PagePosition:
    identity: str
    offset: int


class CursorCodec:
    """Authenticate continuation; process restart deliberately invalidates old cursors.

    Identity is a retained content reference or immutable snapshot ID, not a URL.
    Source retention and snapshot checks remain mandatory at the storage boundary.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time, ttl_seconds: int = 3600) -> None:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError("Cursor TTL must be between 1 and 86400 seconds.")
        self._key = secrets.token_bytes(32)
        self._clock = clock
        self._ttl = ttl_seconds

    def encode(self, selectors: dict[str, Any], *, identity: str, offset: int) -> str:
        if type(offset) is not int or not 0 <= offset <= 2**53 - 1:
            raise _invalid()
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,256}", identity):
            raise _invalid()
        payload = json.dumps(
            {
                "v": 1,
                "q": _query_hash(selectors),
                "i": identity,
                "o": offset,
                "e": self._clock() + self._ttl,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        signature = hmac.digest(self._key, payload, "sha256")
        return base64.urlsafe_b64encode(signature + payload).decode().rstrip("=")

    def decode(self, cursor: str, selectors: dict[str, Any], *, offset: int = 0) -> PagePosition:
        if (
            offset != 0
            or not isinstance(cursor, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{44,2048}", cursor)
        ):
            raise _invalid()
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            if base64.urlsafe_b64encode(raw).decode().rstrip("=") != cursor:
                raise _invalid()
            signature, payload = raw[:32], raw[32:]
            if not hmac.compare_digest(signature, hmac.digest(self._key, payload, "sha256")):
                raise _invalid()
            data = json.loads(payload)
            if data["v"] != 1 or data["q"] != _query_hash(selectors):
                raise _invalid()
            if self._clock() >= data["e"]:
                raise _invalid("cursor_expired")
            return PagePosition(identity=data["i"], offset=data["o"])
        except (ValueError, KeyError, TypeError) as exc:
            raise _invalid() from exc
