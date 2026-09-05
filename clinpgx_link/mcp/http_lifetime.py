"""Expose real HTTP disconnects to tool work without parsing source or arguments."""

from __future__ import annotations

import asyncio
import contextlib
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

DISCONNECTED: ContextVar[asyncio.Event | None] = ContextVar(
    "clinpgx_http_disconnected", default=None
)


async def serve_with_disconnect(app: ASGIApp, scope: Scope, receive: Receive, send: Send) -> None:
    """One bounded receive pump observes disconnect even while JSON output waits."""
    disconnected = asyncio.Event()
    token = DISCONNECTED.set(disconnected)
    messages: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)

    async def pump() -> None:
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected.set()
                await messages.put(message)
                return
            await messages.put(message)

    reader = asyncio.create_task(pump())
    try:
        await app(scope, messages.get, send)
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        DISCONNECTED.reset(token)
