"""High-level API service mappings retain registry validation and provenance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from clinpgx_link.models import SourceInfo, SourceResponse


@dataclass
class FakeClient:
    calls: list[tuple[str, str, dict[str, Any], dict[str, Any] | None, str]]

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
        representation: str = "json",
    ) -> SourceResponse:
        self.calls.append((method, path, params or {}, form, representation))
        source = SourceInfo("fixture", "https://api.clinpgx.org", "now", "0" * 64, "api")
        return SourceResponse({"id": "PA124"}, source)


@pytest.mark.asyncio
async def test_call_binds_before_delegating_every_request_component():
    from clinpgx_link.services.api import ApiService

    client = FakeClient([])
    service = ApiService(client)  # type: ignore[arg-type]
    result = await service.call(
        "GET /data/gene/{id}",
        path_parameters={"id": "PA124"},
        query_parameters={"view": "max"},
        representation="jsonld",
    )
    assert result.value == {"id": "PA124"}
    assert client.calls == [("GET", "/data/gene/PA124", {"view": "max"}, None, "jsonld")]


@pytest.mark.asyncio
async def test_search_and_get_use_closed_entity_mappings():
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.services.api import ApiService

    client = FakeClient([])
    service = ApiService(client)  # type: ignore[arg-type]
    await service.search("gene", {"symbol": "CYP2D6"}, view="base")
    await service.get("summary_annotation", "1448100508", view="max")
    assert client.calls == [
        ("GET", "/data/gene", {"symbol": "CYP2D6", "view": "base"}, None, "json"),
        ("GET", "/data/summaryAnnotation/1448100508", {"view": "max"}, None, "json"),
    ]
    with pytest.raises(InvalidInputError):
        await service.search("banana", {"name": "secret"})


@pytest.mark.asyncio
async def test_broken_vip_api_get_has_typed_working_fallback_guidance():
    from clinpgx_link.exceptions import UpstreamUnavailableError
    from clinpgx_link.services.api import ApiService

    service = ApiService(FakeClient([]))  # type: ignore[arg-type]
    with pytest.raises(UpstreamUnavailableError) as caught:
        await service.get("vip", "PA166170264")
    assert caught.value.subtype == "documented_broken_operation"
    assert caught.value.hint is not None
    assert "GET /site/vip/{id}" in caught.value.hint
    assert "PA166170264" not in str(caught.value)


@pytest.mark.asyncio
async def test_service_rejects_filter_view_collision():
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.services.api import ApiService

    service = ApiService(FakeClient([]))  # type: ignore[arg-type]
    with pytest.raises(InvalidInputError):
        await service.search("gene", {"symbol": "CYP2D6", "view": "min"}, view="base")
