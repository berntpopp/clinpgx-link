from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from scripts.benchmark_codex_protocol import canonical_sha256
from scripts.evaluation_traces import AdapterInputError, RunExpectation, normalize_run

SHA = "a" * 40
PROMPT = "Synthetic source task."
SUCCESS = {
    "success": True,
    "data": {"text": "<external-source>synthetic evidence</external-source>"},
    "_meta": {"elapsed_ms": 4.5, "timing_scope": "tool_boundary"},
}
ERROR = {
    "success": False,
    "error_code": "invalid_input",
    "message": "Synthetic invalid input.",
}
SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}},
    "required": ["id"],
    "additionalProperties": False,
}


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for row in rows
    )


def _private_run(tmp_path: Path, trace_name: str, trace: bytes, summary: dict[str, Any]) -> Path:
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    summary_raw = (json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n").encode()
    for name, raw in ((trace_name, trace), ("summary.json", summary_raw)):
        path = run / name
        path.write_bytes(raw)
        path.chmod(0o600)
    return run


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _rewrite(path: Path, value: object) -> None:
    path.write_bytes((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode())
    path.chmod(0o600)


def _rewrite_trace(run: Path, trace_name: str, rows: list[dict[str, Any]]) -> None:
    trace = _jsonl(rows)
    (run / trace_name).write_bytes(trace)
    (run / trace_name).chmod(0o600)
    summary = _read_json(run / "summary.json")
    if trace_name == "stdout.jsonl":
        summary["trace_sha256"] = hashlib.sha256(trace).hexdigest()
        summary["trace_size_bytes"] = len(trace)
    else:
        summary["trace"]["sha256"] = hashlib.sha256(trace).hexdigest()
        summary["trace"]["bytes"] = len(trace)
        summary["trace"]["records"] = len(rows)
    _rewrite(run / "summary.json", summary)


def _expectation(run: Path, consumer: str, **overrides: object) -> RunExpectation:
    trace_name = "stdout.jsonl" if consumer == "opus" else "trace.jsonl"
    values: dict[str, object] = {
        "consumer": consumer,
        "candidate_sha": SHA,
        "expected_model": "claude-opus-5" if consumer == "opus" else "gpt-5.6-terra",
        "requested_model": "opus" if consumer == "opus" else "gpt-5.6-terra",
        "requested_effort": None if consumer == "opus" else "high",
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "summary_sha256": hashlib.sha256((run / "summary.json").read_bytes()).hexdigest(),
        "trace_sha256": hashlib.sha256((run / trace_name).read_bytes()).hexdigest(),
        "hard_call_limit": 4,
        "deadline_seconds": 30.0,
        "trace_limit_bytes": 65536,
    }
    values.update(overrides)
    return RunExpectation.model_validate(values)


def _claude_artifacts(tmp_path: Path, *, envelope: dict[str, Any] = SUCCESS) -> Path:
    content = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    rows = [
        {
            "type": "system",
            "subtype": "init",
            "model": "claude-opus-5",
            "tools": ["mcp__clinpgx__get_record"],
            "mcp_servers": [{"name": "clinpgx", "status": "connected"}],
        },
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-5",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "mcp__clinpgx__get_record",
                        "input": {"id": "SYNTHETIC"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "is_error": envelope["success"] is False,
                        "content": content,
                    }
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Synthetic final answer.",
            "duration_ms": 900,
            "total_cost_usd": 0.25,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_creation_input_tokens": 2,
                "cache_read_input_tokens": 3,
            },
            "modelUsage": {"claude-opus-5": {"inputTokens": 10}},
        },
    ]
    trace = _jsonl(rows)
    summary = {
        "schema_version": 1,
        "accepted": True,
        "acceptance_failures": [],
        "duration_seconds": 0.9,
        "deadline_seconds": 30.0,
        "max_tool_calls": 4,
        "max_trace_bytes": 65536,
        "requested_model": "opus",
        "resolved_model": "claude-opus-5",
        "init_tools": ["mcp__clinpgx__get_record"],
        "init_mcp_servers": [{"name": "clinpgx", "status": "connected"}],
        "assistant_models": ["claude-opus-5"],
        "git_sha": SHA,
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "termination_reason": "completed",
        "exit_code": 0,
        "result_received": True,
        "trace_truncated": False,
        "trace_sha256": hashlib.sha256(trace).hexdigest(),
        "trace_size_bytes": len(trace),
        "calls": [
            {
                "id": "call-1",
                "name": "mcp__clinpgx__get_record",
                "is_error": envelope["success"] is False,
                "result_received": True,
            }
        ],
        "errors": (
            [{"call_id": "call-1", "code": "invalid_input"}] if envelope["success"] is False else []
        ),
        "usage": rows[-1]["usage"],
        "total_cost_usd": 0.25,
        "model_usage": rows[-1]["modelUsage"],
        "final_response": "Synthetic final answer.",
    }
    return _private_run(tmp_path, "stdout.jsonl", trace, summary)


