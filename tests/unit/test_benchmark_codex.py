from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from scripts.benchmark_codex import AppServerSession, SessionError, _private_file
from scripts.benchmark_codex_isolation import (
    EXPECTED_SYSTEM_SKILLS,
    RunInputError,
    build_sandbox_command,
    create_private_run_directory,
    open_prompt,
    validate_home_environment,
    validate_loopback_url,
    validate_preflight,
    verify_descendants,
)
from scripts.benchmark_codex_protocol import ProtocolTrace, assess_acceptance

TOOLS = {
    "search_records": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "response_mode": {
                "type": "string",
                "enum": ["minimal", "compact", "standard", "full"],
                "default": "compact",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "get_record": {
        "type": "object",
        "properties": {"id": {"type": "string", "minLength": 1}},
        "required": ["id"],
        "additionalProperties": False,
    },
}


def _message(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"method": method, "params": params}


def _item(
    method: str,
    *,
    item_id: str,
    tool: str,
    arguments: dict[str, Any],
    status: str,
    result: dict[str, Any] | None = None,
    thread_id: str = "thread-1",
    turn_id: str = "turn-1",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": item_id,
        "type": "mcpToolCall",
        "server": "clinpgx",
        "tool": tool,
        "arguments": arguments,
        "status": status,
    }
    if result is not None:
        item["result"] = result
    return _message(
        method,
        {"threadId": thread_id, "turnId": turn_id, "item": item},
    )


def _success_result() -> dict[str, Any]:
    return {"structuredContent": {"success": True, "data": {"records": []}}}


def _error_result(code: str = "invalid_input") -> dict[str, Any]:
    return {
        "structuredContent": {
            "success": False,
            "error": {"code": code, "message": "Safe public error."},
        }
    }


def _trace() -> ProtocolTrace:
    return ProtocolTrace(
        thread_id="thread-1",
        advertised_tools=TOOLS,
        requested_model="gpt-5.6-terra",
        requested_provider="openai",
        requested_effort="high",
        observed_identity={
            "model": "gpt-5.6-terra",
            "modelProvider": "openai",
            "reasoningEffort": "high",
            "instructionSources": [],
        },
        max_calls=4,
    )


def _complete_trace(trace: ProtocolTrace) -> None:
    trace.observe(
        _message("turn/started", {"turn": {"id": "turn-1", "status": "inProgress"}}),
        10,
    )
    trace.observe(
        _item(
            "item/started",
            item_id="a",
            tool="search_records",
            arguments={"query": "CYP2C19", "response_mode": "minimal"},
            status="inProgress",
        ),
        20,
    )
    trace.observe(
        _item(
            "item/completed",
            item_id="a",
            tool="search_records",
            arguments={"query": "CYP2C19", "response_mode": "minimal"},
            status="completed",
            result=_success_result(),
        ),
        30,
    )
    trace.observe(
        _message(
            "item/completed",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {
                    "id": "answer",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "Grounded answer.",
                },
            },
        ),
        40,
    )
    trace.observe(
        _message(
            "turn/completed",
            {"turn": {"id": "turn-1", "status": "completed"}},
        ),
        50,
    )


