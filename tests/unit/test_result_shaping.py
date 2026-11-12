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