def _wire(envelope: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "structuredContent": envelope,
    }


def _terra_artifacts(tmp_path: Path, *, envelope: dict[str, Any] = SUCCESS) -> Path:
    status = "completed" if envelope["success"] is True else "failed"
    request = {
        "method": "turn/start",
        "id": 8,
        "params": {
            "threadId": "thread-1",
            "input": [{"type": "text", "text": PROMPT}],
            "model": "gpt-5.6-terra",
            "effort": "high",
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "readOnly"},
        },
    }
    start = {
        "method": "item/started",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {
                "id": "call-1",
                "type": "mcpToolCall",
                "server": "clinpgx",
                "tool": "get_record",
                "arguments": {"id": "SYNTHETIC"},
                "status": "inProgress",
            },
        },
    }
    completed = json.loads(json.dumps(start))
    completed["method"] = "item/completed"
    completed["params"]["item"].update(
        {"status": status, "error": None, "durationMs": 6, "result": _wire(envelope)}
    )
    messages = [
        ("client_to_server", 1, request),
        ("server_to_client", 2, {"id": 8, "result": {"turn": {"id": "turn-1"}}}),
        (
            "server_to_client",
            3,
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "inProgress"},
                },
            },
        ),
        ("server_to_client", 4, start),
        ("server_to_client", 10, completed),
        (
            "server_to_client",
            10,
            {
                "method": "thread/tokenUsage/updated",
                "params": {"tokenUsage": {"totalTokenUsage": {"inputTokens": 12}}},
            },
        ),
        (
            "server_to_client",
            11,
            {
                "method": "item/started",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {"id": "answer", "type": "agentMessage", "phase": "final_answer"},
                },
            },
        ),
        (
            "server_to_client",
            12,
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {
                        "id": "answer",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "Synthetic final answer.",
                    },
                },
            },
        ),
        (
            "server_to_client",
            13,
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            },
        ),
    ]
    trace = _jsonl(
        [
            {"direction": direction, "elapsed_ms": elapsed, "message": message}
            for direction, elapsed, message in messages
        ]
    )
    errors = (
        [{"call_id": "call-1", "code": "invalid_input"}] if envelope["success"] is False else []
    )
    preflight = {
        "failures": [],
        "client_version": "0.153.4",
        "tool_names": ["get_record"],
        "tool_schemas": {"get_record": {"schema": SCHEMA, "sha256": canonical_sha256(SCHEMA)}},
        "observed_client_identity": {
            "model": "gpt-5.6-terra",
            "modelProvider": "openai",
            "reasoningEffort": "high",
            "instructionSources": [],
        },
    }
    summary = {
        "schema": "clinpgx-codex-benchmark-v1",
        "accepted": True,
        "termination_reason": "completed",
        "client_run_duration_ms": 20,
        "revision": SHA,
        "requested": {
            "model": "gpt-5.6-terra",
            "model_provider": "openai",
            "reasoning_effort": "high",
            "max_tool_calls": 4,
            "deadline_seconds": 30.0,
            "max_trace_bytes": 65536,
        },
        "observed_client_identity": preflight["observed_client_identity"],
        "prompt": {
            "bytes": len(PROMPT.encode()),
            "sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        },
        "trace": {
            "path": "trace.jsonl",
            "bytes": len(trace),
            "sha256": hashlib.sha256(trace).hexdigest(),
            "complete": True,
            "records": len(messages),
        },
        "preflight": preflight,
        "turn": {
            "accepted": True,
            "failures": [],
            "calls": [
                {
                    "id": "call-1",
                    "tool": "get_record",
                    "arguments": {"id": "SYNTHETIC"},
                    "outcome": "mcp_error" if errors else "success",
                    "started_elapsed_ms": {"value": 4, "reason": None},
                    "completed_elapsed_ms": {"value": 10, "reason": None},
                    "boundary_elapsed_ms": {"value": 4.5, "reason": None},
                }
            ],
            "errors": errors,
            "final_answer": "Synthetic final answer.",
            "observed_client_identity": preflight["observed_client_identity"],
            "usage": {"value": {"totalTokenUsage": {"inputTokens": 12}}, "reason": None},
        },
        "processes": {"failures": []},
    }
    return _private_run(tmp_path, "trace.jsonl", trace, summary)


def test_claude_success_preserves_envelope_fences_and_counter_origins(tmp_path: Path) -> None:
    run = _claude_artifacts(tmp_path)

    result = normalize_run(run, expected=_expectation(run, "opus"))

    assert result.transport_passed is True
    assert result.lifecycle_complete is True
    assert result.calls[0].envelope == SUCCESS
    assert result.calls[0].wire_mirror == "not_observed"
    assert result.calls[0].arguments == {"id": "SYNTHETIC"}
    assert result.measurements.returned_text_bytes == len(
        json.dumps(SUCCESS, ensure_ascii=False, separators=(",", ":")).encode()
    )
    assert result.measurements.input_tokens == 10
    assert result.raw_usage == {
        "model_usage": {"claude-opus-5": {"inputTokens": 10}},
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_creation_input_tokens": 2,
            "cache_read_input_tokens": 3,
        },
    }
    assert "claude_wire_mirror_not_observed" in result.limitations