def test_multicall_trace_accepts_concurrent_completion_order() -> None:
    trace = _trace()
    trace.observe(
        _message("turn/started", {"turn": {"id": "turn-1", "status": "inProgress"}}),
        10,
    )
    trace.observe(
        _item(
            "item/started",
            item_id="a",
            tool="search_records",
            arguments={"query": "CYP2C19"},
            status="inProgress",
        ),
        20,
    )
    trace.observe(
        _item(
            "item/started",
            item_id="b",
            tool="get_record",
            arguments={"id": "PA1"},
            status="inProgress",
        ),
        21,
    )
    trace.observe(
        _item(
            "item/completed",
            item_id="b",
            tool="get_record",
            arguments={"id": "PA1"},
            status="completed",
            result=_success_result(),
        ),
        30,
    )
    trace.observe(
        _item(
            "item/completed",
            item_id="a",
            tool="search_records",
            arguments={"query": "CYP2C19"},
            status="completed",
            result=_success_result(),
        ),
        35,
    )
    trace.observe(
        _message(
            "item/completed",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {
                    "id": "answer",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "Done.",
                },
            },
        ),
        40,
    )
    trace.observe(
        _message("turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}),
        50,
    )

    result = assess_acceptance(trace, termination="completed", trace_complete=True)

    assert result["accepted"] is True
    assert [call["id"] for call in result["calls"]] == ["a", "b"]
    assert [call["completed_elapsed_ms"]["value"] for call in result["calls"]] == [35, 30]


def test_valid_mcp_error_is_observed_and_recovery_can_succeed() -> None:
    trace = _trace()
    trace.observe(
        _message("turn/started", {"turn": {"id": "turn-1", "status": "inProgress"}}),
        1,
    )
    trace.observe(
        _item(
            "item/started",
            item_id="bad",
            tool="search_records",
            arguments={"query": "unknown"},
            status="inProgress",
        ),
        2,
    )
    trace.observe(
        _item(
            "item/completed",
            item_id="bad",
            tool="search_records",
            arguments={"query": "unknown"},
            status="completed",
            result=_error_result(),
        ),
        3,
    )
    trace.observe(
        _item(
            "item/started",
            item_id="recovery",
            tool="get_record",
            arguments={"id": "PA1"},
            status="inProgress",
        ),
        4,
    )
    trace.observe(
        _item(
            "item/completed",
            item_id="recovery",
            tool="get_record",
            arguments={"id": "PA1"},
            status="completed",
            result=_success_result(),
        ),
        5,
    )
    trace.observe(
        _message(
            "item/completed",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {
                    "id": "answer",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "Recovered.",
                },
            },
        ),
        6,
    )
    trace.observe(
        _message("turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}),
        7,
    )

    result = assess_acceptance(trace, termination="completed", trace_complete=True)

    assert result["accepted"] is True
    assert result["errors"] == [{"call_id": "bad", "code": "invalid_input"}]
    assert [call["outcome"] for call in result["calls"]] == ["mcp_error", "success"]


@pytest.mark.parametrize(
    "code",
    [
        "invalid_input",
        "not_found",
        "ambiguous_query",
        "upstream_unavailable",
        "rate_limited",
        "internal",
    ],
)
def test_each_public_mcp_error_code_is_a_valid_observation(code: str) -> None:
    trace = _trace()
    trace.observe(
        _message("turn/started", {"turn": {"id": "turn-1", "status": "inProgress"}}),
        1,
    )
    trace.observe(
        _item(
            "item/started",
            item_id="error",
            tool="get_record",
            arguments={"id": "missing"},
            status="inProgress",
        ),
        2,
    )
    trace.observe(
        _item(
            "item/completed",
            item_id="error",
            tool="get_record",
            arguments={"id": "missing"},
            status="completed",
            result=_error_result(code),
        ),
        3,
    )
    trace.observe(
        _message(
            "item/completed",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {
                    "id": "answer",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "Reported the source error.",
                },
            },
        ),
        4,
    )
    trace.observe(
        _message("turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}),
        5,
    )

    result = assess_acceptance(trace, termination="completed", trace_complete=True)

    assert result["accepted"] is True
    assert result["errors"] == [{"call_id": "error", "code": code}]


@pytest.mark.parametrize(
    ("identity", "event", "failure"),
    [
        (
            {"modelProvider": "openai", "reasoningEffort": "high", "instructionSources": []},
            None,
            "model_identity_missing",
        ),
        (
            {
                "model": "gpt-5.6-terra-rerouted",
                "modelProvider": "openai",
                "reasoningEffort": "high",
                "instructionSources": [],
            },
            None,
            "model_identity_mismatch",
        ),
        (
            {
                "model": "gpt-5.6-terra",
                "modelProvider": "other",
                "reasoningEffort": "high",
                "instructionSources": [],
            },
            None,
            "model_provider_mismatch",
        ),
        (
            {
                "model": "gpt-5.6-terra",
                "modelProvider": "openai",
                "reasoningEffort": "low",
                "instructionSources": [],
            },
            None,
            "reasoning_effort_mismatch",
        ),
        (
            {
                "model": "gpt-5.6-terra",
                "modelProvider": "openai",
                "reasoningEffort": "high",
                "instructionSources": ["AGENTS.md"],
            },
            None,
            "instruction_sources_present",
        ),
        (
            {
                "model": "gpt-5.6-terra",
                "modelProvider": "openai",
                "reasoningEffort": "high",
                "instructionSources": [],
            },
            _message("model/rerouted", {"fromModel": "gpt-5.6-terra", "toModel": "other"}),
            "model_rerouted",
        ),
    ],
)
def test_identity_and_reroute_fail_closed(
    identity: dict[str, Any], event: dict[str, Any] | None, failure: str
) -> None:
    trace = ProtocolTrace(
        thread_id="thread-1",
        advertised_tools=TOOLS,
        requested_model="gpt-5.6-terra",
        requested_provider="openai",
        requested_effort="high",
        observed_identity=identity,
        max_calls=4,
    )
    _complete_trace(trace)
    if event is not None:
        trace.observe(event, 55)

    result = assess_acceptance(trace, termination="completed", trace_complete=True)

    assert result["accepted"] is False
    assert failure in result["failures"]


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        ("non_clinpgx", "unexpected_mcp_server"),
        ("builtin", "forbidden_tool_item"),
        ("no_tool", "missing_mcp_call"),
        ("no_final", "missing_final_answer"),
        ("missing_start", "completed_without_start"),
        ("missing_completion", "incomplete_mcp_call"),
        ("duplicate", "duplicate_mcp_start"),
        ("wrong_thread", "thread_id_mismatch"),
        ("wrong_turn", "turn_id_mismatch"),
        ("failed_transport", "failed_mcp_transport"),
    ],
)
def test_invalid_lifecycle_and_forbidden_tools_fail_closed(mutation: str, failure: str) -> None:
    trace = _trace()
    if mutation == "no_tool":
        trace.observe(_message("turn/started", {"turn": {"id": "turn-1"}}), 1)
        trace.observe(
            _message(
                "item/completed",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {
                        "id": "answer",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "No call.",
                    },
                },
            ),
            2,
        )
        trace.observe(
            _message("turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}), 3
        )
    elif mutation == "builtin":
        trace.observe(_message("turn/started", {"turn": {"id": "turn-1"}}), 1)
        trace.observe(
            _message(
                "item/started",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {"id": "shell", "type": "commandExecution"},
                },
            ),
            2,
        )
    else:
        trace.observe(_message("turn/started", {"turn": {"id": "turn-1"}}), 1)
        started = _item(
            "item/started",
            item_id="a",
            tool="search_records",
            arguments={"query": "CYP2C19"},
            status="inProgress",
        )
        completed = _item(
            "item/completed",
            item_id="a",
            tool="search_records",
            arguments={"query": "CYP2C19"},
            status="completed",
            result=_success_result(),
        )
        if mutation != "missing_start":
            trace.observe(started, 2)
        if mutation == "duplicate":
            trace.observe(started, 3)
        elif mutation != "missing_completion":
            if mutation == "non_clinpgx":
                completed["params"]["item"]["server"] = "other"
            if mutation == "wrong_thread":
                completed["params"]["threadId"] = "thread-2"
            if mutation == "wrong_turn":
                completed["params"]["turnId"] = "turn-2"
            if mutation == "failed_transport":
                completed["params"]["item"].update(status="failed", error={"message": "down"})
            trace.observe(completed, 4)
        if mutation == "no_final":
            trace.observe(
                _message("turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}), 5
            )

    result = assess_acceptance(trace, termination="completed", trace_complete=True)

    assert result["accepted"] is False
    assert failure in result["failures"]


@pytest.mark.parametrize(
    ("termination", "complete", "failure"),
    [
        ("deadline", True, "deadline"),
        ("tool_call_limit", True, "tool_call_limit"),
        ("trace_byte_limit", False, "trace_byte_limit"),
        ("truncated_jsonl", False, "truncated_jsonl"),
    ],
)
def test_hard_limits_and_incomplete_streams_fail_closed(
    termination: str, complete: bool, failure: str
) -> None:
    trace = _trace()
    _complete_trace(trace)

    result = assess_acceptance(trace, termination=termination, trace_complete=complete)

    assert result["accepted"] is False
    assert failure in result["failures"]


def test_missing_usage_cost_and_scheduler_timing_are_null_with_reasons() -> None:
    trace = _trace()
    _complete_trace(trace)

    result = assess_acceptance(trace, termination="completed", trace_complete=True)

    assert result["accepted"] is True
    assert result["usage"] == {"value": None, "reason": "app_server_did_not_report_usage"}
    assert result["cost_usd"] == {"value": None, "reason": "app_server_does_not_report_cost"}
    assert result["scheduler_queue_ms"] == {
        "value": None,
        "reason": "app_server_does_not_report_scheduler_queue_time",
    }
    assert result["client_turn_duration_ms"] == {"value": 40, "reason": None}
    assert result["calls"][0]["server_duration_ms"] == {
        "value": None,
        "reason": "app_server_tool_item_did_not_report_duration",
    }
    assert result["calls"][0]["boundary_elapsed_ms"] == {
        "value": None,
        "reason": "mcp_envelope_did_not_report_boundary_timing",
    }


def _preflight_fixture() -> dict[str, Any]:
    skills = [
        {"name": name, "scope": "system", "enabled": True, "pluginId": None}
        for name in sorted(EXPECTED_SYSTEM_SKILLS)
    ]
    return {
        "initialize": {"userAgent": "codex_cli_rs/0.153.4", "codexHome": "/home/test/.codex"},
        "config": {
            "config": {"mcp_servers": {"clinpgx": {"required": True, "enabled": True}}},
            "layers": [
                {"name": {"type": "user"}, "config": {}},
            ],
        },
        "hooks": {"data": []},
        "skills": {"data": [{"skills": skills}]},
        "plugins": {"marketplaces": []},
        "thread": {
            "model": "gpt-5.6-terra",
            "modelProvider": "openai",
            "reasoningEffort": "high",
            "instructionSources": [],
            "thread": {"id": "thread-1", "ephemeral": True},
        },
        "mcp": {
            "data": [
                {
                    "name": "clinpgx",
                    "pluginId": None,
                    "runtimeStatus": "connected",
                    "tools": {name: {"inputSchema": schema} for name, schema in TOOLS.items()},
                }
            ]
        },
    }


def test_preflight_preserves_runtime_schemas_and_pinned_client_inputs() -> None:
    evidence = validate_preflight(
        _preflight_fixture(), expected_codex_home=Path("/home/test/.codex")
    )

    assert evidence["failures"] == []
    assert evidence["tool_names"] == ["get_record", "search_records"]
    assert evidence["client_version"] == "0.153.4"
    assert evidence["intrinsic_system_skills"] == sorted(EXPECTED_SYSTEM_SKILLS)
    assert set(evidence["tool_schemas"]) == set(TOOLS)
    assert all(len(value["sha256"]) == 64 for value in evidence["tool_schemas"].values())


@pytest.mark.parametrize(
    ("mutate", "failure"),
    [
        (
            lambda value: value["config"]["config"]["mcp_servers"].update(other={}),
            "unexpected_configured_mcp",
        ),
        (lambda value: value["hooks"]["data"].append({"hooks": [{}]}), "hooks_present"),
        (
            lambda value: value["skills"]["data"][0]["skills"].append(
                {"name": "user-skill", "scope": "user", "enabled": True}
            ),
            "unexpected_skills",
        ),
        (
            lambda value: value["plugins"]["marketplaces"].append({"plugins": [{}]}),
            "plugins_present",
        ),
        (
            lambda value: value["thread"].update(instructionSources=["AGENTS.md"]),
            "instruction_sources_present",
        ),
        (lambda value: value["mcp"]["data"][0].update(runtimeStatus="failed"), "mcp_not_connected"),
        (
            lambda value: value["mcp"]["data"][0]["tools"]["search_records"].update(
                inputSchema={"type": "array"}
            ),
            "invalid_tool_schema",
        ),
    ],
)
def test_isolation_preflight_rejects_unexpected_inventory(mutate: Any, failure: str) -> None:
    fixture = _preflight_fixture()
    mutate(fixture)

    evidence = validate_preflight(fixture, expected_codex_home=Path("/home/test/.codex"))

    assert failure in evidence["failures"]


def test_descendant_verification_requires_exact_executable_paths() -> None:
    allowed = {Path("/usr/bin/bwrap"), Path("/opt/codex/bin/codex")}

    assert (
        verify_descendants([{"pid": 1, "ppid": 0, "executable": "/usr/bin/bwrap"}], allowed)[
            "failures"
        ]
        == []
    )
    rejected = verify_descendants(
        [{"pid": 2, "ppid": 1, "executable": "/opt/unexpected/codex"}],
        allowed,
    )
    assert rejected["failures"] == ["unexpected_descendant"]


def test_private_artifacts_prompt_and_sandbox_command(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("immutable prompt\n")
    handle, metadata = open_prompt(prompt, max_bytes=1024)
    try:
        assert handle.read() == b"immutable prompt\n"
    finally:
        handle.close()
    assert metadata["bytes"] == 17

    output = create_private_run_directory(tmp_path / "output")
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    with pytest.raises(RunInputError, match="already exists"):
        create_private_run_directory(output)

    command = build_sandbox_command(
        bwrap=Path("/usr/bin/bwrap"),
        codex=Path("/opt/codex/bin/codex"),
        home=Path("/home/test"),
        auth=Path("/home/test/.codex/auth.json"),
        cwd=Path("/tmp/empty"),  # noqa: S108 - intended namespace path
        mcp_url="http://127.0.0.1:18765/mcp",
    )
    joined = " ".join(command)
    assert "--ro-bind / /" in joined
    assert "--tmpfs /home/test/.codex" in joined
    assert "--ro-bind /home/test/.codex/auth.json /home/test/.codex/auth.json" in joined
    assert "--tmpfs /home/test/.agents" in joined
    assert "gpt-5.6-terra" in joined and "high" in joined
    assert "shell_tool" in command and 'web_search="disabled"' in command


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:18765/mcp",
        "http://example.com:18765/mcp",
        "http://user@127.0.0.1:18765/mcp",
        "http://127.0.0.1:18765/mcp?secret=x",
    ],
)
def test_input_validation_rejects_nonloopback_or_noncanonical_urls(url: str) -> None:
    with pytest.raises(RunInputError):
        validate_loopback_url(url)


