"""Response-Envelope v1.2 provenance and the vendored schema pin.

Schema validation of every envelope the suite builds lives in ``tests/conftest.py``;
these tests pin the schema file and the provenance values of the envelope builder.
The schema is vendored from berntpopp/genefoundry-envelope, tag v1.2.0.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from clinpgx_link.exceptions import NotFoundError
from clinpgx_link.mcp.envelope import error_result, success_result
from clinpgx_link.models import SourceInfo

_CONFORMANCE = Path(__file__).parents[1] / "conformance"
_SCHEMA_FILE = _CONFORMANCE / "envelope-v1.2.schema.json"
_VALIDATOR = Draft202012Validator(json.loads(_SCHEMA_FILE.read_text(encoding="utf-8")))
_DIGEST = "a" * 64


def _source(label: str, data_source: str, **fields: Any) -> SourceInfo:
    return SourceInfo(
        label, "https://www.clinpgx.org/x", fields.pop("at"), _DIGEST, data_source, **fields
    )


def _envelope(source: SourceInfo) -> dict[str, Any]:
    payload = success_result({"id": "PA124"}, source=source, elapsed_ms=1.5).structured_content
    assert isinstance(payload, dict)
    return payload


def test_vendored_schema_is_the_pinned_release() -> None:
    pin = (_CONFORMANCE / "envelope-v1.2.schema.sha256").read_text(encoding="utf-8").strip()
    assert hashlib.sha256(_SCHEMA_FILE.read_bytes()).hexdigest() == pin


def test_snapshot_result_names_its_data_release_and_download_time() -> None:
    source = _source(
        "ClinPGx local snapshot",
        "download",
        at="2026-09-05T10:00:00+02:00",
        release_tag="data-2026-09-05",
    )
    payload = _envelope(source)
    meta = payload["_meta"]
    assert (meta["source"], meta["source_label"]) == ("clinpgx", "ClinPGx local snapshot")
    assert meta["data_version"] == meta["release_tag"] == "data-2026-09-05"
    assert meta["retrieved_at"] == "2026-09-05T08:00:00Z"
    assert list(_VALIDATOR.iter_errors(payload)) == []


@pytest.mark.parametrize(
    ("label", "data_source", "upstream"),
    [
        ("ClinPGx REST API", "api", "clinpgx"),
        ("ClinPGx website API", "website", "clinpgx"),
        ("ClinPGx REST API", "cache", "clinpgx"),  # a cache hit keeps the recorded fetch time
        ("CPIC API", "website", "cpic"),
    ],
)
def test_live_result_is_live_and_names_its_upstream(
    label: str, data_source: str, upstream: str
) -> None:
    payload = _envelope(_source(label, data_source, at="2026-11-20T08:00:00Z"))
    meta = payload["_meta"]
    assert (meta["source"], meta["data_version"]) == (upstream, "live")
    assert meta["retrieved_at"] == "2026-11-20T08:00:00Z"
    assert list(_VALIDATOR.iter_errors(payload)) == []


def test_server_generated_result_keeps_its_label_and_claims_no_release() -> None:
    meta = _envelope(_source("clinpgx-link", "server", at="2026-11-20T08:00:00+00:00"))["_meta"]
    assert meta["source"] == "clinpgx-link"
    assert "data_version" not in meta


def test_schema_rejects_a_result_without_provenance() -> None:
    payload = _envelope(_source("ClinPGx REST API", "api", at="2026-11-20T08:00:00Z"))
    del payload["_meta"]["data_version"]
    assert [error.message for error in _VALIDATOR.iter_errors(payload)] == [
        "'data_version' is a required property"
    ]


def test_error_envelope_validates_once_timed() -> None:
    payload = error_result(NotFoundError("missing")).structured_content
    assert isinstance(payload, dict)
    payload["_meta"]["elapsed_ms"] = 0.0  # the boundary middleware always sets the timer
    assert list(_VALIDATOR.iter_errors(payload)) == []