@pytest.mark.parametrize(
    ("consumer", "builder"), [("opus", _claude_artifacts), ("terra", _terra_artifacts)]
)
def test_typed_error_is_a_call_outcome_not_a_transport_failure(
    tmp_path: Path, consumer: str, builder: Any
) -> None:
    run = builder(tmp_path, envelope=ERROR)

    result = normalize_run(run, expected=_expectation(run, consumer))

    assert result.transport_passed is True
    assert result.calls[0].outcome == "mcp_error"
    assert result.calls[0].error_code == "invalid_input"


def test_terra_success_verifies_both_wire_copies_and_lifecycle(tmp_path: Path) -> None:
    run = _terra_artifacts(tmp_path)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert result.transport_passed is True
    assert result.capture_complete is True
    assert result.lifecycle_complete is True
    assert result.observed_model == "gpt-5.6-terra"
    assert result.observed_effort == "high"
    assert result.calls[0].wire_mirror == "verified"
    assert result.calls[0].raw_event_ordinals == (3, 4)
    assert result.measurements.returned_text_bytes == len(
        json.dumps(SUCCESS, ensure_ascii=False, separators=(",", ":")).encode()
    )
    assert result.raw_usage == {"totalTokenUsage": {"inputTokens": 12}}
    assert "terra_preflight_is_runner_observed" in result.limitations


def test_hash_mismatch_raises_only_fixed_safe_reason(tmp_path: Path) -> None:
    run = _claude_artifacts(tmp_path)
    expected = _expectation(run, "opus", trace_sha256="0" * 64)

    with pytest.raises(AdapterInputError) as caught:
        normalize_run(run, expected=expected)

    assert caught.value.reason == "hash_mismatch"
    assert str(run) not in str(caught.value)


def test_symlink_artifact_is_rejected_without_following_it(tmp_path: Path) -> None:
    run = _claude_artifacts(tmp_path)
    expected = _expectation(run, "opus")
    target = run / "kept"
    target.write_bytes((run / "stdout.jsonl").read_bytes())
    target.chmod(0o600)
    (run / "stdout.jsonl").unlink()
    os.symlink(target, run / "stdout.jsonl")

    with pytest.raises(AdapterInputError, match="unsafe_io"):
        normalize_run(run, expected=expected)


