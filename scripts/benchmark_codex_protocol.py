"""Fail-closed validation for Codex app-server benchmark turn traces."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

PUBLIC_ERROR_CODES = frozenset(
    {
        "invalid_input",
        "not_found",
        "ambiguous_query",
        "upstream_unavailable",
        "rate_limited",
        "internal",
    }
)
_BENIGN_ITEM_TYPES = frozenset({"userMessage", "agentMessage", "reasoning", "plan"})


def _reject_json_constant(_constant: str) -> None:
    raise ValueError


def canonical_sha256(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


class ProtocolTrace:
    """Incrementally validate one app-server thread/turn/item lifecycle."""

    def __init__(
        self,
        *,
        thread_id: str,
        advertised_tools: dict[str, dict[str, Any]],
        requested_model: str,
        requested_provider: str,
        requested_effort: str,
        observed_identity: dict[str, Any],
        max_calls: int,
    ) -> None:
        self.thread_id = thread_id
        self.advertised_tools = advertised_tools
        self.max_calls = max_calls
        self.failures: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self._calls_by_id: dict[str, dict[str, Any]] = {}
        self._started_items: dict[str, str] = {}
        self._completed_items: set[str] = set()
        self.errors: list[dict[str, str]] = []
        self.turn_id: str | None = None
        self.turn_started_elapsed_ms: int | None = None
        self.turn_completed_elapsed_ms: int | None = None
        self.turn_completed = False
        self.final_answer: str | None = None
        self.final_answer_elapsed_ms: int | None = None
        self.usage: object | None = None
        self.reroutes: list[object] = []
        self.observed_identity = dict(observed_identity)
        self._validate_identity(requested_model, requested_provider, requested_effort)

    def _add_failure(self, reason: str) -> None:
        if reason not in self.failures:
            self.failures.append(reason)

    @property
    def has_incomplete_items(self) -> bool:
        return set(self._started_items) != self._completed_items

    def _validate_identity(self, model: str, provider: str, effort: str) -> None:
        observed_model = self.observed_identity.get("model")
        if not isinstance(observed_model, str) or not observed_model:
            self._add_failure("model_identity_missing")
        elif observed_model != model:
            self._add_failure("model_identity_mismatch")
        observed_provider = self.observed_identity.get("modelProvider")
        if not isinstance(observed_provider, str) or not observed_provider:
            self._add_failure("model_provider_missing")
        elif observed_provider != provider:
            self._add_failure("model_provider_mismatch")
        observed_effort = self.observed_identity.get("reasoningEffort")
        if not isinstance(observed_effort, str) or not observed_effort:
            self._add_failure("reasoning_effort_missing")
        elif observed_effort != effort:
            self._add_failure("reasoning_effort_mismatch")
        if self.observed_identity.get("instructionSources") != []:
            self._add_failure("instruction_sources_present")

    def observe(self, message: dict[str, Any], elapsed_ms: int) -> None:
        method = message.get("method")
        if not isinstance(method, str):
            return
        if self.turn_completed and method in {
            "turn/started",
            "item/started",
            "item/completed",
            "turn/completed",
        }:
            self._add_failure("event_after_turn_completed")
        if "id" in message:
            self._add_failure("unexpected_server_request")
        if method == "model/rerouted":
            self.reroutes.append(message.get("params"))
            self._add_failure("model_rerouted")
        elif method == "turn/started":
            self._turn_started(message, elapsed_ms)
        elif method in {"item/started", "item/completed"}:
            self._item(method, message, elapsed_ms)
        elif method == "turn/completed":
            self._turn_completed(message, elapsed_ms)
        elif method == "thread/tokenUsage/updated":
            params = message.get("params")
            if isinstance(params, dict) and params.get("tokenUsage") is not None:
                self.usage = params["tokenUsage"]

    def _turn_started(self, message: dict[str, Any], elapsed_ms: int) -> None:
        params = message.get("params")
        if not isinstance(params, dict) or params.get("threadId") != self.thread_id:
            self._add_failure("thread_id_mismatch")
        turn = params.get("turn") if isinstance(params, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            self._add_failure("turn_started_missing_id")
            return
        if self.turn_started_elapsed_ms is not None:
            self._add_failure("duplicate_turn_start")
        if not isinstance(turn, dict) or turn.get("status") != "inProgress":
            self._add_failure("invalid_turn_start_status")
        if self.turn_id not in {None, turn_id}:
            self._add_failure("turn_id_mismatch")
        self.turn_id = turn_id
        self.turn_started_elapsed_ms = elapsed_ms

    def _validate_ids(self, params: dict[str, Any]) -> None:
        if params.get("threadId") != self.thread_id:
            self._add_failure("thread_id_mismatch")
        turn_id = params.get("turnId")
        if not isinstance(turn_id, str) or not turn_id or self.turn_id not in {None, turn_id}:
            self._add_failure("turn_id_mismatch")
        elif self.turn_id is None:
            self.turn_id = turn_id

    def _item(self, method: str, message: dict[str, Any], elapsed_ms: int) -> None:
        params = message.get("params")
        item = params.get("item") if isinstance(params, dict) else None
        if not isinstance(params, dict) or not isinstance(item, dict):
            self._add_failure("malformed_item_event")
            return
        if self.turn_started_elapsed_ms is None:
            self._add_failure("item_before_turn_start")
        self._validate_ids(params)
        item_type = item.get("type")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            self._add_failure(
                "mcp_item_missing_id" if item_type == "mcpToolCall" else "item_missing_id"
            )
            return
        if method == "item/started":
            if item_id in self._started_items:
                self._add_failure(
                    "duplicate_mcp_start" if item_type == "mcpToolCall" else "duplicate_item_start"
                )
                return
            self._started_items[item_id] = str(item_type)
        else:
            started_type = self._started_items.get(item_id)
            if started_type is None:
                self._add_failure("completed_without_start")
                return
            if item_id in self._completed_items:
                self._add_failure(
                    "duplicate_mcp_completion"
                    if item_type == "mcpToolCall"
                    else "duplicate_item_completion"
                )
                return
            if started_type != item_type:
                self._add_failure("item_identity_mismatch")
                return
            self._completed_items.add(item_id)
        if item_type == "mcpToolCall":
            self._mcp_item(method, item, elapsed_ms)
            return
        if item_type not in _BENIGN_ITEM_TYPES:
            self._add_failure("forbidden_tool_item")
            return
        if method == "item/completed" and item_type == "agentMessage":
            phase = item.get("phase")
            text = item.get("text")
            if phase == "final_answer" and isinstance(text, str) and text.strip():
                if any(call["completed_elapsed_ms"] is None for call in self.calls):
                    self._add_failure("final_before_calls_complete")
                    return
                if self.final_answer is not None:
                    self._add_failure("duplicate_final_answer")
                    return
                self.final_answer = text
                self.final_answer_elapsed_ms = elapsed_ms

    def _mcp_item(self, method: str, item: dict[str, Any], elapsed_ms: int) -> None:
        if self.final_answer is not None:
            self._add_failure("mcp_call_after_final")
        if item.get("server") != "clinpgx":
            self._add_failure("unexpected_mcp_server")
        item_id = item.get("id")
        tool = item.get("tool")
        arguments = item.get("arguments")
        assert isinstance(item_id, str)
        if not isinstance(tool, str) or tool not in self.advertised_tools:
            self._add_failure("unadvertised_mcp_tool")
        elif not self._valid_arguments(tool, arguments):
            self._add_failure("invalid_tool_arguments")
        if method == "item/started":
            if item_id in self._calls_by_id:
                self._add_failure("duplicate_mcp_start")
                return
            if item.get("status") != "inProgress":
                self._add_failure("invalid_mcp_start_status")
            call: dict[str, Any] = {
                "id": item_id,
                "tool": tool,
                "arguments": arguments,
                "started_elapsed_ms": elapsed_ms,
                "completed_elapsed_ms": None,
                "client_lifecycle_ms": None,
                "server_duration_ms": None,
                "boundary_elapsed_ms": None,
                "outcome": None,
            }
            self._calls_by_id[item_id] = call
            self.calls.append(call)
            if len(self.calls) > self.max_calls:
                self._add_failure("tool_call_limit")
            return

        matched_call = self._calls_by_id.get(item_id)
        if matched_call is None:
            self._add_failure("completed_without_start")
            return
        if matched_call["completed_elapsed_ms"] is not None:
            self._add_failure("duplicate_mcp_completion")
            return
        if matched_call["tool"] != tool or matched_call["arguments"] != arguments:
            self._add_failure("mcp_item_mismatch")
        matched_call["completed_elapsed_ms"] = elapsed_ms
        started_elapsed = matched_call["started_elapsed_ms"]
        if not isinstance(started_elapsed, int):
            self._add_failure("invalid_mcp_start_timing")
            return
        matched_call["client_lifecycle_ms"] = max(0, elapsed_ms - started_elapsed)
        duration = item.get("durationMs")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
            matched_call["server_duration_ms"] = duration
        if item.get("status") != "completed" or item.get("error") is not None:
            matched_call["outcome"] = "transport_failure"
            self._add_failure("failed_mcp_transport")
            return
        result = item.get("result")
        structured = self._mirrored_envelope(result)
        if not isinstance(structured, dict):
            return
        success = structured.get("success")
        if success is True:
            matched_call["outcome"] = "success"
        elif success is False:
            code = structured.get("error_code")
            if not isinstance(code, str) or code not in PUBLIC_ERROR_CODES:
                self._add_failure("invalid_mcp_error_code")
                return
            matched_call["outcome"] = "mcp_error"
            self.errors.append({"call_id": item_id, "code": code})
        else:
            self._add_failure("invalid_structured_mcp_result")
        meta = structured.get("_meta")
        boundary = meta.get("elapsed_ms") if isinstance(meta, dict) else None
        if (
            isinstance(meta, dict)
            and meta.get("timing_scope") == "tool_boundary"
            and isinstance(boundary, (int, float))
            and not isinstance(boundary, bool)
            and boundary >= 0
        ):
            matched_call["boundary_elapsed_ms"] = boundary

    def _mirrored_envelope(self, result: object) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            self._add_failure("missing_structured_mcp_result")
            return None
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            self._add_failure("missing_structured_mcp_result")
            return None
        content = result.get("content")
        if not isinstance(content, list) or len(content) != 1:
            self._add_failure("mcp_mirror_mismatch")
            return None
        text_block = content[0]
        if not isinstance(text_block, dict) or text_block.get("type") != "text":
            self._add_failure("mcp_mirror_mismatch")
            return None
        text = text_block.get("text")
        if not isinstance(text, str):
            self._add_failure("mcp_mirror_mismatch")
            return None
        try:
            mirrored = json.loads(text, parse_constant=_reject_json_constant)
        except (ValueError, TypeError, RecursionError):
            self._add_failure("mcp_mirror_mismatch")
            return None
        if mirrored != structured:
            self._add_failure("mcp_mirror_mismatch")
            return None
        return structured

    def _valid_arguments(self, tool: str, arguments: object) -> bool:
        if not isinstance(arguments, dict):
            return False
        try:
            Draft202012Validator(self.advertised_tools[tool]).validate(arguments)
        except (SchemaError, ValidationError, RecursionError):
            return False
        return True

    def _turn_completed(self, message: dict[str, Any], elapsed_ms: int) -> None:
        params = message.get("params")
        if not isinstance(params, dict) or params.get("threadId") != self.thread_id:
            self._add_failure("thread_id_mismatch")
        turn = params.get("turn") if isinstance(params, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or self.turn_id != turn_id:
            self._add_failure("turn_id_mismatch")
        if self.turn_started_elapsed_ms is None:
            self._add_failure("turn_completed_without_start")
        if not isinstance(turn, dict) or turn.get("status") != "completed":
            self._add_failure("turn_not_completed")
        self.turn_completed = True
        self.turn_completed_elapsed_ms = elapsed_ms


def assess_acceptance(
    trace: ProtocolTrace,
    *,
    termination: str,
    trace_complete: bool,
    exit_code: int | None = 0,
) -> dict[str, Any]:
    failures = list(trace.failures)

    def add(reason: str) -> None:
        if reason not in failures:
            failures.append(reason)

    if termination != "completed":
        add(termination)
    if not trace_complete:
        add("truncated_jsonl" if termination == "truncated_jsonl" else "trace_byte_limit")
    if exit_code not in {0, None}:
        add("nonzero_exit")
    if not trace.calls:
        add("missing_mcp_call")
    if any(call["completed_elapsed_ms"] is None for call in trace.calls):
        add("incomplete_mcp_call")
    if trace.has_incomplete_items:
        add("incomplete_item_lifecycle")
    if trace.final_answer is None:
        add("missing_final_answer")
    if not trace.turn_completed:
        add("missing_turn_completed")
    if trace.turn_started_elapsed_ms is None:
        add("missing_turn_started")
    client_duration = (
        trace.turn_completed_elapsed_ms - trace.turn_started_elapsed_ms
        if trace.turn_completed_elapsed_ms is not None and trace.turn_started_elapsed_ms is not None
        else None
    )
    reported_calls = [
        {
            "id": call["id"],
            "tool": call["tool"],
            "arguments": call["arguments"],
            "outcome": call["outcome"],
            "started_elapsed_ms": {
                "value": call["started_elapsed_ms"],
                "reason": None,
            },
            "completed_elapsed_ms": {
                "value": call["completed_elapsed_ms"],
                "reason": (
                    None
                    if call["completed_elapsed_ms"] is not None
                    else "mcp_item_completion_not_observed"
                ),
            },
            "client_lifecycle_ms": {
                "value": call["client_lifecycle_ms"],
                "reason": (
                    None
                    if call["client_lifecycle_ms"] is not None
                    else "complete_mcp_item_pair_not_observed"
                ),
            },
            "server_duration_ms": {
                "value": call["server_duration_ms"],
                "reason": (
                    None
                    if call["server_duration_ms"] is not None
                    else "app_server_tool_item_did_not_report_duration"
                ),
            },
            "boundary_elapsed_ms": {
                "value": call["boundary_elapsed_ms"],
                "reason": (
                    None
                    if call["boundary_elapsed_ms"] is not None
                    else "mcp_envelope_did_not_report_boundary_timing"
                ),
            },
        }
        for call in trace.calls
    ]
    return {
        "accepted": not failures,
        "acceptance_scope": "transport_and_trace_integrity_only",
        "failures": failures,
        "calls": reported_calls,
        "errors": trace.errors,
        "final_answer": trace.final_answer,
        "observed_client_identity": trace.observed_identity,
        "backend_identity": {"value": None, "reason": "not_exposed_by_app_server"},
        "usage": (
            {"value": trace.usage, "reason": None}
            if trace.usage is not None
            else {"value": None, "reason": "app_server_did_not_report_usage"}
        ),
        "cost_usd": {"value": None, "reason": "app_server_does_not_report_cost"},
        "scheduler_queue_ms": {
            "value": None,
            "reason": "app_server_does_not_report_scheduler_queue_time",
        },
        "client_turn_duration_ms": {
            "value": client_duration,
            "reason": None if client_duration is not None else "complete_turn_pair_not_observed",
        },
    }
