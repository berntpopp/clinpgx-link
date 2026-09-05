"""Preflight tool names and keep argument failures inside fleet envelopes."""

from __future__ import annotations

import uuid
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError
from pydantic import ValidationError

from clinpgx_link.exceptions import (
    ClinPGxError,
    InvalidInputError,
    NotFoundError,
    UpstreamUnavailableError,
)
from clinpgx_link.mcp.envelope import REQUEST_ID, error_result


class BoundaryGuard(Middleware):
    """Unknown names never reach FastMCP's name-reflecting dispatch path."""

    def __init__(self, server: FastMCP, *, source_access_allowed: bool = True) -> None:
        self.server = server
        self.source_access_allowed = source_access_allowed
        self.validators: dict[str, Draft202012Validator] = {}

    @staticmethod
    def _validation_field(error: JSONSchemaValidationError, properties: dict[str, Any]) -> str:
        """Return only a developer-owned top-level property name."""
        path = iter(error.absolute_path)
        field = next(path, None)
        return field if isinstance(field, str) and field in properties else "arguments"

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        token = REQUEST_ID.set(REQUEST_ID.get() or str(uuid.uuid4()))
        try:
            tool = await self.server.get_tool(context.message.name)
            if tool is None:
                return error_result(NotFoundError("Tool unavailable."))
            if not self.source_access_allowed and tool.name not in {
                "get_server_capabilities",
                "get_diagnostics",
                "get_api_schema",
            }:
                return error_result(
                    UpstreamUnavailableError(
                        "Production snapshot is not ready.", subtype="snapshot_not_ready"
                    )
                )
            arguments = context.message.arguments
            if arguments is None:
                arguments = {}
            properties = tool.parameters.get("properties", {})
            if not isinstance(properties, dict):
                return error_result(InvalidInputError("Invalid tool schema.", field="arguments"))
            if not isinstance(arguments, dict) or set(arguments) - set(properties):
                return error_result(InvalidInputError("Unknown tool argument.", field="arguments"))
            validator = self.validators.setdefault(tool.name, Draft202012Validator(tool.parameters))
            validation_error = next(validator.iter_errors(arguments), None)
            if validation_error is not None:
                return error_result(
                    InvalidInputError(
                        "Invalid tool arguments.",
                        field=self._validation_field(validation_error, properties),
                    )
                )
            try:
                return await call_next(context)
            except (ValidationError, FastMCPValidationError):
                return error_result(InvalidInputError("Invalid tool arguments.", field="arguments"))
            except ClinPGxError as exc:
                return error_result(exc)
        finally:
            REQUEST_ID.reset(token)
