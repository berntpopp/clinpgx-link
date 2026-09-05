"""Allowlisted ClinPGx website and CPIC reference-operation binding."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, cast

from clinpgx_link.exceptions import InvalidInputError
from clinpgx_link.models import BoundRequest

_REGISTRY_PATH = Path(__file__).with_name("website_operations.json")
_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9._~-]{1,128}\Z")
_PA_ID = re.compile(r"PA[0-9]{1,32}\Z")
_NUMERIC_ID = re.compile(r"[0-9]{1,32}\Z")
_CPIC_FILTER = re.compile(
    r"(?:eq|neq|gt|gte|lt|lte|like|ilike|is|in|cs|cd|ov|sl|sr|nxl|nxr|adj)\..{1,4000}\Z"
)


def _invalid(field: str, hint: str, *, subtype: str | None = None) -> InvalidInputError:
    return InvalidInputError(
        "Unsupported website request.", field=field, hint=hint, subtype=subtype
    )


def _load_registry(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload["operations"]
        if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
            raise ValueError("operations must be objects")
        ids = [item["id"] for item in entries]
        if len(ids) != 76 or len(ids) != len(set(ids)):
            raise ValueError("operation inventory mismatch")
        source_counts = {
            source: sum(item["source"] == source for item in entries)
            for source in ("clinpgx_website", "cpic")
        }
        if source_counts != {"clinpgx_website": 36, "cpic": 40}:
            raise ValueError("source inventory mismatch")
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise RuntimeError("Vendored website operation registry is invalid.") from exc
    return cast(list[dict[str, Any]], entries)


def _safe_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise _invalid(field, "Use a nonempty bounded string value.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _invalid(field, "Control characters are not supported.")
    return value


def _path_value(name: str, value: Any, contract: dict[str, Any]) -> str:
    rendered = _safe_text(value, "path_parameters")
    pattern = contract.get("pattern")
    if pattern and re.fullmatch(pattern, rendered) is None:
        raise _invalid("path_parameters", "Use an identifier matching the captured route.")
    if name == "literatureId" and _NUMERIC_ID.fullmatch(rendered) is None:
        raise _invalid("path_parameters", "Use the verified numeric literature identifier.")
    if name in {"id", "geneId"} and pattern is None and _PA_ID.fullmatch(rendered) is None:
        raise _invalid("path_parameters", "Use a ClinPGx PA identifier.")
    if rendered in {".", ".."} or _SAFE_SEGMENT.fullmatch(rendered) is None:
        raise _invalid("path_parameters", "Use a bounded identifier without URL syntax.")
    return rendered


def _bounded_integer(value: Any, field: str, *, minimum: int, maximum: int) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _invalid(field, "Use a bounded integer.")
    rendered = str(value)
    if not rendered.isascii() or not rendered.isdecimal() or len(rendered) > 10:
        raise _invalid(field, "Use a bounded integer.")
    parsed = int(rendered)
    if not minimum <= parsed <= maximum:
        raise _invalid(field, "Use a bounded integer.")
    return str(parsed)


class WebsiteRegistry:
    """Discover all evidence and bind only verified read-only reference routes."""

    def __init__(self, registry_path: Path = _REGISTRY_PATH) -> None:
        entries = _load_registry(registry_path)
        self._operations = {entry["id"]: entry for entry in entries}

    def list_operations(self) -> list[dict[str, Any]]:
        return copy.deepcopy(list(self._operations.values()))

    def describe(self, operation: str) -> dict[str, Any]:
        entry = self._operations.get(operation)
        if entry is None:
            raise _invalid("operation", "Choose an operation returned by get_website_schema.")
        return copy.deepcopy(entry)

    def bind(
        self,
        operation: str,
        path_parameters: dict[str, Any],
        query_parameters: dict[str, Any],
    ) -> BoundRequest:
        entry = self._operations.get(operation)
        if entry is None:
            raise _invalid("operation", "Choose an operation returned by get_website_schema.")
        if not entry["callable"]:
            raise _invalid(
                "operation",
                "This captured entry is inventory-only because a reference-read contract was not verified.",
                subtype="operation_unavailable",
            )
        if entry["source"] == "cpic":
            return self._bind_cpic(entry, path_parameters, query_parameters)
        return self._bind_site(entry, path_parameters, query_parameters)

    @staticmethod
    def _bind_site(
        entry: dict[str, Any],
        path_parameters: dict[str, Any],
        query_parameters: dict[str, Any],
    ) -> BoundRequest:
        contract = entry.get("contract", {})
        path_contract = contract.get("path", {})
        query_contract = contract.get("query", {})
        if set(path_parameters) != set(path_contract):
            raise _invalid("path_parameters", "Supply exactly the captured path parameters.")
        if set(query_parameters) - set(query_contract):
            raise _invalid("query_parameters", "Use only captured query parameters.")
        path = entry["path"]
        for name, parameter in path_contract.items():
            path = path.replace(
                "{" + name + "}", _path_value(name, path_parameters[name], parameter)
            )
        params: dict[str, Any] = {}
        for name, value in query_parameters.items():
            rendered = _safe_text(value, "query_parameters")
            allowed = query_contract[name].get("enum")
            if allowed is None and entry["id"] == "GET /site/guideline/{id}" and name == "view":
                allowed = ["base"]
            if allowed is not None and rendered not in allowed:
                raise _invalid("query_parameters", "Use a value observed for this route.")
            params[name] = rendered
        representation = (
            "text"
            if entry["decoder"]
            in {
                "json_body_even_if_text_plain",
                "tsv_attachment",
            }
            else "json"
        )
        return BoundRequest("GET", path, params, None, representation)

    @staticmethod
    def _bind_cpic(
        entry: dict[str, Any],
        path_parameters: dict[str, Any],
        query_parameters: dict[str, Any],
    ) -> BoundRequest:
        if path_parameters:
            raise _invalid("path_parameters", "This CPIC reference route has no path parameters.")
        allowed = set(entry["query_parameters"])
        if set(query_parameters) - allowed:
            raise _invalid(
                "query_parameters",
                "Use only captured row filters and bounded pagination; projections are unavailable.",
            )
        params: dict[str, Any] = {}
        for name, value in query_parameters.items():
            if name == "limit":
                params[name] = _bounded_integer(value, "query_parameters", minimum=1, maximum=100)
            elif name == "offset":
                params[name] = _bounded_integer(
                    value, "query_parameters", minimum=0, maximum=1_000_000
                )
            else:
                rendered = _safe_text(value, "query_parameters")
                if _CPIC_FILTER.fullmatch(rendered) is None:
                    raise _invalid(
                        "query_parameters",
                        "Use an explicit PostgREST comparison operator such as eq.value.",
                    )
                params[name] = rendered
        params.setdefault("limit", "20")
        params.setdefault("offset", "0")
        return BoundRequest("GET", entry["path"], params)


__all__ = ["WebsiteRegistry"]
