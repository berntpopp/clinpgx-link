"""Trace-first adapter for retained Claude Code benchmark artifacts."""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from scripts.evaluation_contracts import Measurements, canonical_bytes
from scripts.evaluation_traces import (
    AdapterInputError,
    NormalizedCall,
    NormalizedRun,
    RunExpectation,
    add_failure,
    parse_json,
    parse_json_line,
    read_artifacts,
)

_PREFIX = "mcp__clinpgx__"
_ERROR_CODES = {
    "invalid_input",
    "not_found",
    "ambiguous_query",
    "upstream_unavailable",
    "rate_limited",
    "internal",
}


def _same(left: object, right: object) -> bool:
    try:
        return canonical_bytes(left) == canonical_bytes(right)
    except (TypeError, ValueError, RecursionError):
        return False


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            converted = float(value)
        except OverflowError:
            return None
        return converted if math.isfinite(converted) and converted >= 0 else None
    return None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _texts(content: object) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        item["text"]
        for item in content
        if isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
    ]


def _application_envelope(content: object) -> tuple[dict[str, Any] | None, int]:
    texts = _texts(content)
    byte_count = sum(len(text.encode("utf-8")) for text in texts)
    for text in texts:
        try:
            value = parse_json(text.encode("utf-8"))
        except AdapterInputError:
            continue
        if isinstance(value, dict) and isinstance(value.get("success"), bool):
            return value, byte_count
    return None, byte_count


def _summary_call(call: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call.get("call_id"),
        "name": call.get("tool"),
        "is_error": call.get("outcome") == "mcp_error",
        "result_received": call.get("result_ordinal") is not None,
    }


