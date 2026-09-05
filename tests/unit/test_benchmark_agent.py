"""Exercise the bounded real-agent benchmark harness with a local Claude stub."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import textwrap
import time
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from scripts import benchmark_agent

SCRIPT = Path(__file__).parents[2] / "scripts/benchmark_agent.py"


def _stub(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "bin" / "claude"
    executable.parent.mkdir()
    executable.write_text(
        "#!/usr/bin/env python3\nimport json, os, pathlib, sys, time\n" + textwrap.dedent(body)
    )
    executable.chmod(0o700)
    return executable


def _run(
    tmp_path: Path,
    stub_body: str,
    *,
    output_name: str = "run",
    deadline: str = "3",
    calls: str = "4",
    trace_bytes: str = "65536",
    url: str = "http://127.0.0.1:18765/mcp",
) -> tuple[subprocess.CompletedProcess[str], Path, bytes]:
    _stub(tmp_path, stub_body)
    prompt = b"Return source evidence only.\n"
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_bytes(prompt)
    output = tmp_path / output_name
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{env['PATH']}"
    result = subprocess.run(  # noqa: S603 - fixed project script with a temporary stub PATH
        [
            sys.executable,
            str(SCRIPT),
            "--mcp-url",
            url,
            "--prompt-file",
            str(prompt_path),
            "--output-dir",
            str(output),
            "--deadline-seconds",
            deadline,
            "--max-tool-calls",
            calls,
            "--max-trace-bytes",
            trace_bytes,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=10,
    )
    return result, output, prompt


def _summary(output: Path) -> dict[str, Any]:
    value = json.loads((output / "summary.json").read_bytes())
    assert isinstance(value, dict)
    return value


SUCCESS_STUB = """
args = sys.argv[1:]
required = {
    "--print", "--verbose", "--strict-mcp-config", "--disable-slash-commands",
    "--no-session-persistence", "--restricted", "--no-chrome",
}
assert required <= set(args), args
assert "--safe-mode" not in args
def option(name):
    return args[args.index(name) + 1]
assert option("--model") == "opus"
assert option("--output-format") == "stream-json"
assert option("--tools") == ""
assert option("--allowedTools") == "mcp__clinpgx__*"
assert option("--setting-sources") == ""
assert option("--permission-mode") == "dontAsk"
assert option("--permission-prompts") == "none"
assert option("--prompt-suggestions") == "false"
config = json.loads(pathlib.Path(option("--mcp-config")).read_text())
assert config == {"mcpServers": {"clinpgx": {
    "type": "http", "url": "http://127.0.0.1:18765/mcp"
}}}
settings = json.loads(pathlib.Path(option("--settings")).read_text())
assert settings == {"disableAllHooks": True, "enabledPlugins": {}}
assert pathlib.Path.cwd() == pathlib.Path(option("--mcp-config")).parent
assert sys.stdin.read() == "Return source evidence only.\\n"
events = [
    {"type": "system", "subtype": "init", "model": "claude-opus-5",
     "tools": ["mcp__clinpgx__search_records", "mcp__clinpgx__get_record"],
     "mcp_servers": [{"name": "clinpgx", "status": "connected"}]},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "call-1", "name": "mcp__clinpgx__search_records",
         "input": {"query": "CYP2C19"}}
    ]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "call-1", "is_error": False,
         "content": "source result"}
    ]}},
    {"type": "result", "subtype": "success", "is_error": False,
     "result": "The source reports CYP2C19.", "duration_ms": 1250,
     "duration_api_ms": 1100, "total_cost_usd": 0.125,
     "usage": {"input_tokens": 11, "output_tokens": 17,
               "cache_creation_input_tokens": 23, "cache_read_input_tokens": 29},
     "modelUsage": {"claude-opus-5": {"inputTokens": 11, "outputTokens": 17,
                                       "cacheReadInputTokens": 29,
                                       "cacheCreationInputTokens": 23,
                                       "costUSD": 0.125}}},
]
for event in events:
    print(json.dumps(event), flush=True)
