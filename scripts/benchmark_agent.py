#!/usr/bin/env python3
"""Run one bounded Claude Code benchmark against an existing loopback MCP server."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit

CHUNK_BYTES = 64 * 1024
TERMINATION_GRACE_SECONDS = 0.25
MCP_TOOL_PREFIX = "mcp__clinpgx__"


class RunInputError(ValueError):
    """A safe, pre-launch validation failure."""


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return value


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _loopback_url(raw: str) -> str:
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a canonical loopback HTTP URL") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or parsed.path == "/"
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError("must be a canonical loopback HTTP URL")
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    canonical = f"http://{host}:{port}{parsed.path}"
    if raw != canonical:
        raise argparse.ArgumentTypeError("must be a canonical loopback HTTP URL")
    return canonical


def _private_file(path: Path) -> BinaryIO:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    return os.fdopen(descriptor, "wb", buffering=0)


def _prompt_descriptor(path: Path) -> tuple[BinaryIO, str, int]:
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    handle = os.fdopen(descriptor, "rb", buffering=0)
    details = os.fstat(descriptor)
    if not stat.S_ISREG(details.st_mode):
        handle.close()
        raise RunInputError("prompt must be a regular file")
    digest = hashlib.sha256()
    size = 0
    while chunk := handle.read(CHUNK_BYTES):
        digest.update(chunk)
        size += len(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return handle, digest.hexdigest(), size


def _git_sha(project_root: Path) -> str:
    git = shutil.which("git")
    if git is None:
        raise RunInputError("git is unavailable")
    try:
        value = subprocess.run(  # noqa: S603 - fixed read-only git invocation
            [git, "-C", str(project_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except subprocess.SubprocessError as exc:
        raise RunInputError("cannot resolve the benchmark git revision") from exc
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise RunInputError("git returned an invalid revision")
    return value


def _model_matches(requested: str, resolved: object) -> bool:
    if not isinstance(resolved, str) or not resolved:
        return False
    if requested in {"opus", "sonnet", "haiku"}:
        return re.search(rf"(?:^|-){re.escape(requested)}(?:-|$)", resolved) is not None
    return resolved == requested


class EventState:
    """Incrementally retain only bounded summary data from complete JSONL events."""

    def __init__(self) -> None:
        self.pending = bytearray()
        self.parse_errors = 0
        self.lifecycle_errors = 0
        self.init: dict[str, Any] | None = None
        self.result: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] = []
        self._call_indexes: dict[str, int] = {}
        self.errors: list[dict[str, str]] = []
        self.assistant_models: list[str | None] = []

    def feed(self, raw: bytes) -> None:
        self.pending.extend(raw)
        while True:
            newline = self.pending.find(b"\n")
            if newline < 0:
                return
            line = bytes(self.pending[:newline])
            del self.pending[: newline + 1]
            if line:
                self._event(line)

    def finish(self) -> None:
        if self.pending:
            self._event(bytes(self.pending))
            self.pending.clear()

    def _event(self, line: bytes) -> None:
        try:
            value = json.loads(line)
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            self.parse_errors += 1
            return
        if not isinstance(value, dict):
            self.parse_errors += 1
            return
        if self.result is not None:
            self.lifecycle_errors += 1
        message = value.get("message")
        if message is not None and self.init is None:
            self.lifecycle_errors += 1
        if value.get("type") == "system" and value.get("subtype") == "init":
            if self.init is None:
                self.init = value
            else:
                self.parse_errors += 1
        if value.get("type") == "result":
            if self.init is None:
                self.lifecycle_errors += 1
            if self.result is None:
                self.result = value
            else:
                self.parse_errors += 1
        if value.get("type") == "assistant":
            if not isinstance(message, dict):
                self.parse_errors += 1
            else:
                model = message.get("model")
                self.assistant_models.append(model if isinstance(model, str) else None)
        if not isinstance(message, dict):
            return
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                self._tool_use(block)
            elif block.get("type") == "tool_result":
                self._tool_result(block)

    def _tool_use(self, block: dict[str, Any]) -> None:
        call_id = block.get("id")
        name = block.get("name")
        if (
            not isinstance(call_id, str)
            or not isinstance(name, str)
            or call_id in self._call_indexes
        ):
            self.parse_errors += 1
            return
        self._call_indexes[call_id] = len(self.calls)
        self.calls.append(
            {"id": call_id, "name": name, "is_error": False, "result_received": False}
        )

    def _tool_result(self, block: dict[str, Any]) -> None:
        call_id = block.get("tool_use_id")
        if not isinstance(call_id, str):
            self.parse_errors += 1
            return
        index = self._call_indexes.get(call_id)
        if index is None or self.calls[index]["result_received"] is True:
            self.parse_errors += 1
            return
        is_error = block.get("is_error") is True
        self.calls[index]["result_received"] = True
        self.calls[index]["is_error"] = is_error
        if is_error:
            self.errors.append({"kind": "tool_result", "tool_use_id": call_id})


def _signal_group(process: subprocess.Popen[bytes], chosen: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, chosen)


def _reap(process: subprocess.Popen[bytes], *, force_group: bool = False) -> None:
    """Bound cleanup even when capture or artifact I/O raises."""
    if force_group:
        _signal_group(process, signal.SIGTERM)
    if process.poll() is None:
        try:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            process.wait(timeout=1)
    if force_group:
        _signal_group(process, signal.SIGKILL)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _capture(
    process: subprocess.Popen[bytes],
    stdout_file: BinaryIO,
    stderr_file: BinaryIO,
    deadline: float,
    max_calls: int,
    max_bytes: int,
) -> tuple[EventState, str, bool, hashlib._Hash, int, hashlib._Hash, int]:
    state = EventState()
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    streams = {
        process.stdout.fileno(): ("stdout", stdout_file),
        process.stderr.fileno(): ("stderr", stderr_file),
    }
    for descriptor in streams:
        os.set_blocking(descriptor, False)
        selector.register(descriptor, selectors.EVENT_READ)
    stdout_digest = hashlib.sha256()
    stderr_digest = hashlib.sha256()
    stdout_size = 0
    stderr_size = 0
    termination = "completed"
    truncated = False
    terminate_at: float | None = None

    try:
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if termination == "completed" and now >= deadline:
                termination = "deadline"
                terminate_at = now
                _signal_group(process, signal.SIGTERM)
            elif terminate_at is not None and now - terminate_at >= TERMINATION_GRACE_SECONDS:
                _signal_group(process, signal.SIGKILL)
                terminate_at = None
            timeout = max(0.0, min(0.05, deadline - now)) if termination == "completed" else 0.05
            for key, _mask in selector.select(timeout):
                descriptor = key.fd
                try:
                    raw = os.read(descriptor, CHUNK_BYTES)
                except BlockingIOError:
                    continue
                if not raw:
                    selector.unregister(descriptor)
                    continue
                stream_name, destination = streams[descriptor]
                remaining = max_bytes - stdout_size - stderr_size
                retained = raw[: max(0, remaining)]
                if retained:
                    destination.write(retained)
                    if stream_name == "stdout":
                        stdout_digest.update(retained)
                        stdout_size += len(retained)
                        state.feed(retained)
                    else:
                        stderr_digest.update(retained)
                        stderr_size += len(retained)
                if len(retained) != len(raw) and termination == "completed":
                    termination = "trace_byte_limit"
                    truncated = True
                    terminate_at = time.monotonic()
                    _signal_group(process, signal.SIGTERM)
                if termination == "completed" and len(state.calls) > max_calls:
                    termination = "tool_call_limit"
                    terminate_at = time.monotonic()
                    _signal_group(process, signal.SIGTERM)
        state.finish()
    finally:
        selector.close()
    return state, termination, truncated, stdout_digest, stdout_size, stderr_digest, stderr_size


def _acceptance_failures(
    state: EventState,
    termination: str,
    truncated: bool,
    exit_code: int | None,
    requested_model: str,
) -> list[str]:
    failures: list[str] = []
    if termination != "completed":
        failures.append(termination)
    if truncated and "trace_byte_limit" not in failures:
        failures.append("trace_byte_limit")
    if exit_code != 0:
        failures.append("nonzero_exit")
    if state.parse_errors:
        failures.append("invalid_jsonl")
    if state.lifecycle_errors:
        failures.append("invalid_event_lifecycle")
    if any(not call["result_received"] for call in state.calls):
        failures.append("unmatched_tool_calls")
    if any(not call["name"].startswith(MCP_TOOL_PREFIX) for call in state.calls):
        failures.append("unexpected_tool_calls")
    if state.result is None:
        failures.append("missing_result")
    elif state.result.get("subtype") != "success" or state.result.get("is_error") is not False:
        failures.append("unsuccessful_result")
    elif not isinstance(state.result.get("result"), str) or not state.result["result"]:
        failures.append("missing_final_response")
    init = state.init
    if init is None:
        failures.append("missing_init")
        return failures
    if not _model_matches(requested_model, init.get("model")):
        failures.append("model_mismatch")
    if any(model != init.get("model") for model in state.assistant_models):
        failures.append("assistant_model_mismatch")
    tools = init.get("tools")
    if (
        not isinstance(tools, list)
        or not tools
        or not all(isinstance(tool, str) and tool.startswith(MCP_TOOL_PREFIX) for tool in tools)
    ):
        failures.append("unexpected_tools")
    elif any(call["name"] not in tools for call in state.calls) and (
        "unexpected_tool_calls" not in failures
    ):
        failures.append("unexpected_tool_calls")
    servers = init.get("mcp_servers")
    if (
        not isinstance(servers, list)
        or len(servers) != 1
        or not isinstance(servers[0], dict)
        or servers[0].get("name") != "clinpgx"
        or servers[0].get("status") != "connected"
    ):
        failures.append("unexpected_mcp_servers")
    return failures


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def run(args: argparse.Namespace) -> bool:
    project_root = Path(__file__).resolve().parents[1]
    output: Path = args.output_dir
    try:
        output.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise RunInputError("output directory already exists") from exc
    prompt: BinaryIO | None = None
    try:
        prompt, prompt_sha256, prompt_size = _prompt_descriptor(args.prompt_file)
        git_sha = _git_sha(project_root)
        claude = shutil.which("claude")
        if claude is None:
            raise RunInputError("claude executable is unavailable")
        started_wall = dt.datetime.now(dt.UTC)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="clinpgx-benchmark-") as isolated_raw:
            isolated = Path(isolated_raw)
            config = isolated / "mcp.json"
            descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as config_file:
                config_file.write(
                    _canonical({"mcpServers": {"clinpgx": {"type": "http", "url": args.mcp_url}}})
                )
            settings = isolated / "settings.json"
            descriptor = os.open(settings, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as settings_file:
                settings_file.write(_canonical({"disableAllHooks": True, "enabledPlugins": {}}))
            command = [
                claude,
                "--print",
                "--verbose",
                "--output-format",
                "stream-json",
                "--model",
                args.model,
                "--strict-mcp-config",
                "--mcp-config",
                str(config),
                "--tools",
                "",
                "--allowedTools",
                f"{MCP_TOOL_PREFIX}*",
                "--disable-slash-commands",
                "--no-session-persistence",
                "--restricted",
                "--setting-sources",
                "",
                "--settings",
                str(settings),
                "--permission-mode",
                "dontAsk",
                "--permission-prompts",
                "none",
                "--no-chrome",
                "--prompt-suggestions",
                "false",
            ]
            with (
                _private_file(output / "stdout.jsonl") as stdout_file,
                _private_file(output / "stderr.log") as stderr_file,
            ):
                process = subprocess.Popen(  # noqa: S603 - fixed executable and literal argv
                    command,
                    stdin=prompt,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=isolated,
                    start_new_session=True,
                    env=dict(os.environ),
                )
                try:
                    captured = _capture(
                        process,
                        stdout_file,
                        stderr_file,
                        started + args.deadline_seconds,
                        args.max_tool_calls,
                        args.max_trace_bytes,
                    )
                    exit_code = process.wait(timeout=1)
                    stdout_file.flush()
                    stderr_file.flush()
                    os.fsync(stdout_file.fileno())
                    os.fsync(stderr_file.fileno())
                finally:
                    _reap(process, force_group=True)
        duration = time.monotonic() - started
        state, termination, truncated, trace_hash, trace_size, stderr_hash, stderr_size = captured
        failures = _acceptance_failures(state, termination, truncated, exit_code, args.model)
        init = state.init or {}
        result = state.result or {}
        raw_usage = result.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        raw_model_usage = result.get("modelUsage")
        model_usage = raw_model_usage if isinstance(raw_model_usage, dict) else {}
        summary = {
            "schema_version": 1,
            "accepted": not failures,
            "acceptance_scope": "transport_and_trace_integrity_only",
            "acceptance_failures": failures,
            "started_at": started_wall.isoformat().replace("+00:00", "Z"),
            "duration_seconds": duration,
            "deadline_seconds": args.deadline_seconds,
            "max_tool_calls": args.max_tool_calls,
            "max_trace_bytes": args.max_trace_bytes,
            "mcp_url": args.mcp_url,
            "requested_model": args.model,
            "resolved_model": init.get("model"),
            "init_tools": init.get("tools"),
            "init_mcp_servers": init.get("mcp_servers"),
            "assistant_models": state.assistant_models,
            "git_sha": git_sha,
            "prompt_sha256": prompt_sha256,
            "prompt_size_bytes": prompt_size,
            "termination_reason": termination,
            "exit_code": exit_code,
            "result_received": state.result is not None,
            "trace_truncated": truncated,
            "trace_sha256": trace_hash.hexdigest(),
            "trace_size_bytes": trace_size,
            "stderr_sha256": stderr_hash.hexdigest(),
            "stderr_size_bytes": stderr_size,
            "calls": state.calls,
            "errors": state.errors,
            "usage": {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            },
            "total_cost_usd": result.get("total_cost_usd"),
            "result_duration_ms": result.get("duration_ms"),
            "result_duration_api_ms": result.get("duration_api_ms"),
            "model_usage": model_usage,
            "final_response": result.get("result"),
        }
        with _private_file(output / "summary.json") as summary_file:
            summary_file.write(_canonical(summary))
            summary_file.flush()
            os.fsync(summary_file.fileno())
        directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        sys.stdout.write(
            f"benchmark-agent: {'accepted' if not failures else 'invalid'}; "
            f"summary={output / 'summary.json'}\n"
        )
        return not failures
    finally:
        if prompt is not None:
            prompt.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-url", required=True, type=_loopback_url)
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", default="opus")
    parser.add_argument("--deadline-seconds", default=600.0, type=_positive_float)
    parser.add_argument("--max-tool-calls", default=45, type=_positive_int)
    parser.add_argument("--max-trace-bytes", default=32 * 1024 * 1024, type=_positive_int)
    args = parser.parse_args()
    try:
        return 0 if run(args) else 1
    except (OSError, RunInputError, subprocess.SubprocessError):
        sys.stderr.write("benchmark-agent: launch or artifact validation failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