def test_failed_claude_run_with_missing_model_is_recorded_not_raised(tmp_path: Path) -> None:
    trace = b""
    summary = {
        "schema_version": 1,
        "accepted": False,
        "acceptance_failures": ["missing_init", "missing_result"],
        "duration_seconds": 1.0,
        "deadline_seconds": 30.0,
        "max_tool_calls": 4,
        "max_trace_bytes": 65536,
        "requested_model": "opus",
        "resolved_model": None,
        "init_tools": None,
        "init_mcp_servers": None,
        "assistant_models": [],
        "git_sha": SHA,
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "termination_reason": "deadline",
        "exit_code": -15,
        "result_received": False,
        "trace_truncated": False,
        "trace_sha256": hashlib.sha256(trace).hexdigest(),
        "trace_size_bytes": 0,
        "calls": [],
        "errors": [],
        "usage": {},
        "model_usage": {},
        "total_cost_usd": None,
        "final_response": None,
    }
    run = _private_run(tmp_path, "stdout.jsonl", trace, summary)

    result = normalize_run(run, expected=_expectation(run, "opus"))

    assert result.observed_model is None
    assert result.transport_passed is False
    assert result.lifecycle_complete is False
    assert result.final_answer is None
    assert "missing_init" in result.failures


@pytest.mark.parametrize(
    ("consumer", "field", "replacement", "failure"),
    [
        ("opus", "calls", [], "summary_calls_mismatch"),
        ("opus", "final_response", "Invented final.", "summary_final_answer_mismatch"),
        ("terra", "turn.calls", [], "summary_calls_mismatch"),
        ("terra", "turn.final_answer", "Invented final.", "summary_final_answer_mismatch"),
    ],
)
def test_summary_cannot_rewrite_trace_facts(
    tmp_path: Path, consumer: str, field: str, replacement: object, failure: str
) -> None:
    run = _claude_artifacts(tmp_path) if consumer == "opus" else _terra_artifacts(tmp_path)
    summary = _read_json(run / "summary.json")
    target = summary
    parts = field.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = replacement
    _rewrite(run / "summary.json", summary)

    result = normalize_run(run, expected=_expectation(run, consumer))

    assert result.transport_passed is False
    assert failure in result.failures


def test_fake_accepted_summary_cannot_supply_missing_claude_final(tmp_path: Path) -> None:
    run = _claude_artifacts(tmp_path)
    rows = [json.loads(line) for line in (run / "stdout.jsonl").read_text().splitlines()][:-1]
    _rewrite_trace(run, "stdout.jsonl", rows)

    result = normalize_run(run, expected=_expectation(run, "opus"))

    assert result.final_answer is None
    assert result.lifecycle_complete is False
    assert result.transport_passed is False
    assert "missing_result" in result.failures


def test_partial_jsonl_tail_is_preserved_as_incomplete_failed_capture(tmp_path: Path) -> None:
    run = _terra_artifacts(tmp_path)
    trace_path = run / "trace.jsonl"
    trace = trace_path.read_bytes() + b'{"direction":"server_to_client"'
    trace_path.write_bytes(trace)
    trace_path.chmod(0o600)
    summary = _read_json(run / "summary.json")
    summary["trace"].update(
        {
            "sha256": hashlib.sha256(trace).hexdigest(),
            "bytes": len(trace),
            "complete": False,
        }
    )
    _rewrite(run / "summary.json", summary)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert result.capture_complete is False
    assert result.transport_passed is False
    assert "partial_jsonl_tail" in result.failures


def test_duplicate_protocol_key_is_malformed_even_with_matching_external_hashes(
    tmp_path: Path,
) -> None:
    run = _terra_artifacts(tmp_path)
    trace_path = run / "trace.jsonl"
    trace = trace_path.read_bytes().replace(
        b'{"direction":"client_to_server",',
        b'{"direction":"client_to_server","direction":"client_to_server",',
        1,
    )
    trace_path.write_bytes(trace)
    trace_path.chmod(0o600)
    summary = _read_json(run / "summary.json")
    summary["trace"]["sha256"] = hashlib.sha256(trace).hexdigest()
    summary["trace"]["bytes"] = len(trace)
    _rewrite(run / "summary.json", summary)

    with pytest.raises(AdapterInputError, match="malformed_artifact"):
        normalize_run(run, expected=_expectation(run, "terra"))


