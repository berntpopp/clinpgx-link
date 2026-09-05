"""Cursor integrity, selector binding and expiration are observable contracts."""

import pytest

from clinpgx_link.exceptions import InvalidInputError


def test_cursor_preserves_position_identity_and_ignores_presentation():
    from clinpgx_link.mcp.pagination import CursorCodec

    codec = CursorCodec(clock=lambda: 100.0)
    selectors = {"operation": "GET /data/gene", "filters": {"symbol": "CYP2C19"}}
    token = codec.encode(selectors, identity="content:" + "a" * 64, offset=20)
    page = codec.decode(token, selectors)
    assert page.offset == 20
    assert page.identity == "content:" + "a" * 64
    assert "CYP2C19" not in token
    reordered = {"filters": {"symbol": "CYP2C19"}, "operation": "GET /data/gene"}
    assert codec.decode(token, reordered) == page


@pytest.mark.parametrize("changed", [{"pointer": "/other"}, {"representation": "jsonld"}])
def test_cursor_cannot_be_reused_for_changed_selector(changed):
    from clinpgx_link.mcp.pagination import CursorCodec

    codec = CursorCodec()
    selectors = {"pointer": "/data", "representation": "json"}
    token = codec.encode(selectors, identity="snapshot-1", offset=1)
    with pytest.raises(InvalidInputError, match="cursor"):
        codec.decode(token, {**selectors, **changed})


def test_cursor_rejects_tampering_other_process_and_noncanonical_encoding():
    from clinpgx_link.mcp.pagination import CursorCodec

    codec = CursorCodec()
    token = codec.encode({}, identity="snapshot-1", offset=1)
    for bad in (token + "=", "!" + token[1:], token[:-8] + "abcdefgh", "x" * 8193):
        with pytest.raises(InvalidInputError):
            codec.decode(bad, {})
    with pytest.raises(InvalidInputError):
        CursorCodec().decode(token, {})


def test_cursor_expiry_and_mutually_exclusive_offset():
    from clinpgx_link.mcp.pagination import CursorCodec

    now = [100.0]
    codec = CursorCodec(clock=lambda: now[0], ttl_seconds=10)
    token = codec.encode({}, identity="snapshot-1", offset=1)
    with pytest.raises(InvalidInputError):
        codec.decode(token, {}, offset=1)
    now[0] = 110.0
    with pytest.raises(InvalidInputError) as caught:
        codec.decode(token, {})
    assert caught.value.subtype == "cursor_expired"


@pytest.mark.parametrize("offset", [-1, True, 1.5, 10**100])
def test_cursor_refuses_invalid_positions(offset):
    from clinpgx_link.mcp.pagination import CursorCodec

    with pytest.raises(InvalidInputError):
        CursorCodec().encode({}, identity="snapshot-1", offset=offset)


@pytest.mark.parametrize("deadline", [True, float("nan"), float("inf"), 10**1000])
def test_cursor_rejects_invalid_retention_deadlines(deadline):
    from clinpgx_link.mcp.pagination import CursorCodec

    with pytest.raises(InvalidInputError):
        CursorCodec().encode({}, identity="snapshot-1", offset=1, expires_at=deadline)
