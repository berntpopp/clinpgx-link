"""Serialization helper for repositories that own one SQLite connection."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, cast


def serialized_connection[Method: Callable[..., Any]](method: Method) -> Method:
    """Hold the repository's reentrant lock for one complete operation."""

    @wraps(method)
    def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        with self._connection_lock:
            return method(self, *args, **kwargs)

    return cast(Method, guarded)


__all__ = ["serialized_connection"]