def test_terra_mirror_comparison_is_type_sensitive_for_bool_and_int(tmp_path: Path) -> None:
    run = _terra_artifacts(tmp_path)
    rows = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
    completed = rows[4]["message"]["params"]["item"]["result"]
    completed["structuredContent"]["success"] = 1
    _rewrite_trace(run, "trace.jsonl", rows)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert result.transport_passed is False
    assert result.calls[0].wire_mirror == "invalid"
    assert "mcp_mirror_mismatch" in result.failures


def test_terra_preflight_schema_digest_and_inventory_are_verified(tmp_path: Path) -> None:
    run = _terra_artifacts(tmp_path)
    summary = _read_json(run / "summary.json")
    summary["preflight"]["tool_schemas"]["get_record"]["sha256"] = "0" * 64
    _rewrite(run / "summary.json", summary)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert result.transport_passed is False
    assert "invalid_preflight_tool_schema" in result.failures


@pytest.mark.parametrize(
    ("consumer", "overrides", "failure"),
    [
        ("opus", {"expected_model": "claude-sonnet-5"}, "observed_model_mismatch"),
        ("opus", {"requested_effort": "high"}, "effort_pin_unverifiable"),
        ("terra", {"expected_model": "gpt-5.6-luna"}, "observed_model_mismatch"),
        ("terra", {"requested_effort": "low"}, "observed_effort_mismatch"),
    ],
)
def test_model_and_effort_pins_never_invent_an_observation(
    tmp_path: Path, consumer: str, overrides: dict[str, object], failure: str
) -> None:
    run = _claude_artifacts(tmp_path) if consumer == "opus" else _terra_artifacts(tmp_path)

    result = normalize_run(run, expected=_expectation(run, consumer, **overrides))

    assert result.transport_passed is False
    assert failure in result.failures


def test_terra_forbidden_tool_and_transport_error_are_retained_nonpassing(tmp_path: Path) -> None:
    run = _terra_artifacts(tmp_path)
    rows = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
    for row in rows[3:5]:
        item = row["message"]["params"]["item"]
        item["server"] = "builtin"
        item["tool"] = "Read"
    rows[4]["message"]["params"]["item"]["error"] = {"message": "synthetic"}
    _rewrite_trace(run, "trace.jsonl", rows)
    summary = _read_json(run / "summary.json")
    summary["turn"]["calls"][0].update({"tool": "Read", "outcome": "transport_failure"})
    summary["accepted"] = False
    summary["turn"]["accepted"] = False
    _rewrite(run / "summary.json", summary)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert result.calls[0].tool == "Read"
    assert result.calls[0].outcome == "transport_failure"
    assert result.transport_passed is False
    assert "unexpected_mcp_server" in result.failures
    assert "failed_mcp_transport" in result.failures


def test_terra_duplicate_completion_is_nonpassing(tmp_path: Path) -> None:
    run = _terra_artifacts(tmp_path)
    rows = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
    duplicate = json.loads(json.dumps(rows[4]))
    duplicate["elapsed_ms"] = 10
    rows.insert(5, duplicate)
    _rewrite_trace(run, "trace.jsonl", rows)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert result.transport_passed is False
    assert "duplicate_mcp_completion" in result.failures


def test_terra_concurrent_calls_may_complete_out_of_start_order(tmp_path: Path) -> None:
    run = _terra_artifacts(tmp_path)
    rows = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
    second_start = json.loads(json.dumps(rows[3]))
    second_start["elapsed_ms"] = 5
    second_start["message"]["params"]["item"].update(
        {"id": "call-2", "arguments": {"id": "SYNTHETIC-2"}}
    )
    second_completed = json.loads(json.dumps(rows[4]))
    second_completed["elapsed_ms"] = 8
    second_completed["message"]["params"]["item"].update(
        {"id": "call-2", "arguments": {"id": "SYNTHETIC-2"}}
    )
    rows[4]["elapsed_ms"] = 10
    rows[4:4] = [second_start, second_completed]
    summary = _read_json(run / "summary.json")
    second_summary = json.loads(json.dumps(summary["turn"]["calls"][0]))
    second_summary.update({"id": "call-2", "arguments": {"id": "SYNTHETIC-2"}})
    summary["turn"]["calls"].append(second_summary)
    _rewrite(run / "summary.json", summary)
    _rewrite_trace(run, "trace.jsonl", rows)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert result.transport_passed is True
    assert [call.call_id for call in result.calls] == ["call-1", "call-2"]
    assert [call.client_completed_elapsed_ms for call in result.calls] == [10.0, 8.0]


