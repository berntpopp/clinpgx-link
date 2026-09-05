"""Cancellation across a real TCP socket and the pinned stateless HTTP MCP host."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import uvicorn

from clinpgx_link.config import Settings
from clinpgx_link.exceptions import NotFoundError
from clinpgx_link.mcp.admission import run_sync
from clinpgx_link.mcp.envelope import error_result
from clinpgx_link.server_manager import create_app
from tests.unit.test_repository import _repository


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline", [False, True])
async def test_real_http_disconnect_retains_worker_capacity_and_recovers(tmp_path, deadline):
    fixture, built = _repository(tmp_path)
    fixture.close()
    app = create_app(
        Settings(
            _env_file=None, cache_root=tmp_path, snapshot_path=built.database, max_active_calls=2
        )
    )
    await app.state.source_client._http.aclose()
    app.state.source_client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"allGenes": {"count": 2}})
        )
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    started, release = threading.Event(), threading.Event()

    def block(repository):
        started.set()
        release.wait(5)

    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        if deadline:
            app.state.admission.deadline_seconds = 0.05

        @app.state.mcp.tool(name="controlled_work", output_schema=None)
        async def controlled_work(source: str = "download"):
            await run_sync(block, app.state.repository)
            return error_result(NotFoundError("controlled"))

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:

            async def call(name, arguments=None):
                response = await client.post(
                    "/mcp",
                    headers={"accept": "application/json, text/event-stream"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments or {}},
                    },
                )
                response.raise_for_status()
                return response.json()["result"]["structuredContent"]

            pending = asyncio.create_task(call("controlled_work", {"source": "download"}))
            assert await asyncio.to_thread(started.wait, 2)
            if deadline:
                expired = await pending
                assert expired["subtype"] == "execution_deadline"
                assert expired["error_code"] == "upstream_unavailable"
                assert expired["_meta"]["elapsed_ms"] >= 40
                # The expired lease retains its captured deadline. Subsequent
                # ordinary reads use the production budget, not this test probe.
                app.state.admission.deadline_seconds = 60
            else:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            for _ in range(100):
                if app.state.admission.snapshot()["pending_termination"]:
                    break
                await asyncio.sleep(0.01)
            assert app.state.admission.snapshot()["pending_termination"] == 1
            overload = await call("get_server_capabilities")
            assert overload["subtype"] == "admission_capacity"
            upstream = await call("get_api_data", {"operation": "GET /report/stats"})
            assert upstream["success"] is True
            began = time.monotonic()
            health = (await client.get("/health")).json()
            assert time.monotonic() - began < 0.5
            assert health["ready"] is False
            assert health["admission"]["pending_termination"] == 1
            release.set()
            for _ in range(100):
                if app.state.admission.snapshot()["active"]["local"] == 0:
                    break
                await asyncio.sleep(0.01)
            recovered = await call("get_server_capabilities")
            assert recovered["success"] is True
            assert recovered["_meta"]["timing_scope"] == "tool_boundary"
            assert app.state.admission.snapshot()["pending_termination"] == 0
            local, upstream = await asyncio.gather(
                call("get_dataset", {"dataset_id": "data/genes.zip"}),
                call("get_api_data", {"operation": "GET /report/stats"}),
            )
            assert local["success"] is True
            assert local["_meta"]["snapshot_id"] == "sha256:" + built.manifest[
                "snapshot_id"
            ].removeprefix("sha256:")
            assert upstream["success"] is True
            assert local["_meta"]["request_id"] != upstream["_meta"]["request_id"]
    finally:
        release.set()
        server.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(serving, 5)
        sock.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime_mode, degraded_status", [("development", 200), ("production", 503)]
)
async def test_health_responds_while_cancelled_work_saturates_executor(
    tmp_path, monkeypatch, runtime_mode, degraded_status
):
    fixture, built = _repository(tmp_path)
    fixture.close()
    app = create_app(
        Settings(
            _env_file=None,
            cache_root=tmp_path,
            snapshot_path=built.database,
            max_active_calls=2,
            runtime_mode=runtime_mode,
            expected_snapshot="sha256:" + built.manifest["snapshot_id"].removeprefix("sha256:"),
        )
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    started, release = threading.Event(), threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)
    finished = threading.Event()
    # Isolate a deterministically full shared executor and restore the loop's prior
    # executor at test exit. No observer is submitted to this saturated executor.
    monkeypatch.setattr(asyncio.get_running_loop(), "_default_executor", executor)

    def block():
        started.set()
        release.wait(5)
        finished.set()

    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started

        @app.state.mcp.tool(name="controlled_work", output_schema=None)
        async def controlled_work(source: str = "download"):
            await run_sync(block)
            return error_result(NotFoundError("controlled"))

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=1) as client:
            pending = asyncio.create_task(
                client.post(
                    "/mcp",
                    headers={"accept": "application/json, text/event-stream"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "controlled_work",
                            "arguments": {"source": "download"},
                        },
                    },
                )
            )
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            for _ in range(100):
                if app.state.admission.snapshot()["pending_termination"] == 1:
                    break
                await asyncio.sleep(0.01)
            assert app.state.admission.snapshot()["pending_termination"] == 1
            assert app.state.admission.snapshot()["active"]["local"] == 1

            response = await client.get("/health", timeout=0.25)
            assert not release.is_set()
            assert not finished.is_set()
            assert response.status_code == degraded_status
            health = response.json()
            assert health["status"] == "degraded"
            assert health["ready"] is False
            assert health["admission"]["pending_termination"] == 1

            release.set()
            await asyncio.wait_for(app.state.admission.drain(), 1)
            recovered = await client.get("/health")
            assert recovered.status_code == 200
            assert recovered.json()["ready"] is True
            assert recovered.json()["admission"]["pending_termination"] == 0
    finally:
        release.set()
        if server.started:
            await asyncio.wait_for(app.state.admission.drain(), 2)
        server.should_exit = True
        await asyncio.wait_for(serving, 5)
        executor.shutdown(wait=True)
        sock.close()
