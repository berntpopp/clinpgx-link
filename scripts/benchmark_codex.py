#!/usr/bin/env python3
"""Run one isolated, bounded Terra/high benchmark against a loopback ClinPGx MCP."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import pwd
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO

if not __package__:  # Support ``python scripts/benchmark_codex.py``.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmark_codex_isolation import (
    MODEL,
    MODEL_PROVIDER,
    REASONING_EFFORT,
    RunInputError,
    build_sandbox_command,
    create_private_run_directory,
    monitor_processes,
    open_prompt,
    positive_float,
    positive_int,
    resolve_toolchain,
    validate_home_environment,
    validate_loopback_url,
    validate_preflight,
    verify_descendants,
)
from scripts.benchmark_codex_protocol import (
    ProtocolTrace,
    assess_acceptance,
)

CHUNK_BYTES = 64 * 1024
TERMINATION_GRACE_SECONDS = 0.5
PRIVATE_PROMPT_LIMIT_BYTES = 1024 * 1024
SANDBOX_CWD = Path("/tmp/clinpgx-codex-benchmark")  # noqa: S108 - namespace tmpfs


class SessionError(RuntimeError):
    """A sanitized app-server transport or hard-limit failure."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _private_file(path: Path) -> BinaryIO:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    return os.fdopen(descriptor, "wb", buffering=0)


def _signal_group(process: subprocess.Popen[bytes], chosen: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, chosen)


def _terminate(process: subprocess.Popen[bytes]) -> None:
    _signal_group(process, signal.SIGTERM)
    if process.poll() is None:
        try:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            process.wait(timeout=2)
    _signal_group(process, signal.SIGKILL)