print("stub diagnostic", file=sys.stderr, flush=True)
"""


def test_success_preserves_complete_private_trace_and_resolved_metadata(tmp_path: Path) -> None:
    result, output, prompt = _run(tmp_path, SUCCESS_STUB)

    assert result.returncode == 0, result.stderr
    assert "The source reports" not in result.stdout
    assert "stub diagnostic" not in result.stderr
    summary = _summary(output)
    trace = (output / "stdout.jsonl").read_bytes()
    stderr = (output / "stderr.log").read_bytes()
    assert summary["accepted"] is True
    assert summary["acceptance_scope"] == "transport_and_trace_integrity_only"
    assert summary["requested_model"] == "opus"
    assert summary["resolved_model"] == "claude-opus-5"
    assert summary["init_tools"] == [
        "mcp__clinpgx__search_records",
        "mcp__clinpgx__get_record",
    ]
    assert summary["init_mcp_servers"] == [{"name": "clinpgx", "status": "connected"}]
    assert summary["prompt_sha256"] == hashlib.sha256(prompt).hexdigest()
    assert len(summary["git_sha"]) == 40
    assert summary["termination_reason"] == "completed"
    assert summary["exit_code"] == 0
    assert summary["calls"] == [
        {
            "id": "call-1",
            "name": "mcp__clinpgx__search_records",
            "is_error": False,
            "result_received": True,
        }
    ]
    assert summary["errors"] == []
    assert summary["usage"] == {
        "cache_creation_input_tokens": 23,
        "cache_read_input_tokens": 29,
        "input_tokens": 11,
        "output_tokens": 17,
    }
    assert summary["total_cost_usd"] == 0.125
    assert summary["model_usage"]["claude-opus-5"]["costUSD"] == 0.125
    assert summary["final_response"] == "The source reports CYP2C19."
    assert summary["trace_sha256"] == hashlib.sha256(trace).hexdigest()
    assert summary["trace_size_bytes"] == len(trace)
    assert stderr == b"stub diagnostic\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


@pytest.mark.parametrize(
    ("stub_body", "deadline", "calls", "trace_bytes", "reason"),
    [
        ("time.sleep(5)\n", "0.15", "4", "65536", "deadline"),
        (
            """
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-5",
 "tools": ["mcp__clinpgx__get_record"],
 "mcp_servers": [{"name": "clinpgx", "status": "connected"}]}), flush=True)
print(json.dumps({"type": "assistant", "message": {"content": [
 {"type": "tool_use", "id": "a", "name": "mcp__clinpgx__get_record", "input": {}},
 {"type": "tool_use", "id": "b", "name": "mcp__clinpgx__get_record", "input": {}}
]}}), flush=True)
time.sleep(5)
""",
            "3",
            "1",
            "65536",
            "tool_call_limit",
        ),
        (
            "sys.stdout.write('x' * 10000); sys.stdout.flush(); time.sleep(5)\n",
            "3",
            "4",
            "512",
            "trace_byte_limit",
        ),
    ],
)
def test_hard_limits_terminate_process_group_and_invalidate_acceptance(
    tmp_path: Path,
    stub_body: str,
    deadline: str,
    calls: str,
    trace_bytes: str,
    reason: str,
) -> None:
    result, output, _ = _run(
        tmp_path,
        stub_body,
        deadline=deadline,
        calls=calls,
        trace_bytes=trace_bytes,
    )

    assert result.returncode == 1
    summary = _summary(output)
    assert summary["accepted"] is False
    assert summary["termination_reason"] == reason
    assert summary["result_received"] is False
    assert reason in summary["acceptance_failures"]
    if reason == "trace_byte_limit":
        assert summary["trace_size_bytes"] <= 512
        assert summary["trace_truncated"] is True


@pytest.mark.parametrize(
    ("stub_body", "failure"),
    [
        (
            """
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-5",
 "tools": ["mcp__clinpgx__get_record"],
 "mcp_servers": [{"name": "clinpgx", "status": "connected"}]}))
""",
            "missing_result",
        ),
        (
            """
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5",
 "tools": ["Read", "mcp__clinpgx__get_record"],
 "mcp_servers": [{"name": "other", "status": "connected"}]}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
 "result": "wrong runtime", "usage": {}, "total_cost_usd": 0}))
""",
            "model_mismatch",
        ),
        (
            """
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-5",
 "tools": ["mcp__clinpgx__get_record"],
 "mcp_servers": [{"name": "clinpgx", "status": "connected"}]}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
 "usage": {}, "total_cost_usd": 0}))
