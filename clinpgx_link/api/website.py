"""Adapters for captured ClinPGx website and CPIC reference-data routes."""

from __future__ import annotations

import json
from typing import Any

from clinpgx_link.api.client import ClinPGxClient
from clinpgx_link.api.website_operations import WebsiteRegistry
from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.models import SourceResponse

_CLINPGX_LICENSE = {
    "name": "Creative Commons Attribution-ShareAlike 4.0 International",
    "spdx": "CC-BY-SA-4.0",
    "url": "https://www.clinpgx.org/page/dataUsagePolicy",
}
_CPIC_LICENSE = {
    "name": "Creative Commons CC0 1.0 Universal",
    "spdx": "CC0-1.0",
    "url": "https://github.com/cpicpgx/cpic-data/blob/main/LICENSE.md",
}


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode_text_json(value: Any) -> Any:
    if not isinstance(value, str):
        raise DataValidationError("Website source returned an invalid JSON representation.")
    try:
        decoded = json.loads(
            value,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DataValidationError("Website source returned invalid JSON.") from exc
    if isinstance(decoded, dict) and set(decoded) >= {"status", "data"}:
        if decoded["status"] != "success":
            raise DataValidationError("Website source returned a failure envelope.")
        return decoded["data"]
    return decoded


class WebsiteClient:
    """Invoke one validated website/CPIC operation with source-specific provenance."""

    def __init__(
        self,
        client: ClinPGxClient,
        registry: WebsiteRegistry | None = None,
    ) -> None:
        self._client = client
        self.registry = registry or WebsiteRegistry()

    def list_operations(self) -> list[dict[str, Any]]:
        return self.registry.list_operations()

    def describe(self, operation: str) -> dict[str, Any]:
        return self.registry.describe(operation)

    async def call(
        self,
        operation: str,
        path_parameters: dict[str, Any] | None = None,
        query_parameters: dict[str, Any] | None = None,
    ) -> SourceResponse:
        description = self.registry.describe(operation)
        request = self.registry.bind(operation, path_parameters or {}, query_parameters or {})
        if description["source"] == "cpic":
            response = await self._client.request_cpic(request.path, params=request.params)
            response.details["license"] = dict(_CPIC_LICENSE)
        else:
            response = await self._client.request(
                request.method,
                request.path,
                params=request.params,
                representation=request.representation,
            )
            decoder = description["decoder"]
            if decoder == "json_body_even_if_text_plain":
                response.value = _decode_text_json(response.value)
            elif decoder == "json_envelope":
                stored = self._client.content_store.get(response.details["content_ref"])
                decoded = _decode_text_json(stored.raw.decode("utf-8"))
                # _decode_text_json unwraps valid envelopes; check source shape independently.
                try:
                    source_shape = json.loads(stored.raw)
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise DataValidationError("Website source returned invalid JSON.") from exc
                if not isinstance(source_shape, dict) or not set(source_shape) >= {
                    "status",
                    "data",
                }:
                    raise DataValidationError(
                        "Website source response omitted its expected envelope."
                    )
                response.value = decoded
            response.details["license"] = dict(_CLINPGX_LICENSE)
        response.details["operation"] = operation
        return response

    async def close(self) -> None:
        await self._client.close()


__all__ = ["WebsiteClient"]
