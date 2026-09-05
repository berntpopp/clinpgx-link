"""Bounded ClinPGx HTTP behavior against recorded source-shaped responses."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parents[1] / "fixtures/api"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


def _settings(tmp_path: Path, **overrides):
    from clinpgx_link.config import Settings

    return Settings(
        _env_file=None,
        cache_root=tmp_path / "cache",
        request_timeout_seconds=2,
        request_deadline_seconds=10,
        **overrides,
    )


def _content_store(tmp_path: Path):
    from clinpgx_link.content.store import ContentStore

    root = tmp_path / "content"
    root.mkdir(mode=0o700)
    return ContentStore(root / "content.sqlite", max_bytes=2_000_000, max_entries=50)


def _client(tmp_path: Path, handler, **setting_overrides):
    from clinpgx_link.api.client import ClinPGxClient, RequestScheduler

    clock = FakeClock()
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, follow_redirects=False)
    client = ClinPGxClient(
        _settings(tmp_path, **setting_overrides),
        http_client=http_client,
        content_store=_content_store(tmp_path),
        scheduler=RequestScheduler(2, clock=clock, sleep=clock.sleep),
    )
    return client, http_client, clock


@pytest.mark.asyncio
async def test_jsend_array_is_unwrapped_and_exact_body_is_retained(tmp_path):
    raw = (FIXTURES / "gene_query_min.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.clinpgx.org/v1/data/gene?symbol=CYP2D6&view=min"
        assert request.headers["accept"] == "application/json"
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    client, http_client, _ = _client(tmp_path, handler)
    result = await client.request("GET", "/data/gene", params={"symbol": "CYP2D6", "view": "min"})

    assert result.value[0]["id"] == "PA128"
    assert result.source.sha256 == hashlib.sha256(raw).hexdigest()
    assert result.source.data_source == "api"
    assert result.source.coverage == "unknown"
    stored = client.content_store.get(result.details["content_ref"])
    assert stored.raw == raw
    assert stored.source == result.source
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_jsonld_retains_context_id_and_all_source_fields(tmp_path):
    raw = (FIXTURES / "gene_jsonld.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/ld+json"
        return httpx.Response(200, content=raw, headers={"content-type": "application/ld+json"})

    client, http_client, _ = _client(tmp_path, handler)
    result = await client.request(
        "GET", "/data/gene/PA124", params={"view": "min"}, representation="jsonld"
    )

    expected = json.loads(raw)["data"]
    assert result.value == expected
    assert result.value["@id"] == "https://clinpgx.org/gene/PA124"
    assert result.value["@context"] == "https://api.clinpgx.org/jsonld/gene.jsonld"
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_raw_report_object_is_not_mistaken_for_jsend(tmp_path):
    raw = b'{"allGenes":{"count":25047},"lastTimeStatisticsCollected":"9/5/26"}'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    client, http_client, _ = _client(tmp_path, handler)
    result = await client.request("GET", "/report/stats")

    assert result.value == json.loads(raw)
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_numeric_text_and_204_are_distinct_valid_results(tmp_path):
    responses = [
        httpx.Response(200, content=b"7144344", headers={"content-type": "text/plain"}),
        httpx.Response(204, content=b""),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client, http_client, _ = _client(tmp_path, handler)
    found = await client.request("GET", "/report/literatureId/12345678", representation="text")
    absent = await client.request("GET", "/report/literatureId/99999999", representation="text")

    assert found.value == 7144344
    assert found.details["http_status"] == 200
    assert absent.value is None
    assert absent.details["http_status"] == 204
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_infobutton_posts_verified_form_and_preserves_html(tmp_path):
    raw = b"<!doctype html><html><body>ClinPGx source result</body></html>"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "https://api.clinpgx.org/v1/infobutton"
        assert (await request.aread()) == b"mainSearchCriteria.v.c=PA124"
        return httpx.Response(200, content=raw, headers={"content-type": "text/html"})

    client, http_client, _ = _client(tmp_path, handler)
    result = await client.request(
        "POST",
        "/infobutton",
        form={"mainSearchCriteria.v.c": "PA124"},
        representation="html",
    )

    assert result.value == raw.decode()
    stored = client.content_store.get(result.details["content_ref"])
    assert stored.raw == raw
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_empty_404_search_differs_from_missing_record(tmp_path):
    error = (FIXTURES / "error_unknown_gene.json").read_bytes()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=error, headers={"content-type": "application/json"})

    from clinpgx_link.exceptions import NotFoundError

    client, http_client, _ = _client(tmp_path, handler)
    empty = await client.request("GET", "/data/gene", params={"symbol": "NO_MATCH"})
    assert empty.value == []
    assert empty.details["http_status"] == 404
    with pytest.raises(NotFoundError) as caught:
        await client.request("GET", "/data/gene/PA999999")
    assert "PA999999" not in str(caught.value)
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_bad_success_body_and_terminal_upstream_status_are_typed(tmp_path):
    responses = [
        httpx.Response(200, content=b"{broken", headers={"content-type": "application/json"}),
        httpx.Response(503, content=b"secret upstream body"),
        httpx.Response(503, content=b"secret upstream body"),
        httpx.Response(503, content=b"secret upstream body"),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    from clinpgx_link.exceptions import DataValidationError, UpstreamUnavailableError

    client, http_client, _ = _client(tmp_path, handler)
    with pytest.raises(DataValidationError) as invalid:
        await client.request("GET", "/report/stats")
    assert "broken" not in str(invalid.value)
    with pytest.raises(UpstreamUnavailableError) as unavailable:
        await client.request("GET", "/report/stats")
    assert "secret" not in str(unavailable.value)
    assert not responses
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_data_route_rejects_success_without_jsend_envelope(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "PA124"})

    from clinpgx_link.exceptions import DataValidationError

    client, http_client, _ = _client(tmp_path, handler)
    with pytest.raises(DataValidationError):
        await client.request("GET", "/data/gene/PA124")
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        b'{"data":{"id":"PA124","id":"PA125"},"status":"success"}',
        b'{"data":{"value":NaN},"status":"success"}',
    ],
)
async def test_json_decoder_rejects_lossy_or_nonstandard_objects(tmp_path, raw):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    from clinpgx_link.exceptions import DataValidationError

    client, http_client, _ = _client(tmp_path, handler)
    with pytest.raises(DataValidationError):
        await client.request("GET", "/data/gene/PA124")
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_complete_body_cap_never_returns_partial_success(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"data":"' + b"x" * 200 + b'","status":"success"}',
            headers={"content-type": "application/json"},
        )

    from clinpgx_link.exceptions import ResponseTooLargeError

    client, http_client, _ = _client(tmp_path, handler, max_response_bytes=64)
    with pytest.raises(ResponseTooLargeError):
        await client.request("GET", "/data/gene", params={"symbol": "CYP2D6"})
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_redirect_must_remain_on_approved_origin(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/stolen"})

    from clinpgx_link.exceptions import UpstreamUnavailableError

    client, http_client, _ = _client(tmp_path, handler)
    with pytest.raises(UpstreamUnavailableError):
        await client.request("GET", "/report/stats")
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_initial_website_origin_must_be_separately_approved(tmp_path):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"status": "success", "data": {}})

    from clinpgx_link.exceptions import UpstreamUnavailableError

    client, http_client, _ = _client(
        tmp_path,
        handler,
        website_allowed_origins=("https://website.example",),
    )
    with pytest.raises(UpstreamUnavailableError):
        await client.request("GET", "/site/gene/PA124")
    assert calls == 0
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "https://api.clinpgx.org:443/v1/report/stats",
        "https://api.clinpgx.org:bad/v1/report/stats",
        "https://user@api.clinpgx.org/v1/report/stats",
        "https://api.clinpgx.org/admin",
    ],
)
async def test_redirect_rejects_noncanonical_or_outside_base_path(tmp_path, location):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": location})

    from clinpgx_link.exceptions import UpstreamUnavailableError

    client, http_client, _ = _client(tmp_path, handler)
    with pytest.raises(UpstreamUnavailableError):
        await client.request("GET", "/report/stats")
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_representation_requires_matching_success_media_type(tmp_path):
    responses = [
        httpx.Response(200, json={"data": {}, "status": "success"}),
        httpx.Response(200, content=b"not html", headers={"content-type": "text/plain"}),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    from clinpgx_link.exceptions import DataValidationError

    client, http_client, _ = _client(tmp_path, handler)
    with pytest.raises(DataValidationError):
        await client.request("GET", "/data/gene/PA124", representation="jsonld")
    with pytest.raises(DataValidationError):
        await client.request("GET", "/infobutton", representation="html")
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_success_cache_reuses_decoded_result_and_content_reference(tmp_path):
    raw = (FIXTURES / "gene_query_min.json").read_bytes()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    client, http_client, _ = _client(tmp_path, handler)
    first = await client.request("GET", "/data/gene", params={"symbol": "CYP2D6"})
    first.value[0]["symbol"] = "MUTATED"
    second = await client.request("GET", "/data/gene", params={"symbol": "CYP2D6"})

    assert calls == 1
    assert second.value[0]["symbol"] == "CYP2D6"
    assert second.source.data_source == "cache"
    assert second.details["content_ref"] == first.details["content_ref"]
    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_scheduler_spaces_concurrent_attempts_at_aggregate_rate():
    from clinpgx_link.api.client import RequestScheduler

    clock = FakeClock()
    scheduler = RequestScheduler(2, clock=clock, sleep=clock.sleep)
    dispatched = []

    async def worker() -> None:
        await scheduler.acquire()
        dispatched.append(clock.now)

    await asyncio.gather(*(worker() for _ in range(4)))

    assert dispatched == [0.0, 0.5, 1.0, 1.5]


@pytest.mark.parametrize("rate", [0, -1, 2.01])
def test_scheduler_cannot_be_configured_above_source_limit(rate):
    from clinpgx_link.api.client import RequestScheduler
    from clinpgx_link.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError):
        RequestScheduler(rate)
