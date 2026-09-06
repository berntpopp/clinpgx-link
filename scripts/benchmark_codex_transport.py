"""Bounded JSONL transport for the isolated Codex app-server runner."""

from __future__ import annotations

import contextlib
import json
import os
import re
import selectors
import signal
import subprocess
import threading
import time
from typing import Any, BinaryIO

from scripts.benchmark_codex_protocol import ProtocolTrace

CHUNK_BYTES = 64 * 1024
TERMINATION_GRACE_SECONDS = 0.5
DEFAULT_PREFLIGHT_EVENTS = 512
MAX_RETAINED_METHODS = 128
_SAFE_METHOD = re.compile(r"[A-Za-z0-9_./-]{1,128}\Z")


class SessionError(RuntimeError):
    """A sanitized app-server transport or hard-limit failure."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def canonical_jsonl(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _signal_group(process: subprocess.Popen[bytes], chosen: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, chosen)


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    """Bounded teardown of the owned app-server process group."""
    _signal_group(process, signal.SIGTERM)
    if process.poll() is None:
        try:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            process.wait(timeout=2)
    _signal_group(process, signal.SIGKILL)


class AppServerSession:
    """Deadline-aware JSONL channel with bounded preflight and turn capture."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        trace_output: BinaryIO,
        max_trace_bytes: int,
        deadline: float,
        process_violation: threading.Event | None = None,
        process_failure_state: dict[str, str] | None = None,
        max_preflight_bytes: int | None = None,
        max_preflight_events: int = DEFAULT_PREFLIGHT_EVENTS,
    ) -> None:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise SessionError("app_server_pipes_unavailable")
        self.process = process
        self.trace_output = trace_output
        self.max_trace_bytes = max_trace_bytes
        self.max_preflight_bytes = max_preflight_bytes or max_trace_bytes
        self.max_preflight_events = max_preflight_events
        self.deadline = deadline
        self.process_violation = process_violation
        self.process_failure_state = process_failure_state
        self.started = time.monotonic()
        self.stderr_bytes = 0
        self.stderr_chunks = 0
        self.notification_methods: set[str] = set()
        self.preflight_bytes = 0
        self.preflight_events = 0
        self.trace_bytes = 0
        self.trace_records = 0
        self.trace_complete = True
        self._capture = False
        self._trace: ProtocolTrace | None = None
        self._pending = bytearray()
        self._ready: list[dict[str, Any]] = []
        self._closed = False
        self._selector = selectors.DefaultSelector()
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        for stream in (process.stdout, process.stderr):
            self._selector.register(stream.fileno(), selectors.EVENT_READ)

    def begin_turn(self, trace: ProtocolTrace) -> None:
        if self._capture:
            raise SessionError("second_model_turn_refused")
        self._trace = trace
        self._capture = True

    def bind_turn_id(self, turn_id: str) -> None:
        if self._trace is None:
            raise SessionError("turn_trace_unavailable")
        if self._trace.turn_id not in {None, turn_id}:
            raise SessionError("turn_id_mismatch")
        self._trace.turn_id = turn_id

    def _record(self, direction: str, message: dict[str, Any]) -> None:
        wrapper = {
            "direction": direction,
            "elapsed_ms": int((time.monotonic() - self.started) * 1000),
            "message": message,
        }
        raw = canonical_jsonl(wrapper)
        if self.trace_bytes + len(raw) > self.max_trace_bytes:
            self.trace_complete = False
            raise SessionError("trace_byte_limit")
        self.trace_output.write(raw)
        self.trace_bytes += len(raw)
        self.trace_records += 1

    def send(self, method: str, request_id: int | None, params: dict[str, Any]) -> None:
        if self._closed or self.process.stdin is None:
            raise SessionError("app_server_stdin_unavailable")
        message: dict[str, Any] = {"method": method, "params": params}
        if request_id is not None:
            message["id"] = request_id
        if self._capture:
            self._record("client_to_server", message)
        raw = canonical_jsonl(message)
        descriptor = self.process.stdin.fileno()
        offset = 0
        while offset < len(raw):
            self._check_limits()
            try:
                written = os.write(descriptor, raw[offset:])
            except BlockingIOError:
                timeout = min(0.05, max(0.0, self.deadline - time.monotonic()))
                _readable, writable, _exceptional = select_writable(descriptor, timeout)
                if not writable:
                    continue
                continue
            except (BrokenPipeError, OSError) as exc:
                raise SessionError("app_server_stdin_closed") from exc
            if written <= 0:
                raise SessionError("app_server_stdin_closed")
            offset += written

    def request(self, method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
        self.send(method, request_id, params)
        while True:
            message = self.next_message()
            if message.get("id") != request_id:
                if "id" in message and not isinstance(message.get("method"), str):
                    raise SessionError("unexpected_response_id")
                continue
            if message.get("error") is not None:
                raise SessionError(f"{method.replace('/', '_')}_protocol_error")
            result = message.get("result")
            if not isinstance(result, dict):
                raise SessionError(f"{method.replace('/', '_')}_missing_result")
            return result

    def _check_limits(self) -> None:
        if self.process_violation is not None and self.process_violation.is_set():
            reason = (
                self.process_failure_state.get("reason", "unexpected_descendant")
                if self.process_failure_state is not None
                else "unexpected_descendant"
            )
            raise SessionError(reason)
        if time.monotonic() >= self.deadline:
            raise SessionError("deadline")

    def next_message(self) -> dict[str, Any]:
        while not self._ready:
            self._pump()
        message = self._ready.pop(0)
        if self._capture:
            self._record("server_to_client", message)
        method = message.get("method")
        if isinstance(method, str):
            if self._trace is None and method == "model/rerouted":
                raise SessionError("model_rerouted")
            if self._trace is None and "id" in message:
                raise SessionError("unexpected_server_request")
        if self._trace is not None:
            before = set(self._trace.failures)
            self._trace.observe(message, int((time.monotonic() - self.started) * 1000))
            new_failures = [reason for reason in self._trace.failures if reason not in before]
            if new_failures:
                raise SessionError(new_failures[0])
        return message

    def _account_preflight(self, raw_bytes: int) -> None:
        self.preflight_bytes += raw_bytes
        self.preflight_events += 1
        if self.preflight_bytes > self.max_preflight_bytes:
            raise SessionError("preflight_byte_limit")
        if self.preflight_events > self.max_preflight_events:
            raise SessionError("preflight_event_limit")

    def _retain_method(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if isinstance(method, str):
            retained = method if _SAFE_METHOD.fullmatch(method) else "invalid_or_oversized_method"
            if retained in self.notification_methods:
                return
            if len(self.notification_methods) < MAX_RETAINED_METHODS - 1:
                self.notification_methods.add(retained)
            else:
                self.notification_methods.add("additional_methods_omitted")

    def _pump(self, timeout_seconds: float | None = None) -> bool:
        self._check_limits()
        timeout = min(
            0.05 if timeout_seconds is None else timeout_seconds,
            max(0.0, self.deadline - time.monotonic()),
        )
        events = self._selector.select(timeout)
        if not events:
            self._check_limits()
            if self.process.poll() is not None:
                if self._pending:
                    self._reject_truncated()
                raise SessionError("app_server_exited")
            return False
        assert self.process.stdout is not None and self.process.stderr is not None
        for key, _mask in events:
            try:
                raw = os.read(key.fd, CHUNK_BYTES)
            except BlockingIOError:
                continue
            if not raw:
                self._selector.unregister(key.fd)
                if key.fd == self.process.stdout.fileno() and self._pending:
                    self._reject_truncated()
                continue
            if key.fd == self.process.stderr.fileno():
                self.stderr_bytes += len(raw)
                self.stderr_chunks += 1
                if self.stderr_bytes > self.max_trace_bytes:
                    raise SessionError("stderr_byte_limit")
                continue
            self._pending.extend(raw)
            pending_limit = self.max_trace_bytes if self._capture else self.max_preflight_bytes
            if len(self._pending) > pending_limit:
                if self._capture:
                    self.trace_complete = False
                    raise SessionError("trace_byte_limit")
                raise SessionError("preflight_byte_limit")
            while True:
                newline = self._pending.find(b"\n")
                if newline < 0:
                    break
                line = bytes(self._pending[:newline])
                del self._pending[: newline + 1]
                if not line:
                    continue
                if not self._capture:
                    self._account_preflight(len(line) + 1)
                try:
                    value = json.loads(line)
                except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
                    raise SessionError("invalid_jsonl") from exc
                if not isinstance(value, dict):
                    raise SessionError("invalid_jsonl")
                self._retain_method(value)
                self._ready.append(value)
        return True

    def _reject_truncated(self) -> None:
        self.trace_complete = False
        raise SessionError("truncated_jsonl")

    def wait_for_terminal(self) -> None:
        if self._trace is None:
            raise SessionError("turn_trace_unavailable")
        while not self._trace.turn_completed:
            self.next_message()
        while self._ready:
            self.next_message()
        drain_deadline = min(self.deadline, time.monotonic() + 0.05)
        while time.monotonic() < drain_deadline:
            if not self._pump(drain_deadline - time.monotonic()):
                break
            while self._ready:
                self.next_message()
        if self._pending:
            self._reject_truncated()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.process.stdin is not None:
            with contextlib.suppress(BrokenPipeError, OSError):
                self.process.stdin.close()
        terminate_process(self.process)
        self._selector.close()
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


def select_writable(descriptor: int, timeout: float) -> tuple[list[int], list[int], list[int]]:
    """Small seam around select so deadline behavior remains explicit and testable."""
    import select

    return select.select([], [descriptor], [], timeout)
