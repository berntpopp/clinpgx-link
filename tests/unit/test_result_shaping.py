"""Opt-in, caller-controlled shaping over one retained live API row list (issue #2)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from clinpgx_link.api.client import ClinPGxClient
from clinpgx_link.config import Settings
from clinpgx_link.content.store import ContentStore
from clinpgx_link.mcp.facade import create_mcp
from clinpgx_link.services.api import ApiService
from tests.unit.test_repository import _repository

GOLDEN = Path(__file__).parents[1] / "fixtures" / "shaping"

CONNECTED = [
    {"connectedObject": {"id": "PA1", "name": "atorvastatin"}, "connectionTypes": ["Label"]},
    {
        "connectedObject": {"id": "PA2", "name": "simvastatin"},
        "connectionTypes": ["GuidelineAnnotation", "Label"],
    },
    {"connectedObject": {"id": "PA3", "name": "pravastatin"}, "connectionTypes": ["Label"]},
]
SUMMARY = [
    {"id": "1", "name": "a", "levelOfEvidence": {"term": "3"}, "score": 2.0, "types": ["Toxicity"]},
    {
        "id": "2",
        "name": "b",
        "levelOfEvidence": {"term": "1A"},
        "score": 3.75,
        "types": ["Efficacy"],
    },
    {"id": "3", "name": "c", "score": 1.0, "types": ["Efficacy", "Dosage"]},
    {"id": "4", "name": "d", "levelOfEvidence": {"term": "1A"}, "score": 2.0, "types": []},
]


def _handle(request: httpx.Request) -> httpx.Response:
    if request.url.path.startswith("/v1/report/connectedObjects/"):
        return httpx.Response(200, json=CONNECTED)
    return httpx.Response(200, json={"status": "success", "data": SUMMARY})


@pytest.fixture
async def client(tmp_path):
    store = ContentStore(tmp_path / "content.sqlite")
    upstream = ClinPGxClient(
        Settings(_env_file=None, cache_root=tmp_path),
        httpx.AsyncClient(transport=httpx.MockTransport(_handle)),
        store,
    )
    repository, _ = _repository(tmp_path)
    server = create_mcp(
        content_store=store, api_service=ApiService(upstream), repository=repository
    )
    async with Client(server) as connected:
        yield connected
    repository.close()
    await upstream.close()
    store.close()


RELATED = {"record_id": "PA124", "result_type": "relationship"}
PAIR = {"record_id": "PA124", "other_id": "PA449053", "result_type": "summary_annotation"}
SEARCH = {"entity_type": "summary_annotation", "filters": {"chemical": "PA449053"}}


async def _call(client, tool, args):
    return (await client.call_tool(tool, args, raise_on_error=False)).structured_content


def _data(row):
    return json.loads(row["data"]["text"])


def _normalized(payload):
    """Mask only per-call identity: request id, timing, fetch time and its content ref."""
    meta = payload["_meta"]
    stamp = meta["retrieved_at"]
    meta.update(request_id="<id>", elapsed_ms=0)
    text = json.dumps(payload).replace(stamp, "<retrieved_at>")
    return json.loads(re.sub(r"content:[0-9a-f]{64}", "<content_ref>", text))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "tool", "args"),
    [
        ("related", "get_related_records", RELATED),
        ("pair", "get_related_records", PAIR),
        ("search", "search_records", SEARCH),
    ],
)
async def test_no_shape_output_is_byte_identical_to_v0_1_8(client, name, tool, args):
    golden = GOLDEN / f"{name}.json"
    payload = _normalized(await _call(client, tool, args))
    rendered = json.dumps(payload, indent=1, sort_keys=True) + "\n"
    if os.environ.get("CLINPGX_WRITE_SHAPING_GOLDEN"):
        golden.write_text(rendered)
    assert rendered == golden.read_text()
    assert "shaping" not in payload["_meta"]


@pytest.mark.asyncio
async def test_filter_keeps_original_provenance_and_reports_counts(client):
    payload = await _call(
        client,
        "get_related_records",
        {**RELATED, "shape": {"filter": {"connectionTypes": ["GuidelineAnnotation"]}}},
    )
    (row,) = payload["results"]
    assert _data(row)["connectedObject"]["name"] == "simvastatin"
    assert row["row_index"] == 1
    assert row["source_pointer"]["text"] == "/1"
    shaping = payload["_meta"]["shaping"]
    assert shaping["applied"] == ["filter"]
    assert shaping["rows_before"] == 3
    assert shaping["rows_after"] == 1
    assert shaping["scope"] == "whole_retained_upstream_response"
    assert shaping["filter_matches"] == {"connectionTypes": 1}
    page = payload["_meta"]["pagination"]
    assert page["total"] == 1
    assert page["total_scope"] == "shaped_retained_response"


@pytest.mark.asyncio
async def test_sort_is_stable_missing_last_and_dict_values_compare_by_term(client):
    by_level = await _call(
        client, "get_related_records", {**PAIR, "shape": {"sort": "levelOfEvidence"}}
    )
    assert [r["id"] for r in by_level["results"]] == ["2", "4", "1", "3"]
    by_score = await _call(
        client, "search_records", {**SEARCH, "shape": {"sort": "score", "order": "desc"}}
    )
    assert [r["id"] for r in by_score["results"]] == ["2", "1", "4", "3"]
    assert by_score["_meta"]["shaping"]["applied"] == ["sort"]


@pytest.mark.asyncio
async def test_zero_match_filter_is_success_naming_the_field(client):
    payload = await _call(
        client,
        "get_related_records",
        {**PAIR, "shape": {"filter": {"types": ["Efficacy"], "significance": ["yes"]}}},
    )
    assert payload["success"] is True
    assert payload["results"] == []
    shaping = payload["_meta"]["shaping"]
    assert shaping["rows_before"] == 4 and shaping["rows_after"] == 0
    assert shaping["filter_matches"] == {"types": 2, "significance": 0}
    assert shaping["absent_fields"] == ["significance"]
    assert payload["_meta"]["pagination"]["total"] == 0


@pytest.mark.asyncio
async def test_filter_values_are_type_aware_and_case_insensitive(client):
    payload = await _call(
        client, "get_related_records", {**PAIR, "shape": {"filter": {"levelOfEvidence": ["1a"]}}}
    )
    assert [r["id"] for r in payload["results"]] == ["2", "4"]
    numeric = await _call(
        client, "get_related_records", {**PAIR, "shape": {"filter": {"score": [2]}}}
    )
    assert [r["id"] for r in numeric["results"]] == ["1", "4"]
    boolean = await _call(
        client, "get_related_records", {**PAIR, "shape": {"filter": {"score": [True]}}}
    )
    assert boolean["results"] == []


@pytest.mark.asyncio
async def test_fields_projects_top_level_keys_and_facet_counts_are_fenced(client):
    payload = await _call(
        client,
        "get_related_records",
        {**PAIR, "shape": {"fields": ["levelOfEvidence", "score"], "facet": "types"}},
    )
    assert [r["id"] for r in payload["results"]] == ["1", "2", "3", "4"]
    assert _data(payload["results"][2]) == {"score": 1.0}
    assert _data(payload["results"][1]) == {"levelOfEvidence": {"term": "1A"}, "score": 3.75}
    shaping = payload["_meta"]["shaping"]
    assert shaping["applied"] == ["fields", "facet"]
    facet = shaping["facets"]
    assert facet["field"] == "types"
    assert [(v["value"]["text"], v["count"]) for v in facet["values"]] == [
        ("Efficacy", 2),
        ("Dosage", 1),
        ("Toxicity", 1),
    ]
    assert facet["values"][0]["value"]["kind"] == "untrusted_text"
    assert facet["distinct_values"] == 3 and facet["truncated"] is False


@pytest.mark.asyncio
async def test_cursor_pages_the_shaped_order_and_is_bound_to_the_shape(client):
    shape = {"sort": "score", "order": "desc"}
    first = await _call(client, "get_related_records", {**PAIR, "limit": 2, "shape": shape})
    assert [r["id"] for r in first["results"]] == ["2", "1"]
    cursor = first["_meta"]["pagination"]["next_cursor"]
    second = await _call(
        client, "get_related_records", {**PAIR, "limit": 2, "shape": shape, "cursor": cursor}
    )
    assert [r["id"] for r in second["results"]] == ["4", "3"]
    for other in ({"sort": "score"}, None):
        args = {**PAIR, "cursor": cursor, **({"shape": other} if other else {})}
        rejected = await _call(client, "get_related_records", args)
        assert rejected["success"] is False
        assert rejected["field"] == "cursor"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shape",
    [
        {"sort": "evil/path"},
        {"filter": {"__proto__": ["x"]}},
        {"fields": ["notAKey"]},
        {"facet": "score", "extra": 1},
        {"filter": {"score": []}},
        {"filter": {"types": ["x"] * 21}},
        {"fields": ["id"] * 13},
        {"order": "sideways"},
    ],
)
async def test_unknown_or_oversized_shape_is_named_invalid_input(client, shape):
    payload = await _call(client, "get_related_records", {**PAIR, "shape": shape})
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_input"
    assert payload["field"] == "shape"
    assert payload["subtype"] == "shape_invalid"
    assert "ShapeKey" in payload["message"]


@pytest.mark.asyncio
async def test_shape_on_local_snapshot_is_rejected_not_silently_page_scoped(client):
    payload = await _call(
        client,
        "get_related_records",
        {
            "record_id": "PA124",
            "result_type": "allele",
            "source": "download",
            "shape": {"sort": "id"},
        },
    )
    assert payload["success"] is False
    assert payload["field"] == "shape"
    assert payload["subtype"] == "shape_requires_api_rows"
    assert "source='api'" in payload["message"]


@pytest.mark.asyncio
async def test_empty_shape_is_the_unshaped_call(client):
    await _call(client, "get_related_records", PAIR)  # warm the cache both calls then hit
    plain = _normalized(await _call(client, "get_related_records", PAIR))
    empty = _normalized(await _call(client, "get_related_records", {**PAIR, "shape": {}}))
    assert empty == plain