def _as_mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_claude_run(directory: Path, expected: RunExpectation) -> NormalizedRun:
    summary_raw, trace_raw = read_artifacts(directory, "stdout.jsonl", expected)
    summary_value = parse_json(summary_raw)
    if not isinstance(summary_value, dict):
        raise AdapterInputError("malformed_artifact")
    summary = summary_value
    failures: list[str] = []
    limitations = [
        "claude_wire_mirror_not_observed",
        "claude_effort_not_configured_or_exposed",
        "claude_duration_is_runner_monotonic",
    ]
    calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    init: dict[str, Any] | None = None
    terminal: dict[str, Any] | None = None
    assistant_models: list[str | None] = []
    capture_complete = trace_raw.endswith(b"\n") or not trace_raw
    lifecycle_error = False
    returned_bytes = 0

    for ordinal, raw_line in enumerate(io.BytesIO(trace_raw)):
        if not raw_line.endswith(b"\n"):
            capture_complete = False
            add_failure(failures, "partial_jsonl_tail")
            break
        line = raw_line[:-1]
        if not line:
            raise AdapterInputError("malformed_artifact")
        event = parse_json_line(line)
        if terminal is not None:
            lifecycle_error = True
        event_type = event.get("type")
        if event_type == "system" and event.get("subtype") == "init":
            if init is not None:
                add_failure(failures, "duplicate_init")
            else:
                init = event
        elif event_type == "result":
            if init is None or terminal is not None:
                lifecycle_error = True
            if terminal is None:
                terminal = event
        message = event.get("message")
        if message is not None and init is None:
            lifecycle_error = True
        if event_type == "assistant":
            if not isinstance(message, dict):
                add_failure(failures, "malformed_assistant_event")
            else:
                model = message.get("model")
                assistant_models.append(model if isinstance(model, str) else None)
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                call_id = block.get("id")
                tool = block.get("name")
                arguments = block.get("input")
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or call_id in by_id
                    or not isinstance(tool, str)
                    or not isinstance(arguments, dict)
                ):
                    add_failure(failures, "malformed_tool_use")
                    continue
                new_call: dict[str, Any] = {
                    "index": len(calls),
                    "call_id": call_id,
                    "tool": tool,
                    "arguments": arguments,
                    "envelope": None,
                    "outcome": "incomplete",
                    "error_code": None,
                    "boundary_elapsed_ms": None,
                    "use_ordinal": ordinal,
                    "result_ordinal": None,
                }
                calls.append(new_call)
                by_id[call_id] = new_call
            elif block.get("type") == "tool_result":
                call_id = block.get("tool_use_id")
                matched_call = by_id.get(call_id) if isinstance(call_id, str) else None
                if matched_call is None or matched_call["result_ordinal"] is not None:
                    add_failure(failures, "unpaired_tool_result")
                    continue
                matched_call["result_ordinal"] = ordinal
                envelope, text_bytes = _application_envelope(block.get("content"))
                returned_bytes += text_bytes
                if envelope is None:
                    matched_call["outcome"] = "transport_failure"
                    add_failure(failures, "missing_application_envelope")
                    continue
                matched_call["envelope"] = envelope
                success = envelope.get("success")
                if success is True and block.get("is_error") is not True:
                    matched_call["outcome"] = "success"
                elif success is False:
                    code = envelope.get("error_code")
                    if not isinstance(code, str) or code not in _ERROR_CODES:
                        matched_call["outcome"] = "transport_failure"
                        add_failure(failures, "invalid_mcp_error_code")
                    else:
                        matched_call["outcome"] = "mcp_error"
                        matched_call["error_code"] = code
                else:
                    matched_call["outcome"] = "transport_failure"
                    add_failure(failures, "inconsistent_tool_result")
                meta = envelope.get("_meta")
                elapsed = meta.get("elapsed_ms") if isinstance(meta, dict) else None
                if isinstance(meta, dict) and meta.get("timing_scope") == "tool_boundary":
                    matched_call["boundary_elapsed_ms"] = _number(elapsed)

    if lifecycle_error:
        add_failure(failures, "invalid_event_lifecycle")
    if not capture_complete:
        add_failure(failures, "trace_incomplete")
    if init is None:
        add_failure(failures, "missing_init")
    if terminal is None:
        add_failure(failures, "missing_result")
    for call in calls:
        if call["result_ordinal"] is None:
            add_failure(failures, "incomplete_mcp_call")
    if len(calls) > expected.hard_call_limit:
        add_failure(failures, "hard_call_limit_exceeded")

    init_value = init or {}
    observed_model = init_value.get("model")
    observed_model = observed_model if isinstance(observed_model, str) and observed_model else None
    tools = init_value.get("tools")
    valid_tools = (
        isinstance(tools, list)
        and bool(tools)
        and all(isinstance(tool, str) and tool.startswith(_PREFIX) for tool in tools)
    )
    if not valid_tools:
        add_failure(failures, "unexpected_tools")
    for call in calls:
        tool_name = call.get("tool")
        if (
            not isinstance(tool_name, str)
            or not tool_name.startswith(_PREFIX)
            or not isinstance(tools, list)
            or tool_name not in tools
        ):
            add_failure(failures, "unexpected_tool_calls")
    if init_value.get("mcp_servers") != [{"name": "clinpgx", "status": "connected"}]:
        add_failure(failures, "unexpected_mcp_servers")
    if observed_model != expected.expected_model:
        add_failure(failures, "observed_model_mismatch")
    if any(model != observed_model for model in assistant_models):
        add_failure(failures, "assistant_model_mismatch")
    if expected.requested_effort is not None:
        add_failure(failures, "effort_pin_unverifiable")

    terminal_value = terminal or {}
    final = terminal_value.get("result")
    final = final if isinstance(final, str) and final else None
    if terminal is not None and (
        terminal_value.get("subtype") != "success"
        or terminal_value.get("is_error") is not False
        or final is None
    ):
        add_failure(failures, "unsuccessful_result")

    claims = (
        (summary.get("schema_version"), 1, "summary_schema_mismatch"),
        (summary.get("git_sha"), expected.candidate_sha, "candidate_sha_mismatch"),
        (summary.get("prompt_sha256"), expected.prompt_sha256, "prompt_digest_mismatch"),
        (summary.get("requested_model"), expected.requested_model, "requested_model_mismatch"),
        (summary.get("resolved_model"), observed_model, "summary_model_mismatch"),
        (summary.get("deadline_seconds"), expected.deadline_seconds, "deadline_mismatch"),
        (summary.get("max_tool_calls"), expected.hard_call_limit, "call_limit_mismatch"),
        (summary.get("max_trace_bytes"), expected.trace_limit_bytes, "trace_limit_mismatch"),
        (summary.get("trace_sha256"), expected.trace_sha256, "summary_trace_digest_mismatch"),
        (summary.get("trace_size_bytes"), len(trace_raw), "summary_trace_size_mismatch"),
        (summary.get("init_tools"), tools, "summary_tool_inventory_mismatch"),
        (summary.get("init_mcp_servers"), init_value.get("mcp_servers"), "summary_server_mismatch"),
        (summary.get("assistant_models"), assistant_models, "summary_model_events_mismatch"),
        (summary.get("final_response"), final, "summary_final_answer_mismatch"),
        (
            [_summary_call(call) for call in calls],
            [
                {key: row.get(key) for key in ("id", "name", "is_error", "result_received")}
                for row in summary.get("calls", [])
                if isinstance(row, dict)
            ]
            if isinstance(summary.get("calls"), list)
            else None,
            "summary_calls_mismatch",
        ),
    )
    for actual, claimed, reason in claims:
        if not _same(actual, claimed):
            add_failure(failures, reason)
    raw_usage = terminal_value.get("usage")
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    model_usage = terminal_value.get("modelUsage")
    model_usage = model_usage if isinstance(model_usage, dict) else {}
    projected_usage = {
        key: usage.get(key)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    }
    if not _same(summary.get("usage"), projected_usage) or not _same(
        summary.get("model_usage"), model_usage
    ):
        add_failure(failures, "summary_usage_mismatch")
    derived_errors = [
        {"kind": "tool_result", "tool_use_id": call["call_id"]}
        for call in calls
        if call["outcome"] == "mcp_error"
    ]
    if not _same(summary.get("errors"), derived_errors):
        add_failure(failures, "summary_errors_mismatch")
    if not _same(summary.get("total_cost_usd"), terminal_value.get("total_cost_usd")):
        add_failure(failures, "summary_cost_mismatch")
    if summary.get("accepted") is not True:
        add_failure(failures, "summary_not_accepted")
    if summary.get("acceptance_failures") != []:
        add_failure(failures, "summary_reported_failures")
    if summary.get("termination_reason") != "completed" or summary.get("exit_code") != 0:
        add_failure(failures, "runner_transport_failed")
    if summary.get("result_received") is not (terminal is not None):
        add_failure(failures, "summary_result_presence_mismatch")
    trace_truncated = summary.get("trace_truncated")
    if trace_truncated is not False:
        capture_complete = False
        add_failure(failures, "trace_incomplete")
        if trace_truncated is not True:
            add_failure(failures, "summary_trace_truncation_invalid")

    normalized_calls = tuple(
        NormalizedCall(
            index=call["index"],
            call_id=call["call_id"],
            tool=call["tool"],
            arguments=call["arguments"],
            envelope=call["envelope"],
            outcome=call["outcome"],
            error_code=call["error_code"],
            boundary_elapsed_ms=call["boundary_elapsed_ms"],
            client_started_elapsed_ms=None,
            client_completed_elapsed_ms=None,
            raw_event_ordinals=(
                (call["use_ordinal"], call["result_ordinal"])
                if call["result_ordinal"] is not None
                else (call["use_ordinal"],)
            ),
            wire_mirror="not_observed",
        )
        for call in calls
    )
    duration = _number(summary.get("duration_seconds"))
    if duration is None:
        raise AdapterInputError("malformed_artifact")
    measurements = Measurements(
        input_tokens=_count(usage.get("input_tokens")),
        output_tokens=_count(usage.get("output_tokens")),
        cache_read_input_tokens=_count(usage.get("cache_read_input_tokens")),
        cache_creation_input_tokens=_count(usage.get("cache_creation_input_tokens")),
        total_cost_usd=_number(terminal_value.get("total_cost_usd")),
        returned_text_bytes=returned_bytes,
    )
    lifecycle_complete = (
        init is not None
        and terminal is not None
        and final is not None
        and not lifecycle_error
        and all(call["result_ordinal"] is not None for call in calls)
    )
    summary_requested_model = summary.get("requested_model")
    requested_model = (
        summary_requested_model
        if isinstance(summary_requested_model, str)
        else expected.requested_model
    )
    try:
        return NormalizedRun(
            consumer="opus",
            candidate_sha=expected.candidate_sha,
            prompt_sha256=expected.prompt_sha256,
            summary_sha256=expected.summary_sha256,
            trace_sha256=expected.trace_sha256,
            trace_bytes=len(trace_raw),
            requested_model=requested_model,
            expected_model=expected.expected_model,
            observed_model=observed_model,
            requested_effort=None,
            observed_effort=None,
            effort_unavailable_reason="not_configured_or_exposed",
            model_rerouted=observed_model not in {None, expected.expected_model},
            transport_passed=not failures and capture_complete and lifecycle_complete,
            capture_complete=capture_complete,
            lifecycle_complete=lifecycle_complete,
            hard_call_limit=expected.hard_call_limit,
            deadline_seconds=expected.deadline_seconds,
            trace_limit_bytes=expected.trace_limit_bytes,
            duration_seconds=duration,
            calls=normalized_calls,
            final_answer=final,
            measurements=measurements,
            failures=tuple(failures),
            limitations=tuple(limitations),
            raw_usage={"usage": usage, "model_usage": model_usage}
            if usage or model_usage
            else None,
        )
    except (ValidationError, TypeError, ValueError, RecursionError) as exc:
        raise AdapterInputError("malformed_artifact") from exc


__all__ = ["normalize_claude_run"]