def test_home_and_codex_home_must_remain_at_original_locations() -> None:
    assert validate_home_environment("/home/test", Path("/home/test"), None) == Path("/home/test")
    with pytest.raises(RunInputError, match="HOME"):
        validate_home_environment("/alternate", Path("/home/test"), None)
    with pytest.raises(RunInputError, match="CODEX_HOME"):
        validate_home_environment("/home/test", Path("/home/test"), "/alternate")


def test_prompt_symlink_and_oversize_are_rejected(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_bytes(b"12345")
    link = tmp_path / "link.md"
    link.symlink_to(prompt)

    with pytest.raises(RunInputError, match="prompt"):
        open_prompt(link, max_bytes=1024)
    with pytest.raises(RunInputError, match="size"):
        open_prompt(prompt, max_bytes=4)


def test_runtime_argument_schema_rejects_empty_required_and_accepts_valid_defaults() -> None:
    trace = _trace()
    trace.observe(_message("turn/started", {"turn": {"id": "turn-1"}}), 1)
    trace.observe(
        _item(
            "item/started",
            item_id="a",
            tool="search_records",
            arguments={},
            status="inProgress",
        ),
        2,
    )

    result = assess_acceptance(trace, termination="completed", trace_complete=True)

    assert "invalid_tool_arguments" in result["failures"]

    valid = _trace()
    _complete_trace(valid)
    assert assess_acceptance(valid, termination="completed", trace_complete=True)["accepted"]


def test_tool_call_after_final_answer_is_rejected() -> None:
    trace = _trace()
    _complete_trace(trace)
    trace.observe(
        _item(
            "item/started",
            item_id="late",
            tool="get_record",
            arguments={"id": "PA2"},
            status="inProgress",
        ),
        60,
    )

    result = assess_acceptance(trace, termination="completed", trace_complete=True)

    assert result["accepted"] is False
    assert "mcp_call_after_final" in result["failures"]


def test_turn_and_item_order_require_started_inprogress_before_work() -> None:
    trace = _trace()
    trace.observe(
        _item(
            "item/started",
            item_id="early",
            tool="get_record",
            arguments={"id": "PA2"},
            status="inProgress",
        ),
        1,
    )
    trace.observe(
        _message("turn/started", {"turn": {"id": "turn-1", "status": "completed"}}),
        2,
    )

    result = assess_acceptance(trace, termination="completed", trace_complete=True)

    assert "item_before_turn_start" in result["failures"]
    assert "invalid_turn_start_status" in result["failures"]


def _stub_process(body: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - isolated synthetic test program
        [sys.executable, "-c", body],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def test_jsonrpc_process_captures_complete_turn_and_discards_stderr(tmp_path: Path) -> None:
    body = f"""
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if request.get('method') == 'turn/start':
        print(json.dumps({{'id': request['id'], 'result': {{'turn': {{'id': 'turn-1'}}}}}}), flush=True)
        events = {
        [
            _message("turn/started", {"turn": {"id": "turn-1", "status": "inProgress"}}),
            _item(
                "item/started",
                item_id="a",
                tool="search_records",
                arguments={"query": "CYP2C19"},
                status="inProgress",
            ),
            _item(
                "item/completed",
                item_id="a",
                tool="search_records",
                arguments={"query": "CYP2C19"},
                status="completed",
                result=_success_result(),
            ),
            _message(
                "item/completed",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {
                        "id": "answer",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "Done.",
                    },
                },
            ),
            _message("turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}),
        ]!r
    }
        for event in events:
            print(json.dumps(event), flush=True)
        print('private diagnostic', file=sys.stderr, flush=True)
"""
    process = _stub_process(body)
    trace_path = tmp_path / "trace.jsonl"
    trace = _trace()
    with trace_path.open("xb") as output:
        session = AppServerSession(
            process,
            trace_output=output,
            max_trace_bytes=65536,
            deadline=time.monotonic() + 3,
        )
        session.begin_turn(trace)
        response = session.request("turn/start", 8, {"threadId": "thread-1"})
        session.wait_for_terminal()
        session.close()

    assert response == {"turn": {"id": "turn-1"}}
    assert assess_acceptance(trace, termination="completed", trace_complete=True)["accepted"]
    assert b"private diagnostic" not in trace_path.read_bytes()
    assert session.stderr_bytes > 0


@pytest.mark.parametrize(
    ("body", "limit", "deadline", "reason"),
    [
        (
            "import sys; sys.stdout.write('x' * 2048); sys.stdout.flush()",
            256,
            3.0,
            "trace_byte_limit",
        ),
        (
            "import sys; sys.stdout.write('{\\\"method\\\":'); sys.stdout.flush()",
            4096,
            3.0,
            "truncated_jsonl",
        ),
        ("import time; time.sleep(5)", 4096, 0.1, "deadline"),
    ],
)
def test_jsonrpc_process_rejects_oversize_truncated_and_deadline(
    tmp_path: Path, body: str, limit: int, deadline: float, reason: str
) -> None:
    process = _stub_process(body)
    with (tmp_path / "trace.jsonl").open("xb") as output:
        session = AppServerSession(
            process,
            trace_output=output,
            max_trace_bytes=limit,
            deadline=time.monotonic() + deadline,
        )
        session.begin_turn(_trace())
        with pytest.raises(SessionError) as caught:
            session.next_message()
        session.close()
    assert caught.value.reason == reason


def test_jsonrpc_process_stops_at_call_cap(tmp_path: Path) -> None:
    events = [
        _message("turn/started", {"turn": {"id": "turn-1", "status": "inProgress"}}),
        _item(
            "item/started",
            item_id="a",
            tool="search_records",
            arguments={"query": "a"},
            status="inProgress",
        ),
        _item(
            "item/started",
            item_id="b",
            tool="search_records",
            arguments={"query": "b"},
            status="inProgress",
        ),
    ]
    body = f"import json\nfor event in {events!r}: print(json.dumps(event), flush=True)\n"
    process = _stub_process(body)
    capped = ProtocolTrace(
        thread_id="thread-1",
        advertised_tools=TOOLS,
        requested_model="gpt-5.6-terra",
        requested_provider="openai",
        requested_effort="high",
        observed_identity={
            "model": "gpt-5.6-terra",
            "modelProvider": "openai",
            "reasoningEffort": "high",
            "instructionSources": [],
        },
        max_calls=1,
    )
    with (tmp_path / "trace.jsonl").open("xb") as output:
        session = AppServerSession(
            process,
            trace_output=output,
            max_trace_bytes=4096,
            deadline=time.monotonic() + 3,
        )
        session.begin_turn(capped)
        with pytest.raises(SessionError) as caught:
            while True:
                session.next_message()
        session.close()
    assert caught.value.reason == "tool_call_limit"


def test_jsonrpc_preflight_rejects_model_reroute_before_turn(tmp_path: Path) -> None:
    body = """
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({"method": "model/rerouted", "params": {
        "fromModel": "gpt-5.6-terra", "toModel": "other"
    }}), flush=True)
"""
    process = _stub_process(body)
    with (tmp_path / "trace.jsonl").open("xb") as output:
        session = AppServerSession(
            process,
            trace_output=output,
            max_trace_bytes=4096,
            deadline=time.monotonic() + 2,
        )
        with pytest.raises(SessionError) as caught:
            session.request("thread/start", 6, {})
        session.close()

    assert caught.value.reason == "model_rerouted"
    assert session.notification_methods == ["model/rerouted"]


def test_session_close_kills_residual_process_group_and_private_file_is_exclusive(
    tmp_path: Path,
) -> None:
    child_pid = tmp_path / "child.pid"
    body = f"""
import os, pathlib, time
child = os.fork()
if child == 0:
    os.close(0); os.close(1); os.close(2)
    time.sleep(5)
    os._exit(0)
pathlib.Path({str(child_pid)!r}).write_text(str(child))
time.sleep(5)
"""
    process = _stub_process(body)
    deadline = time.monotonic() + 2
    while not child_pid.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert child_pid.exists()
    artifact = tmp_path / "artifact.json"
    with _private_file(artifact) as output:
        session = AppServerSession(
            process,
            trace_output=output,
            max_trace_bytes=4096,
            deadline=time.monotonic() + 2,
        )
        session.close()

    pid = int(child_pid.read_text())
    deadline = time.monotonic() + 1
    while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        _private_file(artifact)


def test_entrypoint_is_directly_executable_without_import_path_configuration() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/benchmark_codex.py", "--help"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--mcp-url" in result.stdout
