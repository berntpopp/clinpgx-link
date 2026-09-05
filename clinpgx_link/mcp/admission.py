"""Per-process reserved capacity and accounting through actual worker completion."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from typing import Any, Literal

from clinpgx_link.data.repository import DatasetRepository
from clinpgx_link.exceptions import InvalidInputError, RateLimitedError, UpstreamUnavailableError
from clinpgx_link.mcp.http_lifetime import DISCONNECTED
from clinpgx_link.mcp.search_contracts import resolve_search_route

Pool = Literal["local", "upstream"]
_CURRENT: ContextVar[Lease | None] = ContextVar("clinpgx_active_work", default=None)


def route_pool(name: str, arguments: dict[str, Any]) -> Pool:
    """Use only bounded validated selectors; uncertain routes reserve upstream."""
    if name == "get_diagnostics":
        return "upstream" if arguments.get("probe_upstream", False) else "local"
    if name in {
        "get_server_capabilities",
        "get_api_schema",
        "get_source_content",
        "list_datasets",
        "get_dataset",
        "search_dataset",
        "get_dataset_record",
    }:
        return "local"
    if name == "search_records":
        try:
            route = resolve_search_route(
                arguments["entity_type"],
                arguments.get("source", "auto"),
                arguments.get("query"),
                arguments.get("filters") or {},
            )
        except (InvalidInputError, KeyError):
            return "upstream"
        return "local" if route.selected_source == "download" else "upstream"
    return "local" if arguments.get("source") == "download" else "upstream"


def execution_expired() -> UpstreamUnavailableError:
    return UpstreamUnavailableError("Execution deadline reached.", subtype="execution_deadline")


@dataclass(eq=False)
class Lease:
    owner: Admission
    pool: Pool
    deadline: float
    workers: set[asyncio.Future[Any]] = field(default_factory=set)
    dispatch_done: bool = False
    pending_termination: bool = False

    def maybe_release(self) -> None:
        if self.dispatch_done and not self.workers:
            self.owner._leases.discard(self)
            if not self.owner._leases:
                self.owner._idle.set()

    def worker_done(self, task: asyncio.Future[Any]) -> None:
        self.workers.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve abandoned exceptions without reflecting their text.
        self.maybe_release()


class Admission:
    """Event-loop-owned leases; no queue and no borrowing between pools."""

    def __init__(self, maximum: int = 16, *, deadline_seconds: float = 60) -> None:
        if not 2 <= maximum <= 128 or not 0 < deadline_seconds <= 60:
            raise ValueError("Invalid admission limits")
        self.limits: dict[Pool, int] = {"local": (maximum + 1) // 2, "upstream": maximum // 2}
        self.deadline_seconds = deadline_seconds
        self._leases: set[Lease] = set()
        self._idle = asyncio.Event()
        self._idle.set()

    async def drain(self) -> None:
        """Wait for actual work before its source handles may be closed."""
        await self._idle.wait()

    def snapshot(self) -> dict[str, Any]:
        pending = sum(lease.pending_termination for lease in self._leases)
        return {
            "limits": dict(self.limits),
            "active": {
                pool: sum(item.pool == pool for item in self._leases) for pool in self.limits
            },
            "pending_termination": pending,
            "ready": pending == 0,
        }

    async def run[T](self, pool: Pool, operation: Callable[[], Awaitable[T]]) -> T:
        if sum(item.pool == pool for item in self._leases) >= self.limits[pool]:
            raise RateLimitedError("Active capacity exhausted.", subtype="admission_capacity")
        lease = Lease(self, pool, time.monotonic() + self.deadline_seconds)
        self._leases.add(lease)
        self._idle.clear()

        async def dispatch() -> T:
            token = _CURRENT.set(lease)
            try:
                return await operation()
            finally:
                _CURRENT.reset(token)

        task = asyncio.create_task(dispatch())

        def dispatch_done(completed: asyncio.Task[T]) -> None:
            lease.dispatch_done = True
            if not completed.cancelled():
                completed.exception()
            lease.maybe_release()

        task.add_done_callback(dispatch_done)
        disconnected = DISCONNECTED.get()
        watcher = asyncio.create_task(disconnected.wait()) if disconnected is not None else None
        try:
            waiting = {task, watcher} if watcher is not None else {task}
            done, _ = await asyncio.wait(
                waiting, timeout=self.deadline_seconds, return_when=asyncio.FIRST_COMPLETED
            )
            if watcher is not None and watcher in done:
                raise asyncio.CancelledError
            if not done or time.monotonic() >= lease.deadline:
                lease.pending_termination = True
                task.cancel()
                raise execution_expired()
            return task.result()
        except asyncio.CancelledError:
            lease.pending_termination = True
            task.cancel()
            raise
        finally:
            if watcher is not None:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher


async def run_sync[T](function: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Keep a worker tracked after its awaiter is cancelled; budget on that thread."""
    lease = _CURRENT.get()
    if lease is None:
        return await asyncio.to_thread(function, *args, **kwargs)
    if time.monotonic() >= lease.deadline:
        raise execution_expired()
    candidates = (getattr(function, "__self__", None), *args, *kwargs.values())
    repository = next((item for item in candidates if isinstance(item, DatasetRepository)), None)

    def work() -> T:
        if time.monotonic() >= lease.deadline:
            raise execution_expired()
        if repository is not None:
            with repository.execution_budget(deadline=lease.deadline):
                return function(*args, **kwargs)
        return function(*args, **kwargs)

    task = asyncio.get_running_loop().run_in_executor(None, copy_context().run, work)
    lease.workers.add(task)
    task.add_done_callback(lease.worker_done)
    return await asyncio.shield(task)
