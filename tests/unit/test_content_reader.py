"""Source content must remain reconstructable beyond an MCP response budget."""

import base64
import hashlib
import json

import pytest


def test_large_scalar_chunks_reconstruct_without_gaps():
    from clinpgx_link.content.reader import read_content

    original = "αé\u0000" * 49_581
    raw = json.dumps({"evidence": original}, ensure_ascii=False).encode()
    start = 0
    chunks = []
    while True:
        result = read_content(
            raw,
            media_type="application/json",
            pointer="/evidence",
            representation="text",
            start=start,
            length=4096,
        )
        chunks.append(result["text"])
        assert result["returned"] > 0
        assert result["total"] == 148_743
        assert result["sha256"] == hashlib.sha256(original.encode()).hexdigest()
        if not result["has_more"]:
            assert result["next_start"] is None
            break
        assert result["next_start"] > start
        start = result["next_start"]
    assert "".join(chunks) == original


def test_byte_chunks_preserve_exact_source_encoding():
    from clinpgx_link.content.reader import read_content

    raw = b'{ "a" : "\\u00e9", "b": [1, 2] }\r\n'
    parts = []
    for start in range(0, len(raw), 5):
        result = read_content(
            raw, media_type="application/json", representation="base64", start=start, length=5
        )
        parts.append(base64.b64decode(result["base64"], validate=True))
        assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert b"".join(parts) == raw


def test_structure_discovers_escaped_pointers_without_embedding_values():
    from clinpgx_link.content.reader import read_content

    raw = json.dumps({"a/b~c": ["x" * 200_000, {"ok": True}], "other": None}).encode()
    result = read_content(raw, media_type="application/json", representation="structure", length=1)
    assert result["total"] == 2
    assert result["next_start"] == 1
    assert result["items"] == [
        {"key": "a/b~c", "pointer": "/a~1b~0c", "type": "array", "length": 2}
    ]
    nested = read_content(
        raw, media_type="application/json", pointer="/a~1b~0c/0", representation="structure"
    )
    assert nested["type"] == "string"
    assert nested["length"] == 200_000
    assert len(json.dumps(nested)) < 1000


@pytest.mark.parametrize("pointer", ["bad", "/a~2", "/a/01", "/a/-", "/a/9", "/missing"])
def test_invalid_pointer_is_an_actionable_error(pointer):
    from clinpgx_link.content.reader import read_content
    from clinpgx_link.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError):
        read_content(b'{"a": [1]}', media_type="application/json", pointer=pointer)


@pytest.mark.parametrize("start,length", [(-1, 2), (0, 0), (0, 8193), (True, 1), (0, False)])
def test_invalid_slice_cannot_loop_or_exceed_budget(start, length):
    from clinpgx_link.content.reader import read_content
    from clinpgx_link.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError):
        read_content(
            b"hello", media_type="text/plain", representation="text", start=start, length=length
        )


def test_end_slice_is_explicit_and_past_end_is_rejected():
    from clinpgx_link.content.reader import read_content
    from clinpgx_link.exceptions import InvalidInputError

    result = read_content(b"hello", media_type="text/plain", representation="text", start=5)
    assert result["text"] == ""
    assert result["returned"] == 0
    assert result["has_more"] is False
    assert result["next_start"] is None
    with pytest.raises(InvalidInputError):
        read_content(b"hello", media_type="text/plain", representation="text", start=6)


def test_binary_is_available_as_bytes_not_fake_text():
    from clinpgx_link.content.reader import read_content
    from clinpgx_link.exceptions import InvalidInputError

    raw = b"\x89PNG\r\n\x1a\n\xff\x00"
    result = read_content(raw, media_type="image/png", representation="base64")
    assert base64.b64decode(result["base64"]) == raw
    with pytest.raises(InvalidInputError):
        read_content(raw, media_type="image/png", representation="text")


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":"\\ud800"}']
)
def test_invalid_json_cannot_be_reinterpreted_as_valid_data(raw):
    from clinpgx_link.content.reader import read_content
    from clinpgx_link.exceptions import DataValidationError

    with pytest.raises(DataValidationError):
        read_content(raw, media_type="application/json", pointer="/x")
    original = read_content(raw, media_type="application/json", representation="base64")
    assert base64.b64decode(original["base64"]) == raw


def test_selected_string_bytes_are_explicitly_derived_and_lossless():
    from clinpgx_link.content.reader import read_content

    result = read_content(
        b'{"x":"\\u00e9"}',
        media_type="application/json",
        pointer="/x",
        representation="base64",
        start=1,
        length=1,
    )
    assert result["derived"] is True
    assert result["total"] == 2
    assert result["unit"] == "bytes"
    assert base64.b64decode(result["base64"]) == b"\xa9"


def test_pointer_descent_through_scalar_is_rejected():
    from clinpgx_link.content.reader import read_content
    from clinpgx_link.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError):
        read_content(b'{"x":"abc"}', media_type="application/json", pointer="/x/0")