""",
            "missing_final_response",
        ),
    ],
)
def test_missing_result_or_runtime_substitution_invalidates_acceptance(
    tmp_path: Path, stub_body: str, failure: str
) -> None:
    result, output, _ = _run(tmp_path, stub_body)

    assert result.returncode == 1
    summary = _summary(output)
    assert summary["accepted"] is False
    assert failure in summary["acceptance_failures"]


@pytest.mark.parametrize(
    ("assistant_content", "failure"),
    [
        (
            [{"type": "tool_use", "id": "orphan", "name": "mcp__clinpgx__get_record", "input": {}}],
            "unmatched_tool_calls",
        ),
        (
            [
                {"type": "tool_use", "id": "outside", "name": "Read", "input": {}},
                {
                    "type": "tool_result",
                    "tool_use_id": "outside",
                    "is_error": False,
                    "content": "x",
                },
            ],
            "unexpected_tool_calls",
        ),
    ],
)
def test_trace_rejects_unmatched_or_out_of_allowlist_tool_calls(
    tmp_path: Path, assistant_content: list[dict[str, Any]], failure: str
) -> None:
    body = f"""
print(json.dumps({{"type": "system", "subtype": "init", "model": "claude-opus-5",
 "tools": ["mcp__clinpgx__get_record"],
 "mcp_servers": [{{"name": "clinpgx", "status": "connected"}}]}}))
print(json.dumps({{"type": "assistant", "message": {{"content": {assistant_content!r}}}}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
 "result": "answer", "usage": {{}}, "total_cost_usd": 0}}))
"""
    result, output, _ = _run(tmp_path, body)

    assert result.returncode == 1
    assert failure in _summary(output)["acceptance_failures"]


def test_pathological_nested_json_is_recorded_as_invalid_trace(tmp_path: Path) -> None:
    body = "sys.stdout.write('[' * 10000 + '0' + ']' * 10000 + '\\n')\n"

    result, output, _ = _run(tmp_path, body)

    assert result.returncode == 1
    assert "invalid_jsonl" in _summary(output)["acceptance_failures"]


def test_capture_failure_kills_and_reaps_the_spawned_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child_pid_path = tmp_path / "child.pid"
    _stub(
        tmp_path,
        f"""
child = os.fork()
if child == 0:
    time.sleep(5)
    raise SystemExit(0)
pathlib.Path({str(child_pid_path)!r}).write_text(str(child))
sys.stdin.read()
time.sleep(5)
""",
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("bounded prompt\n")
    output = tmp_path / "run"
    spawned: list[subprocess.Popen[bytes]] = []

    def fail_capture(process: subprocess.Popen[bytes], *_args: object) -> None:
        spawned.append(process)
        deadline = time.monotonic() + 2
        while not child_pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert child_pid_path.exists()
        raise OSError("simulated artifact write failure")

    monkeypatch.setattr(benchmark_agent, "_capture", fail_capture)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    args = Namespace(
        mcp_url="http://127.0.0.1:18765/mcp",
        prompt_file=prompt,
        output_dir=output,
        model="opus",
        deadline_seconds=3.0,
        max_tool_calls=4,
        max_trace_bytes=65536,
    )

    with pytest.raises(OSError, match="simulated artifact"):
        benchmark_agent.run(args)

    assert len(spawned) == 1
    assert spawned[0].poll() is not None
    child_pid = int(child_pid_path.read_text())
    deadline = time.monotonic() + 1
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_existing_output_directory_is_never_reused(tmp_path: Path) -> None:
    output = tmp_path / "run"
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_text("keep")

    result, _, _ = _run(tmp_path, "raise AssertionError('must not launch')\n")

    assert result.returncode == 2
    assert sentinel.read_text() == "keep"
    assert set(output.iterdir()) == {sentinel}


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:18765/mcp",
        "http://example.com:18765/mcp",
        "http://user@127.0.0.1:18765/mcp",
        "http://127.0.0.1:18765/mcp?token=secret",
    ],
)
def test_noncanonical_or_nonloopback_mcp_url_is_rejected(tmp_path: Path, url: str) -> None:
    result, output, _ = _run(tmp_path, "raise AssertionError('must not launch')\n", url=url)

    assert result.returncode == 2
    assert not output.exists()