class AppServerSession:
    """Bounded JSONL request/notification channel with private turn capture."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        trace_output: BinaryIO,
        max_trace_bytes: int,
        deadline: float,
        process_violation: threading.Event | None = None,
    ) -> None:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise SessionError("app_server_pipes_unavailable")
        self.process = process
        self.trace_output = trace_output
        self.max_trace_bytes = max_trace_bytes
        self.deadline = deadline
        self.process_violation = process_violation
        self.started = time.monotonic()
        self.stderr_bytes = 0
        self.stderr_chunks = 0
        self.notification_methods: list[str] = []
        self.trace_bytes = 0
        self.trace_records = 0
        self.trace_complete = True
        self._capture = False
        self._trace: ProtocolTrace | None = None
        self._pending = bytearray()
        self._ready: list[dict[str, Any]] = []
        self._closed = False
        self._selector = selectors.DefaultSelector()
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
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
        raw = _canonical(wrapper)
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
        try:
            self.process.stdin.write(_canonical(message))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise SessionError("app_server_stdin_closed") from exc

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
            raise SessionError("unexpected_descendant")
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
            self.notification_methods.append(method)
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
                    self.trace_complete = False
                    raise SessionError("truncated_jsonl")
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
                    self.trace_complete = False
                    raise SessionError("truncated_jsonl")
                continue
            if key.fd == self.process.stderr.fileno():
                self.stderr_bytes += len(raw)
                self.stderr_chunks += 1
                if self.stderr_bytes > self.max_trace_bytes:
                    raise SessionError("stderr_byte_limit")
                continue
            self._pending.extend(raw)
            if len(self._pending) > self.max_trace_bytes:
                self.trace_complete = False
                raise SessionError("trace_byte_limit")
            while True:
                newline = self._pending.find(b"\n")
                if newline < 0:
                    break
                line = bytes(self._pending[:newline])
                del self._pending[: newline + 1]
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
                    raise SessionError("invalid_jsonl") from exc
                if not isinstance(value, dict):
                    raise SessionError("invalid_jsonl")
                self._ready.append(value)
        return True

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

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.process.stdin is not None:
            with contextlib.suppress(BrokenPipeError, OSError):
                self.process.stdin.close()
        _terminate(self.process)
        self._selector.close()
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision(root: Path) -> str:
    git = shutil.which("git")
    if git is None:
        raise RunInputError("git is unavailable")
    try:
        value = subprocess.run(  # noqa: S603 - fixed, read-only git invocation
            [git, "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except subprocess.SubprocessError as exc:
        raise RunInputError("cannot resolve git revision") from exc
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise RunInputError("git returned an invalid revision")
    return value


def _endpoint_ready(url: str) -> bool:
    request = urllib.request.Request(url, method="GET")  # noqa: S310 - loopback validated
    try:
        with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310
            status = int(response.status)
    except urllib.error.HTTPError as error:
        status = int(error.code)
    except (OSError, urllib.error.URLError):
        return False
    return status == 405


def _write_summary(path: Path, summary: dict[str, Any]) -> None:
    with _private_file(path) as output:
        output.write(_canonical(summary))


def _preflight(session: AppServerSession, home: Path) -> tuple[dict[str, Any], str]:
    initialize = session.request(
        "initialize",
        1,
        {
            "clientInfo": {"name": "clinpgx-benchmark-codex", "version": "1"},
            "capabilities": {"experimentalApi": False},
        },
    )
    session.send("initialized", None, {})
    config = session.request("config/read", 2, {"cwd": str(SANDBOX_CWD), "includeLayers": True})
    hooks = session.request("hooks/list", 3, {"cwds": [str(SANDBOX_CWD)]})
    skills = session.request("skills/list", 4, {"cwds": [str(SANDBOX_CWD)], "forceReload": True})
    plugins = session.request(
        "plugin/list",
        5,
        {
            "cwds": [str(SANDBOX_CWD)],
            "forceRefetch": False,
            "marketplaceKinds": ["local"],
        },
    )
    thread = session.request(
        "thread/start",
        6,
        {
            "model": MODEL,
            "modelProvider": MODEL_PROVIDER,
            "cwd": str(SANDBOX_CWD),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "ephemeral": True,
            "serviceName": "clinpgx-benchmark-codex",
            "config": {"model_reasoning_effort": REASONING_EFFORT},
        },
    )
    thread_value = thread.get("thread")
    thread_id = thread_value.get("id") if isinstance(thread_value, dict) else None
    if not isinstance(thread_id, str) or not thread_id:
        raise SessionError("thread_start_missing_id")
    mcp = session.request(
        "mcpServerStatus/list",
        7,
        {"threadId": thread_id, "detail": "toolsAndAuthOnly", "limit": 100},
    )
    evidence = validate_preflight(
        {
            "initialize": initialize,
            "config": config,
            "hooks": hooks,
            "skills": skills,
            "plugins": plugins,
            "thread": thread,
            "mcp": mcp,
        },
        expected_codex_home=home / ".codex",
    )
    if evidence["failures"]:
        raise SessionError(str(evidence["failures"][0]))
    return evidence, thread_id


def run(args: argparse.Namespace) -> bool:
    project_root = Path(__file__).resolve().parents[1]
    mcp_url = validate_loopback_url(args.mcp_url)
    if sys.platform != "linux" or not Path("/proc/self/stat").is_file():
        raise RunInputError("the Terra runner requires Linux procfs and bubblewrap")
    account_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    home = validate_home_environment(
        os.environ.get("HOME"), account_home, os.environ.get("CODEX_HOME")
    )
    auth = home / ".codex" / "auth.json"
    try:
        auth_stat = auth.lstat()
    except OSError as exc:
        raise RunInputError("Codex authentication cache is unavailable") from exc
    if stat.S_ISLNK(auth_stat.st_mode) or not stat.S_ISREG(auth_stat.st_mode):
        raise RunInputError("Codex authentication cache must be a regular non-symlink file")
    prompt_handle, prompt_metadata = open_prompt(
        args.prompt_file, max_bytes=PRIVATE_PROMPT_LIMIT_BYTES
    )
    try:
        prompt_raw = prompt_handle.read()
    finally:
        prompt_handle.close()
    try:
        prompt = prompt_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunInputError("prompt must be valid UTF-8") from exc
    if not prompt.strip() or "\x00" in prompt:
        raise RunInputError("prompt must contain nonempty text without NUL bytes")
    revision = _git_revision(project_root)
    bwrap, codex, allowed_executables = resolve_toolchain()
    output = create_private_run_directory(args.output_dir)
    trace_path = output / "trace.jsonl"
    summary_path = output / "summary.json"
    started_wall = dt.datetime.now(dt.UTC)
    started = time.monotonic()
    termination = "preflight_rejected"
    preflight: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    session: AppServerSession | None = None
    process: subprocess.Popen[bytes] | None = None
    trace: ProtocolTrace | None = None
    failure = "preflight_not_started"
    observed_processes: dict[tuple[int, str], dict[str, Any]] = {}
    monitor_stop = threading.Event()
    process_violation = threading.Event()
    monitor_thread: threading.Thread | None = None
    with _private_file(trace_path) as trace_output:
        try:
            if not _endpoint_ready(mcp_url):
                raise SessionError("mcp_endpoint_not_ready")
            command = build_sandbox_command(
                bwrap=bwrap,
                codex=codex,
                home=home,
                auth=auth,
                cwd=SANDBOX_CWD,
                mcp_url=mcp_url,
            )
            try:
                process = subprocess.Popen(  # noqa: S603 - resolved executable, fixed argv
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=project_root,
                    start_new_session=True,
                    env={},
                )
            except OSError as exc:
                raise SessionError("app_server_launch_failed") from exc
            monitor_thread = threading.Thread(
                target=monitor_processes,
                args=(
                    process.pid,
                    allowed_executables,
                    monitor_stop,
                    process_violation,
                    observed_processes,
                ),
                daemon=True,
            )
            monitor_thread.start()
            session = AppServerSession(
                process,
                trace_output=trace_output,
                max_trace_bytes=args.max_trace_bytes,
                deadline=started + args.deadline_seconds,
                process_violation=process_violation,
            )
            preflight, thread_id = _preflight(session, home)
            if process_violation.is_set():
                raise SessionError("unexpected_descendant")
            schemas = {name: value["schema"] for name, value in preflight["tool_schemas"].items()}
            trace = ProtocolTrace(
                thread_id=thread_id,
                advertised_tools=schemas,
                requested_model=MODEL,
                requested_provider=MODEL_PROVIDER,
                requested_effort=REASONING_EFFORT,
                observed_identity=preflight["observed_client_identity"],
                max_calls=args.max_tool_calls,
            )
            session.begin_turn(trace)
            turn_result = session.request(
                "turn/start",
                8,
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "model": MODEL,
                    "effort": REASONING_EFFORT,
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly"},
                },
            )
            turn_value = turn_result.get("turn")
            turn_id = turn_value.get("id") if isinstance(turn_value, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise SessionError("turn_start_missing_id")
            session.bind_turn_id(turn_id)
            session.wait_for_terminal()
            if process_violation.is_set():
                raise SessionError("unexpected_descendant")
            result = assess_acceptance(
                trace,
                termination="completed",
                trace_complete=session.trace_complete,
                exit_code=process.poll(),
            )
            termination = "completed"
            failure = result["failures"][0] if result["failures"] else ""
        except SessionError as exc:
            termination = exc.reason
            failure = exc.reason
            if trace is not None:
                result = assess_acceptance(
                    trace,
                    termination=termination,
                    trace_complete=session.trace_complete if session is not None else False,
                    exit_code=process.poll() if process is not None else None,
                )
        finally:
            if session is not None:
                session.close()
            elif process is not None:
                _terminate(process)
            monitor_stop.set()
            if monitor_thread is not None:
                monitor_thread.join(timeout=1)
            trace_output.flush()
            os.fsync(trace_output.fileno())

    process_evidence = verify_descendants(list(observed_processes.values()), allowed_executables)
    accepted = bool(result is not None and result["accepted"] and not process_evidence["failures"])
    if process_evidence["failures"] and not failure:
        failure = "unexpected_descendant"
    summary = {
        "schema": "clinpgx-codex-benchmark-v1",
        "accepted": accepted,
        "acceptance_scope": "transport_and_trace_integrity_only",
        "factual_or_score_acceptance": {
            "value": None,
            "reason": "requires_separate_answer_checks_and_blind_judging",
        },
        "failure": failure or None,
        "termination_reason": termination,
        "started_at": started_wall.isoformat(),
        "client_run_duration_ms": int((time.monotonic() - started) * 1000),
        "revision": revision,
        "requested": {
            "model": MODEL,
            "model_provider": MODEL_PROVIDER,
            "reasoning_effort": REASONING_EFFORT,
            "mcp_url": mcp_url,
            "max_tool_calls": args.max_tool_calls,
            "deadline_seconds": args.deadline_seconds,
            "max_trace_bytes": args.max_trace_bytes,
        },
        "observed_client_identity": (
            preflight.get("observed_client_identity") if preflight is not None else None
        ),
        "backend_identity": {"value": None, "reason": "not_exposed_by_app_server"},
        "prompt": prompt_metadata,
        "trace": {
            "path": trace_path.name,
            "bytes": trace_path.stat().st_size,
            "sha256": _sha256_file(trace_path),
            "complete": session.trace_complete if session is not None else True,
            "records": session.trace_records if session is not None else 0,
        },
        "preflight": preflight,
        "turn": result,
        "processes": process_evidence,
        "stderr": {
            "bytes_discarded": session.stderr_bytes if session is not None else 0,
            "chunks_discarded": session.stderr_chunks if session is not None else 0,
            "content_captured": False,
        },
        "notification_methods": (
            sorted(set(session.notification_methods)) if session is not None else []
        ),
        "limitations": [
            "Observed identity is effective Codex client session identity, not backend attestation.",
            "Transport acceptance is not factual correctness or benchmark score acceptance.",
            "Cross-model aggregation, blind judging, and campaign acceptance are separate stages.",
        ],
    }
    _write_summary(summary_path, summary)
    return accepted


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--deadline-seconds", type=positive_float, default=600.0)
    parser.add_argument("--max-tool-calls", type=positive_int, default=40)
    parser.add_argument("--max-trace-bytes", type=positive_int, default=32 * 1024 * 1024)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        accepted = run(args)
    except RunInputError:
        sys.stderr.write("benchmark-codex: input or isolation validation failed\n")
        return 2
    sys.stderr.write(f"benchmark-codex: {'accepted' if accepted else 'invalid'}\n")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