def test_terra_argument_schema_is_applied_to_retained_calls(tmp_path: Path) -> None:
    run = _terra_artifacts(tmp_path)
    rows = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
    for row in rows[3:5]:
        row["message"]["params"]["item"]["arguments"] = {}
    _rewrite_trace(run, "trace.jsonl", rows)
    summary = _read_json(run / "summary.json")
    summary["turn"]["calls"][0]["arguments"] = {}
    _rewrite(run / "summary.json", summary)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert result.transport_passed is False
    assert "invalid_tool_arguments" in result.failures


def test_terra_reroute_notification_is_explicit_and_nonpassing(tmp_path: Path) -> None:
    run = _terra_artifacts(tmp_path)
    rows = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
    rows.insert(
        2,
        {
            "direction": "server_to_client",
            "elapsed_ms": 2,
            "message": {
                "method": "model/rerouted",
                "params": {"from": "gpt-5.6-terra", "to": "gpt-5.6-luna"},
            },
        },
    )
    _rewrite_trace(run, "trace.jsonl", rows)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert result.model_rerouted is True
    assert result.transport_passed is False
    assert "model_rerouted" in result.failures


def test_terra_summary_usage_cannot_replace_trace_usage(tmp_path: Path) -> None:
    run = _terra_artifacts(tmp_path)
    summary = _read_json(run / "summary.json")
    summary["turn"]["usage"]["value"]["totalTokenUsage"]["inputTokens"] = 999
    _rewrite(run / "summary.json", summary)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert result.raw_usage == {"totalTokenUsage": {"inputTokens": 12}}
    assert result.transport_passed is False
    assert "summary_usage_mismatch" in result.failures


def test_summary_declared_trace_size_mismatch_is_nonpassing(tmp_path: Path) -> None:
    run = _claude_artifacts(tmp_path)
    summary = _read_json(run / "summary.json")
    summary["trace_size_bytes"] += 1
    _rewrite(run / "summary.json", summary)

    result = normalize_run(run, expected=_expectation(run, "opus"))

    assert result.transport_passed is False
    assert "summary_trace_size_mismatch" in result.failures


def test_missing_claude_counters_and_cost_stay_null_not_zero(tmp_path: Path) -> None:
    run = _claude_artifacts(tmp_path)
    rows = [json.loads(line) for line in (run / "stdout.jsonl").read_text().splitlines()]
    rows[-1].pop("usage")
    rows[-1].pop("modelUsage")
    rows[-1].pop("total_cost_usd")
    _rewrite_trace(run, "stdout.jsonl", rows)
    summary = _read_json(run / "summary.json")
    summary.update({"usage": {}, "model_usage": {}, "total_cost_usd": None})
    _rewrite(run / "summary.json", summary)

    result = normalize_run(run, expected=_expectation(run, "opus"))

    assert result.measurements.input_tokens is None
    assert result.measurements.output_tokens is None
    assert result.measurements.total_cost_usd is None
    assert result.raw_usage is None


def test_artifact_mode_must_remain_private(tmp_path: Path) -> None:
    run = _claude_artifacts(tmp_path)
    expected = _expectation(run, "opus")
    (run / "summary.json").chmod(0o644)

    with pytest.raises(AdapterInputError, match="unsafe_io"):
        normalize_run(run, expected=expected)


def test_blank_jsonl_record_is_rejected_as_malformed_protocol(tmp_path: Path) -> None:
    run = _claude_artifacts(tmp_path)
    trace_path = run / "stdout.jsonl"
    trace = trace_path.read_bytes().replace(b"\n", b"\n\n", 1)
    trace_path.write_bytes(trace)
    trace_path.chmod(0o600)
    summary = _read_json(run / "summary.json")
    summary.update(
        {
            "trace_sha256": hashlib.sha256(trace).hexdigest(),
            "trace_size_bytes": len(trace),
        }
    )
    _rewrite(run / "summary.json", summary)

    with pytest.raises(AdapterInputError, match="malformed_artifact"):
        normalize_run(run, expected=_expectation(run, "opus"))


