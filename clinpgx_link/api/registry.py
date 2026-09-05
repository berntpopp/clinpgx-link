"""Allowlisted ClinPGx REST operation discovery and argument binding."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, cast

from clinpgx_link.exceptions import InvalidInputError
from clinpgx_link.models import BoundRequest

_OPERATIONS_PATH = Path(__file__).with_name("operations.json")
_SAFE_PATH_SEGMENT = re.compile(r"[A-Za-z0-9._~-]{1,128}\Z")
_INFOBUTTON_FIELDS = frozenset(
    {
        "mainSearchCriteria.v.c",
        "mainSearchCriteria.v.cs",
        "mainSearchCriteria.v.dn",
        "mainSearchCriteria.v.ot",
    }
)
_SEARCH_OPERATIONS = frozenset(
    {
        "GET /data/pathway",
        "GET /data/gene",
        "GET /data/chemical",
        "GET /data/disease",
        "GET /data/variant/",
        "GET /data/literature",
        "GET /data/guidelineAnnotation",
        "GET /data/label",
        "GET /data/summaryAnnotation",
        "GET /data/variantAnnotation",
        "GET /data/ontologyTerm",
        "GET /data/dataAnnotation",
        "GET /data/connection",
    }
)


def _invalid(field: str, hint: str, *, subtype: str | None = None) -> InvalidInputError:
    return InvalidInputError(
        "Unsupported API request.", field=field, hint=hint, subtype=subtype
    )


def _load_operations(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        operations = payload["operations"]
        if not isinstance(operations, list) or not all(
            isinstance(entry, dict) for entry in operations
        ):
            raise ValueError("operations must be objects")
        if payload["operation_count"] != len(operations):
            raise ValueError("operation count mismatch")
        identities = [entry["operation"] for entry in operations]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate operation identity")
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise RuntimeError("Vendored ClinPGx operation registry is invalid.") from exc
    return cast(list[dict[str, Any]], operations)


def _parameter_maps(operation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    path_parameters: dict[str, Any] = {}
    query_parameters: dict[str, Any] = {}
    for parameter in operation["parameters"]:
        location = parameter["in"]
        if location == "path":
            path_parameters[parameter["name"]] = parameter
        elif location == "query":
            query_parameters[parameter["name"]] = parameter
    return path_parameters, query_parameters


def _validate_keys(
    supplied: dict[str, Any], expected: dict[str, Any] | frozenset[str], field: str
) -> None:
    expected_keys = set(expected)
    if set(supplied) - expected_keys:
        raise _invalid(field, "Use only fields declared by the selected operation.")


def _validate_string(value: Any, field: str, parameter: dict[str, Any]) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise _invalid(field, "Use a nonempty bounded string value.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _invalid(field, "Control characters are not supported.")
    allowed = parameter.get("schema", {}).get("enum")
    if allowed is not None and value not in allowed:
        raise _invalid(field, "Choose a value from the operation's declared enumeration.")
    return value


def _validate_path_value(value: Any, parameter: dict[str, Any]) -> str:
    schema_type = parameter.get("schema", {}).get("type")
    if schema_type == "number":
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise _invalid("path_parameters", "Use the declared numeric identifier.")
        rendered = str(value)
        if not rendered.isascii() or not rendered.isdecimal() or len(rendered) > 32:
            raise _invalid("path_parameters", "Use the declared numeric identifier.")
        return rendered
    rendered = _validate_string(value, "path_parameters", parameter)
    if rendered in {".", ".."} or _SAFE_PATH_SEGMENT.fullmatch(rendered) is None:
        raise _invalid("path_parameters", "Use a bounded identifier without URL syntax.")
    return rendered


def _representations(operation: str) -> frozenset[str]:
    if operation.startswith("GET /data/"):
        return frozenset({"json", "jsonld"})
    if operation == "GET /report/literatureId/{pmid}":
        return frozenset({"text"})
    if operation in {"GET /infobutton", "POST /infobutton"}:
        return frozenset({"html"})
    return frozenset({"json"})


class ApiRegistry:
    """Bind only operations and parameters captured in the vendored OpenAPI."""

    def __init__(self, operations_path: Path = _OPERATIONS_PATH) -> None:
        operations = _load_operations(operations_path)
        self._operations = {entry["operation"]: entry for entry in operations}

    def list_operations(self) -> list[dict[str, Any]]:
        """Return defensive operation descriptions in captured order."""
        return copy.deepcopy(list(self._operations.values()))

    def describe(self, operation: str) -> dict[str, Any]:
        """Describe one operation without exposing mutable registry state."""
        entry = self._operations.get(operation)
        if entry is None:
            raise _invalid("operation", "Choose an operation returned by get_api_schema.")
        result = copy.deepcopy(entry)
        result["representations"] = sorted(_representations(operation))
        if operation in _SEARCH_OPERATIONS or operation in {"GET /infobutton", "POST /infobutton"}:
            result["runtime_required_criteria"] = True
        return result

    def bind(
        self,
        operation: str,
        path_parameters: dict[str, Any],
        query_parameters: dict[str, Any],
        *,
        form_parameters: dict[str, Any] | None = None,
        representation: str = "json",
    ) -> BoundRequest:
        """Validate one complete call and return separated safe request components."""
        entry = self._operations.get(operation)
        if entry is None:
            raise _invalid("operation", "Choose an operation returned by get_api_schema.")
        if representation not in _representations(operation):
            raise _invalid(
                "representation", "Choose a representation verified for this operation."
            )
        path_contract, query_contract = _parameter_maps(entry)
        _validate_keys(path_parameters, path_contract, "path_parameters")
        _validate_keys(query_parameters, query_contract, "query_parameters")
        missing_path = set(path_contract) - set(path_parameters)
        if missing_path:
            raise _invalid("path_parameters", "Supply every required path parameter.")

        rendered_path = entry["path"]
        for name, parameter in path_contract.items():
            value = _validate_path_value(path_parameters[name], parameter)
            rendered_path = rendered_path.replace("{" + name + "}", value)

        params: dict[str, Any] = {}
        for name, parameter in query_contract.items():
            if parameter.get("required") and name not in query_parameters:
                raise _invalid("query_parameters", "Supply every required query parameter.")
            if name in query_parameters:
                params[name] = _validate_string(
                    query_parameters[name], "query_parameters", parameter
                )

        supplied_form = form_parameters or {}
        if operation == "POST /infobutton":
            if params:
                raise _invalid("query_parameters", "Infobutton POST accepts form values only.")
            _validate_keys(supplied_form, _INFOBUTTON_FIELDS, "form_parameters")
            form = {
                name: _validate_string(value, "form_parameters", {})
                for name, value in supplied_form.items()
            }
            if not form:
                raise _invalid(
                    "form_parameters",
                    "Supply at least one verified Infobutton criterion.",
                    subtype="missing_criteria",
                )
        else:
            if supplied_form:
                raise _invalid("form_parameters", "This operation does not accept form values.")
            form = None

        criteria = {key: value for key, value in params.items() if key != "view"}
        if operation in _SEARCH_OPERATIONS and not criteria:
            raise _invalid(
                "query_parameters",
                "Supply at least one documented search criterion.",
                subtype="missing_criteria",
            )
        if operation == "GET /infobutton" and not criteria:
            raise _invalid(
                "query_parameters",
                "Supply at least one verified Infobutton criterion.",
                subtype="missing_criteria",
            )
        return BoundRequest(entry["method"], rendered_path, params, form, representation)


__all__ = ["ApiRegistry"]
