"""Every envelope the suite builds is checked against the Response-Envelope v1.2 schema.

All tool results leave through ``clinpgx_link.mcp.envelope.wire_result``. The autouse
fixture below validates each envelope built there during a test against the vendored
schema (``tests/conformance/envelope-v1.2.schema.json``) and fails the test at teardown
when one does not conform.

Three things are left out, as the standard allows:

- results the server generates itself (capabilities, diagnostics, the vendored
  operation registry: ``_meta.data_source == "server"``) carry no upstream data;
- a test marked ``synthetic_envelope`` hands ``wire_result`` a payload it built itself;
- ``_meta.elapsed_ms`` is set by the boundary middleware after ``wire_result``, so an
  envelope built without the middleware is validated with the timer filled in.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from clinpgx_link.mcp import envelope

_SCHEMA = Path(__file__).parent / "conformance" / "envelope-v1.2.schema.json"
ENVELOPE_VALIDATOR = Draft202012Validator(json.loads(_SCHEMA.read_text(encoding="utf-8")))


def envelope_violations(payload: dict[str, Any]) -> list[str]:
    """Schema violations of one envelope, as the boundary middleware would emit it."""
    meta = payload.get("_meta")
    if not isinstance(meta, dict) or meta.get("data_source") == "server":
        return []
    timed = {**payload, "_meta": {**meta, "elapsed_ms": meta.get("elapsed_ms") or 0.0}}
    return [
        f"{'/'.join(map(str, error.absolute_path))}: {error.message}"
        for error in ENVELOPE_VALIDATOR.iter_errors(timed)
    ]


@pytest.fixture(autouse=True)
def _envelopes_conform_to_v1_2(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if "synthetic_envelope" in request.keywords:
        yield
        return
    violations: list[str] = []
    wire_result = envelope.wire_result

    def checked(payload: dict[str, Any], *, is_error: bool = False) -> Any:
        result = wire_result(payload, is_error=is_error)
        violations.extend(envelope_violations(payload))
        return result

    monkeypatch.setattr(envelope, "wire_result", checked)
    yield
    assert not violations, "envelope violates Response-Envelope v1.2:\n" + "\n".join(violations)