def test_terra_preflight_rechecks_schema_shape_and_client_version(tmp_path: Path) -> None:
    run = _terra_artifacts(tmp_path)
    summary = _read_json(run / "summary.json")
    schema = summary["preflight"]["tool_schemas"]["get_record"]["schema"]
    schema["required"] = "id"
    summary["preflight"]["tool_schemas"]["get_record"]["sha256"] = canonical_sha256(schema)
    summary["preflight"]["client_version"] = None
    _rewrite(run / "summary.json", summary)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert result.transport_passed is False
    assert "invalid_preflight_tool_schema" in result.failures
    assert "client_version_missing" in result.failures


def test_claude_typed_error_then_successful_recovery_retains_both_calls(tmp_path: Path) -> None:
    run = _claude_artifacts(tmp_path, envelope=ERROR)
    rows = [json.loads(line) for line in (run / "stdout.jsonl").read_text().splitlines()]
    rows[3:3] = [
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-5",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-2",
                        "name": "mcp__clinpgx__get_record",
                        "input": {"id": "RECOVERY"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-2",
                        "is_error": False,
                        "content": json.dumps(SUCCESS, separators=(",", ":")),
                    }
                ]
            },
        },
    ]
    _rewrite_trace(run, "stdout.jsonl", rows)
    summary = _read_json(run / "summary.json")
    summary["assistant_models"].append("claude-opus-5")
    summary["calls"].append(
        {
            "id": "call-2",
            "name": "mcp__clinpgx__get_record",
            "is_error": False,
            "result_received": True,
        }
    )
    _rewrite(run / "summary.json", summary)

    result = normalize_run(run, expected=_expectation(run, "opus"))

    assert result.transport_passed is True
    assert [call.outcome for call in result.calls] == ["mcp_error", "success"]
    assert result.final_answer == "Synthetic final answer."


def test_terra_typed_error_then_successful_recovery_retains_both_calls(tmp_path: Path) -> None:
    run = _terra_artifacts(tmp_path, envelope=ERROR)
    rows = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
    recovery_start = json.loads(json.dumps(rows[3]))
    recovery_start["elapsed_ms"] = 10
    recovery_start["message"]["params"]["item"].update(
        {"id": "call-2", "arguments": {"id": "RECOVERY"}}
    )
    recovery_complete = json.loads(json.dumps(rows[4]))
    recovery_complete["elapsed_ms"] = 10
    recovery_complete["message"]["params"]["item"].update(
        {
            "id": "call-2",
            "arguments": {"id": "RECOVERY"},
            "status": "completed",
            "result": _wire(SUCCESS),
        }
    )
    rows[5:5] = [recovery_start, recovery_complete]
    _rewrite_trace(run, "trace.jsonl", rows)
    summary = _read_json(run / "summary.json")
    summary["turn"]["calls"].append(
        {
            "id": "call-2",
            "tool": "get_record",
            "arguments": {"id": "RECOVERY"},
            "outcome": "success",
        }
    )
    _rewrite(run / "summary.json", summary)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert result.transport_passed is True
    assert [call.outcome for call in result.calls] == ["mcp_error", "success"]
    assert result.final_answer == "Synthetic final answer."


def test_terra_failed_run_keeps_completed_calls_when_final_and_turn_are_absent(
    tmp_path: Path,
) -> None:
    run = _terra_artifacts(tmp_path, envelope=ERROR)
    rows = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()][:-3]
    _rewrite_trace(run, "trace.jsonl", rows)
    summary = _read_json(run / "summary.json")
    summary["accepted"] = False
    summary["termination_reason"] = "deadline"
    summary["turn"]["accepted"] = False
    summary["turn"]["failures"] = ["deadline", "missing_final_answer"]
    summary["turn"]["final_answer"] = None
    _rewrite(run / "summary.json", summary)

    result = normalize_run(run, expected=_expectation(run, "terra"))

    assert len(result.calls) == 1
    assert result.calls[0].outcome == "mcp_error"
    assert result.final_answer is None
    assert result.lifecycle_complete is False
    assert result.transport_passed is False
    assert "missing_final_answer" in result.failures
