"""Trace-first adapter for retained Codex/Terra app-server artifacts."""

from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError

from scripts.benchmark_codex_protocol import ProtocolTrace, assess_acceptance, canonical_sha256
from scripts.evaluation_contracts import Measurements, canonical_bytes
from scripts.evaluation_traces import (
    AdapterInputError,
    CallOutcome,
    NormalizedCall,
    NormalizedRun,
    PublicErrorCode,
    RunExpectation,
    WireMirror,
    add_failure,
    parse_json,
    parse_json_line,
    read_artifacts,
)

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


def _elapsed(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) and converted >= 0 else None


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _valid_request_id(value: object) -> bool:
    return type(value) is int or (isinstance(value, str) and bool(value))


def _raw_completion(message: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if message.get("method") != "item/completed":
        return None
    params = message.get("params")
    item = params.get("item") if isinstance(params, dict) else None
    if not isinstance(item, dict) or item.get("type") != "mcpToolCall":
        return None
    call_id = item.get("id")
    return (call_id, item) if isinstance(call_id, str) else None


def _wire_envelope(item: dict[str, Any]) -> tuple[dict[str, Any] | None, str, int]:
    result = item.get("result")
    if not isinstance(result, dict):
        return None, "invalid", 0
    structured = result.get("structuredContent")
    content = result.get("content")
    if not isinstance(structured, dict) or not isinstance(content, list) or len(content) != 1:
        return None, "invalid", 0
    block = content[0]
    if not isinstance(block, dict) or block.get("type") != "text":
        return None, "invalid", 0
    text = block.get("text")
    if not isinstance(text, str):
        return None, "invalid", 0
    try:
        decoded = parse_json(text.encode("utf-8"))
    except AdapterInputError:
        return None, "invalid", len(text.encode("utf-8"))
    if not isinstance(decoded, dict) or not _same(decoded, structured):
        return None, "invalid", len(text.encode("utf-8"))
    return structured, "verified", len(text.encode("utf-8"))


def _summary_calls(value: object) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    result: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            return None
        result.append(
            {
                "id": row.get("id"),
                "tool": row.get("tool"),
                "arguments": row.get("arguments"),
                "outcome": row.get("outcome"),
            }
        )
    return result


def normalize_codex_run(directory: Path, expected: RunExpectation) -> NormalizedRun:
    summary_raw, trace_raw = read_artifacts(directory, "trace.jsonl", expected)
    summary_value = parse_json(summary_raw)
    if not isinstance(summary_value, dict):
        raise AdapterInputError("malformed_artifact")
    summary = summary_value
    requested = _mapping(summary.get("requested"))
    preflight = _mapping(summary.get("preflight"))
    identity = _mapping(preflight.get("observed_client_identity"))
    tool_schemas_value = preflight.get("tool_schemas")
    tool_schemas = tool_schemas_value if isinstance(tool_schemas_value, dict) else {}
    schemas: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    limitations = [
        "terra_preflight_is_runner_observed",
        "terra_identity_is_provider_claim_not_backend_attestation",
        "terra_wrapper_and_summary_timings_have_distinct_origins",
    ]
    if preflight.get("failures") != []:
        add_failure(failures, "preflight_reported_failures")
    client_version = preflight.get("client_version")
    if not isinstance(client_version, str) or not client_version:
        add_failure(failures, "client_version_missing")
    for name, evidence in tool_schemas.items():
        if not isinstance(name, str) or not isinstance(evidence, dict):
            add_failure(failures, "invalid_preflight_tool_schema")
            continue
        schema = evidence.get("schema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            add_failure(failures, "invalid_preflight_tool_schema")
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except (SchemaError, RecursionError):
            add_failure(failures, "invalid_preflight_tool_schema")
            continue
        if evidence.get("sha256") != canonical_sha256(schema):
            add_failure(failures, "invalid_preflight_tool_schema")
            continue
        schemas[name] = schema
    if not schemas or preflight.get("tool_names") != sorted(schemas):
        add_failure(failures, "preflight_tool_inventory_mismatch")

    observed_model_value = identity.get("model")
    observed_model = (
        observed_model_value
        if isinstance(observed_model_value, str) and observed_model_value
        else None
    )
    observed_effort_value = identity.get("reasoningEffort")
    observed_effort = (
        observed_effort_value
        if isinstance(observed_effort_value, str) and observed_effort_value
        else None
    )
    if identity.get("modelProvider") != "openai":
        add_failure(failures, "model_provider_mismatch")
    if identity.get("instructionSources") != []:
        add_failure(failures, "instruction_sources_present")
    if observed_model != expected.expected_model:
        add_failure(failures, "observed_model_mismatch")
    if observed_effort != expected.requested_effort:
        add_failure(failures, "observed_effort_mismatch")

    capture_complete = trace_raw.endswith(b"\n") or not trace_raw
    wrappers = io.BytesIO(trace_raw)
    trace: ProtocolTrace | None = None
    start_request: dict[str, Any] | None = None
    request_id: object = None
    turn_response_seen = False
    last_elapsed = -1.0
    raw_ordinals: dict[str, list[int]] = {}
    raw_items: dict[str, dict[str, Any]] = {}
    raw_times: dict[str, list[float]] = {}
    rerouted = False
    record_count = 0
    prompt_digest: str | None = None

    for ordinal, raw_line in enumerate(wrappers):
        if not raw_line.endswith(b"\n"):
            capture_complete = False
            add_failure(failures, "partial_jsonl_tail")
            break
        line = raw_line[:-1]
        if not line:
            raise AdapterInputError("malformed_artifact")
        wrapper = parse_json_line(line)
        record_count += 1
        if set(wrapper) != {"direction", "elapsed_ms", "message"}:
            raise AdapterInputError("malformed_artifact")
        direction = wrapper.get("direction")
        elapsed = _elapsed(wrapper.get("elapsed_ms"))
        message = wrapper.get("message")
        if direction not in {"client_to_server", "server_to_client"} or elapsed is None:
            raise AdapterInputError("malformed_artifact")
        if elapsed < last_elapsed:
            raise AdapterInputError("malformed_artifact")
        last_elapsed = elapsed
        if not isinstance(message, dict):
            raise AdapterInputError("malformed_artifact")
        method = message.get("method")
        if method == "model/rerouted":
            rerouted = True
        if direction == "client_to_server":
            if start_request is not None or method != "turn/start":
                add_failure(failures, "unexpected_client_message")
                continue
            start_request = message
            request_id = message.get("id")
            if not _valid_request_id(request_id):
                add_failure(failures, "turn_start_request_id_invalid")
            params = _mapping(message.get("params"))
            thread_id = params.get("threadId")
            if not isinstance(thread_id, str) or not thread_id:
                add_failure(failures, "turn_start_thread_missing")
                continue
            input_value = params.get("input")
            prompt_text: object = None
            if (
                isinstance(input_value, list)
                and len(input_value) == 1
                and isinstance(input_value[0], dict)
                and input_value[0].get("type") == "text"
            ):
                prompt_text = input_value[0].get("text")
            if isinstance(prompt_text, str):
                prompt_digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
            else:
                add_failure(failures, "turn_prompt_missing")
            if params.get("model") != expected.requested_model:
                add_failure(failures, "requested_model_mismatch")
            if params.get("effort") != expected.requested_effort:
                add_failure(failures, "requested_effort_mismatch")
            if params.get("approvalPolicy") != "never" or params.get("sandboxPolicy") != {
                "type": "readOnly"
            }:
                add_failure(failures, "turn_safety_policy_mismatch")
            trace = ProtocolTrace(
                thread_id=thread_id,
                advertised_tools=schemas,
                requested_model=expected.expected_model,
                requested_provider="openai",
                requested_effort=expected.requested_effort or "",
                observed_identity=identity,
                max_calls=expected.hard_call_limit,
            )
            continue
        response_value = message.get("result")
        if method is None and isinstance(response_value, dict) and not turn_response_seen:
            response_id = message.get("id")
            response_id_matches = (
                _valid_request_id(response_id)
                and type(response_id) is type(request_id)
                and response_id == request_id
            )
            response = message.get("result")
            turn = response.get("turn") if isinstance(response, dict) else None
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            observed_turn_id = trace.turn_id if trace is not None else None
            if (
                not response_id_matches
                or trace is None
                or not isinstance(turn_id, str)
                or not turn_id
                or observed_turn_id not in {None, turn_id}
            ):
                add_failure(failures, "turn_start_response_mismatch")
            else:
                if observed_turn_id is None:
                    trace.turn_id = turn_id
                turn_response_seen = True
            continue
        if trace is None:
            add_failure(failures, "server_event_before_turn_request")
            continue
        completion = _raw_completion(message)
        event_params = message.get("params")
        item = event_params.get("item") if isinstance(event_params, dict) else None
        if isinstance(item, dict) and item.get("type") == "mcpToolCall":
            call_id = item.get("id")
            if isinstance(call_id, str):
                raw_ordinals.setdefault(call_id, []).append(ordinal)
                raw_times.setdefault(call_id, []).append(elapsed)
        if completion is not None:
            raw_items[completion[0]] = completion[1]
        try:
            trace.observe(message, int(elapsed))
        except (AssertionError, TypeError, ValueError, RecursionError) as exc:
            raise AdapterInputError("malformed_artifact") from exc

    if not capture_complete:
        add_failure(failures, "trace_incomplete")
    if start_request is None:
        add_failure(failures, "missing_turn_start_request")
    if not turn_response_seen:
        add_failure(failures, "missing_turn_start_response")
    if prompt_digest != expected.prompt_sha256:
        add_failure(failures, "prompt_digest_mismatch")
    if rerouted:
        add_failure(failures, "model_rerouted")

    if trace is None:
        derived: dict[str, Any] = {
            "accepted": False,
            "failures": ["missing_turn_start_request"],
            "calls": [],
            "errors": [],
            "final_answer": None,
        }
    else:
        derived = assess_acceptance(
            trace,
            termination="completed"
            if summary.get("termination_reason") == "completed"
            else str(summary.get("termination_reason")),
            trace_complete=capture_complete,
            exit_code=None,
        )
        for reason in derived["failures"]:
            add_failure(failures, str(reason))

    calls: list[NormalizedCall] = []
    returned_bytes = 0
    for index, derived_call in enumerate(derived.get("calls", [])):
        call_id = derived_call.get("id")
        if not isinstance(call_id, str):
            raise AdapterInputError("malformed_artifact")
        item = raw_items.get(call_id)
        envelope: dict[str, Any] | None = None
        mirror: WireMirror = "invalid"
        text_bytes = 0
        if item is not None:
            envelope, raw_mirror, text_bytes = _wire_envelope(item)
            mirror = cast(WireMirror, raw_mirror)
        returned_bytes += text_bytes
        outcome_value = derived_call.get("outcome")
        outcome: CallOutcome
        if outcome_value not in {"success", "mcp_error", "transport_failure", "incomplete"}:
            outcome = "incomplete" if item is None else "transport_failure"
        else:
            outcome = cast(CallOutcome, outcome_value)
        code: PublicErrorCode | None = None
        if envelope is not None and envelope.get("success") is False:
            raw_code = envelope.get("error_code")
            if isinstance(raw_code, str) and raw_code in _ERROR_CODES:
                code = cast(PublicErrorCode, raw_code)
        if mirror != "verified":
            add_failure(failures, "mcp_mirror_mismatch")
        times = raw_times.get(call_id, [])
        ordinals = raw_ordinals.get(call_id, [])
        meta = envelope.get("_meta") if isinstance(envelope, dict) else None
        boundary = (
            _elapsed(meta.get("elapsed_ms"))
            if isinstance(meta, dict) and meta.get("timing_scope") == "tool_boundary"
            else None
        )
        calls.append(
            NormalizedCall(
                index=index,
                call_id=call_id,
                tool=derived_call.get("tool"),
                arguments=derived_call.get("arguments"),
                envelope=envelope,
                outcome=outcome,
                error_code=code,
                boundary_elapsed_ms=boundary,
                client_started_elapsed_ms=times[0] if times else None,
                client_completed_elapsed_ms=times[-1] if len(times) > 1 else None,
                raw_event_ordinals=tuple(ordinals),
                wire_mirror=mirror,
            )
        )

    turn_summary = _mapping(summary.get("turn"))
    derived_call_projection = [
        {
            "id": call.call_id,
            "tool": call.tool,
            "arguments": call.arguments,
            "outcome": call.outcome,
        }
        for call in calls
    ]
    comparisons = (
        (summary.get("schema"), "clinpgx-codex-benchmark-v1", "summary_schema_mismatch"),
        (summary.get("revision"), expected.candidate_sha, "candidate_sha_mismatch"),
        (requested.get("model"), expected.requested_model, "summary_requested_model_mismatch"),
        (requested.get("model_provider"), "openai", "summary_provider_mismatch"),
        (requested.get("reasoning_effort"), expected.requested_effort, "summary_effort_mismatch"),
        (requested.get("max_tool_calls"), expected.hard_call_limit, "call_limit_mismatch"),
        (requested.get("deadline_seconds"), expected.deadline_seconds, "deadline_mismatch"),
        (requested.get("max_trace_bytes"), expected.trace_limit_bytes, "trace_limit_mismatch"),
        (
            _mapping(summary.get("prompt")).get("sha256"),
            expected.prompt_sha256,
            "summary_prompt_digest_mismatch",
        ),
        (
            _mapping(summary.get("trace")).get("sha256"),
            expected.trace_sha256,
            "summary_trace_digest_mismatch",
        ),
        (
            _mapping(summary.get("trace")).get("bytes"),
            len(trace_raw),
            "summary_trace_size_mismatch",
        ),
        (
            _mapping(summary.get("trace")).get("records"),
            record_count,
            "summary_trace_count_mismatch",
        ),
        (summary.get("observed_client_identity"), identity, "summary_identity_mismatch"),
        (turn_summary.get("observed_client_identity"), identity, "turn_identity_mismatch"),
        (
            _summary_calls(turn_summary.get("calls")),
            derived_call_projection,
            "summary_calls_mismatch",
        ),
        (turn_summary.get("errors"), derived.get("errors"), "summary_errors_mismatch"),
        (
            turn_summary.get("final_answer"),
            derived.get("final_answer"),
            "summary_final_answer_mismatch",
        ),
        (turn_summary.get("usage"), derived.get("usage"), "summary_usage_mismatch"),
    )
    for actual, claimed, reason in comparisons:
        if not _same(actual, claimed):
            add_failure(failures, reason)
    trace_summary = _mapping(summary.get("trace"))
    if trace_summary.get("path") != "trace.jsonl":
        add_failure(failures, "summary_trace_path_mismatch")
    if trace_summary.get("complete") is not True:
        capture_complete = False
        add_failure(failures, "trace_incomplete")
    if summary.get("accepted") is not True or turn_summary.get("accepted") is not True:
        add_failure(failures, "summary_not_accepted")
    if turn_summary.get("failures") != []:
        add_failure(failures, "summary_reported_failures")
    processes = _mapping(summary.get("processes"))
    if processes.get("failures") != []:
        add_failure(failures, "process_isolation_failed")

    usage_wrapper = _mapping(derived.get("usage"))
    usage_value = usage_wrapper.get("value")
    raw_usage = usage_value if isinstance(usage_value, dict) else None
    duration_ms = _elapsed(summary.get("client_run_duration_ms"))
    if duration_ms is None:
        raise AdapterInputError("malformed_artifact")
    duration = duration_ms / 1000.0
    lifecycle_complete = bool(
        trace is not None
        and turn_response_seen
        and trace.turn_started_elapsed_ms is not None
        and trace.turn_completed
        and trace.final_answer is not None
        and not trace.has_incomplete_items
        and all(call.outcome in {"success", "mcp_error"} for call in calls)
    )
    requested_model_value = requested.get("model")
    requested_model = (
        requested_model_value
        if isinstance(requested_model_value, str)
        else expected.requested_model
    )
    try:
        return NormalizedRun(
            consumer="terra",
            candidate_sha=expected.candidate_sha,
            prompt_sha256=expected.prompt_sha256,
            summary_sha256=expected.summary_sha256,
            trace_sha256=expected.trace_sha256,
            trace_bytes=len(trace_raw),
            requested_model=requested_model,
            expected_model=expected.expected_model,
            observed_model=observed_model,
            requested_effort=requested.get("reasoning_effort")
            if isinstance(requested.get("reasoning_effort"), str)
            else None,
            observed_effort=observed_effort,
            effort_unavailable_reason=(
                "not_exposed_by_client"
                if requested.get("reasoning_effort") is None or observed_effort is None
                else None
            ),
            model_rerouted=rerouted or observed_model not in {None, expected.expected_model},
            transport_passed=not failures and capture_complete and lifecycle_complete,
            capture_complete=capture_complete,
            lifecycle_complete=lifecycle_complete,
            hard_call_limit=expected.hard_call_limit,
            deadline_seconds=expected.deadline_seconds,
            trace_limit_bytes=expected.trace_limit_bytes,
            duration_seconds=duration,
            calls=tuple(calls),
            final_answer=derived.get("final_answer")
            if isinstance(derived.get("final_answer"), str)
            else None,
            measurements=Measurements(returned_text_bytes=returned_bytes),
            failures=tuple(failures),
            limitations=tuple(limitations),
            client_version=preflight.get("client_version")
            if isinstance(preflight.get("client_version"), str)
            else None,
            raw_usage=raw_usage,
        )
    except (ValidationError, TypeError, ValueError, RecursionError) as exc:
        raise AdapterInputError("malformed_artifact") from exc


__all__ = ["normalize_codex_run"]
