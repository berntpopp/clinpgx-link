"""Bounded admission follows actual work, including abandoned worker threads."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time

import pytest
from fastmcp import Client, FastMCP

from clinpgx_link.config import Settings
from clinpgx_link.exceptions import NotFoundError
from clinpgx_link.mcp.envelope import error_result
from clinpgx_link.mcp.middleware import BoundaryGuard


def test_active_capacity_settings_are_bounded():
    assert Settings(_env_file=None).model_dump().get("max_active_calls") == 16
    for value in (1, 129):
        with pytest.raises(ValueError):
            Settings(_env_file=None, max_active_calls=value)


def test_source_routing_and_odd_pool_reservations():
    from clinpgx_link.mcp.admission import Admission, route_pool

    admission = Admission(maximum=5)
    assert admission.snapshot()["limits"] == {"local": 3, "upstream": 2}
    for name in ("get_api_schema", "get_source_content", "list_datasets"):
        assert route_pool(name, {}) == "local"
    assert route_pool("get_diagnostics", {"probe_upstream": True}) == "upstream"
    assert route_pool("search_records", {"entity_type": "gene", "query": "CYP"}) == "local"
    assert (
        route_pool("search_records", {"entity_type": "gene", "filters": {"gene": "CYP2C19"}})
        == "upstream"
    )
    assert route_pool("get_record", {"source": "download"}) == "local"
    assert route_pool("unknown", {"source": "auto"}) == "upstream"


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline", [False, True])
async def test_worker_keeps_slot_after_cancellation_or_deadline(deadline):
    from clinpgx_link.mcp.admission import Admission, run_sync

    admission = Admission(maximum=2, deadline_seconds=0.05 if deadline else 60)
    server = FastMCP("lifetime")
    server.add_middleware(BoundaryGuard(server, admission=admission))
    started, release = threading.Event(), threading.Event()

    def block():
        started.set()
        assert release.wait(5)

    @server.tool(output_schema=None)
    async def list_datasets():
        await run_sync(block)
        return error_result(NotFoundError("test"))

    @server.tool(output_schema=None)
    async def get_api_data():
        return error_result(NotFoundError("test"))

    try:
        async with Client(server) as client:
            task = asyncio.create_task(client.call_tool("list_datasets", {}, raise_on_error=False))
            assert await asyncio.to_thread(started.wait, 2)
            if deadline:
                result = await task
                assert result.structured_content["subtype"] == "execution_deadline"
                assert result.structured_content["error_code"] == "upstream_unavailable"
            else:
                # MCP SDK 2 sends notifications/cancelled when the request task
                # is abandoned, without exposing its internal request counter.
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await asyncio.sleep(0.02)
            overloaded = await client.call_tool("list_datasets", {}, raise_on_error=False)
            assert overloaded.structured_content["subtype"] == "admission_capacity"
            assert overloaded.structured_content["error_code"] == "rate_limited"
            assert overloaded.structured_content["_meta"]["timing_scope"] == "tool_boundary"
            assert admission.snapshot()["pending_termination"] == 1
            draining = asyncio.create_task(admission.drain())
            await asyncio.sleep(0)
            assert not draining.done()
            upstream = await client.call_tool("get_api_data", {}, raise_on_error=False)
            assert upstream.structured_content["error_code"] == "not_found"
            release.set()
            for _ in range(100):
                if admission.snapshot()["active"]["local"] == 0:
                    break
                await asyncio.sleep(0.01)
            assert admission.snapshot()["active"] == {"local": 0, "upstream": 0}
            assert admission.snapshot()["pending_termination"] == 0
            await asyncio.wait_for(draining, 1)
    finally:
        release.set()


def test_repository_lock_wait_expires_before_queued_work_runs(tmp_path):
    from tests.unit.test_repository import _repository

    repository, _ = _repository(tmp_path)
    locked, release = threading.Event(), threading.Event()

    def hold_lock():
        with repository._connection_lock:
            locked.set()
            assert release.wait(3)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert locked.wait(1)
    try:
        began = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            with repository.execution_budget(deadline=began + 0.03):
                pytest.fail("Expired queued work must never enter")
        assert time.monotonic() - began < 0.5
    finally:
        release.set()
        holder.join()
        repository.close()


@pytest.mark.asyncio
async def test_api_retention_uses_injected_tracked_worker(tmp_path):
    import httpx

    from clinpgx_link.api.client import ClinPGxClient
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.mcp.admission import Admission, run_sync
    from clinpgx_link.mcp.facade import create_mcp
    from clinpgx_link.services.api import ApiService

    started, release = threading.Event(), threading.Event()

    class SlowStore(ContentStore):
        def put(self, *args, **kwargs):
            started.set()
            release.wait(2)
            return super().put(*args, **kwargs)

    store = SlowStore(tmp_path / "retention.sqlite")
    source = ClinPGxClient(
        Settings(_env_file=None),
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"allGenes": {"count": 2}})
            )
        ),
        store,
    )
    source.worker = run_sync
    admission = Admission(2, deadline_seconds=0.05)
    server = create_mcp(content_store=store, api_service=ApiService(source), admission=admission)
    try:
        async with Client(server) as client:
            began = time.monotonic()
            result = await client.call_tool(
                "get_api_data", {"operation": "GET /report/stats"}, raise_on_error=False
            )
            assert time.monotonic() - began < 0.5
            assert result.structured_content["subtype"] == "execution_deadline"
            assert started.is_set()
            assert admission.snapshot()["pending_termination"] == 1
            release.set()
            for _ in range(100):
                if not admission.snapshot()["pending_termination"]:
                    break
                await asyncio.sleep(0.01)
            assert admission.snapshot()["pending_termination"] == 0
    finally:
        release.set()
        await source.close()


@pytest.mark.asyncio
async def test_nested_sqlite_budget_preserves_outer_deadline_and_recovers(tmp_path):
    from clinpgx_link.mcp.admission import Admission, run_sync
    from tests.unit.test_repository import _repository

    repository, _ = _repository(tmp_path)
    admission = Admission(maximum=2, deadline_seconds=0.04)
    server = FastMCP("sqlite deadline")
    server.add_middleware(BoundaryGuard(server, admission=admission))
    nested_interrupted, terminated = threading.Event(), threading.Event()
    query = "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n"

    def work(repository):
        try:
            with pytest.raises(sqlite3.OperationalError, match="interrupted"):
                with repository._progress_hooks.budget(timeout_seconds=0.001):
                    repository._connection.execute(query).fetchone()
            nested_interrupted.set()
            repository._connection.execute(query).fetchone()
        finally:
            terminated.set()

    @server.tool(output_schema=None)
    async def list_datasets():
        try:
            await run_sync(work, repository)
        except Exception:
            # Existing tool handlers sanitize foreign exceptions; boundary must still
            # identify its own elapsed execution budget truthfully.
            return error_result(NotFoundError("sanitized"))
        pytest.fail("Outer deadline did not interrupt SQLite")

    try:
        async with Client(server) as client:
            result = await client.call_tool("list_datasets", {}, raise_on_error=False)
        assert result.structured_content["subtype"] == "execution_deadline"
        assert result.structured_content["error_code"] == "upstream_unavailable"
        assert await asyncio.to_thread(terminated.wait, 1)
        assert nested_interrupted.is_set()
        assert repository.search("data/genes.zip", member="genes.tsv", limit=1).value
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_outbound_scheduler_wait_reserves_only_upstream_and_429_has_distinct_guidance(
    tmp_path,
):
    import httpx

    from clinpgx_link.api.client import ClinPGxClient, RequestScheduler
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.mcp.admission import Admission
    from clinpgx_link.mcp.facade import create_mcp
    from clinpgx_link.services.api import ApiService

    waiting, release = asyncio.Event(), asyncio.Event()

    async def wait(delay):
        waiting.set()
        await release.wait()

    store = ContentStore(tmp_path / "content.sqlite")
    scheduler = RequestScheduler(2.0, sleep=wait)
    scheduler._next = time.monotonic() + 10
    source = ClinPGxClient(
        Settings(_env_file=None),
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(429))),
        store,
        scheduler=scheduler,
    )
    server = create_mcp(content_store=store, api_service=ApiService(source), admission=Admission(2))
    try:
        async with Client(server) as client:
            task = asyncio.create_task(
                client.call_tool(
                    "get_api_data", {"operation": "GET /report/stats"}, raise_on_error=False
                )
            )
            await asyncio.wait_for(waiting.wait(), 1)
            local = await client.call_tool("get_server_capabilities", {})
            assert local.structured_content["success"] is True
            excess = await client.call_tool(
                "get_api_data", {"operation": "GET /report/stats"}, raise_on_error=False
            )
            assert excess.structured_content["subtype"] == "admission_capacity"
            assert excess.structured_content["retry_after_seconds"] == 1
            release.set()
            throttled = await task
            assert throttled.structured_content["subtype"] == "upstream_throttle"
            assert throttled.structured_content["retry_after_seconds"] == 30
    finally:
        release.set()
        await source.close()


@pytest.mark.asyncio
async def test_record_profile_repository_work_runs_on_budgeted_worker(tmp_path, monkeypatch):
    from clinpgx_link.content.store import ContentStore
    from clinpgx_link.mcp.facade import create_mcp
    from tests.unit.test_repository import _repository

    repository, _ = _repository(tmp_path)
    record_id = repository.search("data/genes.zip", member="genes.tsv", limit=1).value[0][
        "record_id"
    ]
    store = ContentStore(tmp_path / "content.sqlite")
    observed = []
    original = repository.record_profile

    def profile(*args, **kwargs):
        observed.append((threading.get_ident(), bool(repository._progress_hooks._budgets)))
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "record_profile", profile)
    try:
        async with Client(create_mcp(content_store=store, repository=repository)) as client:
            result = await client.call_tool("get_dataset_record", {"record_id": record_id})
        assert result.structured_content["success"] is True
        assert observed
        assert all(worker != threading.get_ident() and budget for worker, budget in observed)
    finally:
        store.close()
        repository.close()
