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
        assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()
        assert result["derived"] is False
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


def test_pointer_base64_is_rejected_with_original_body_recovery():
    from clinpgx_link.content.reader import read_content
    from clinpgx_link.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError) as caught:
        read_content(
            b'{ "x" : "\\u00e9" }\r\n',
            media_type="application/json",
            pointer="/x",
            representation="base64",
        )
    assert caught.value.field == "pointer"
    assert caught.value.subtype == "base64_pointer_unsupported"
    assert "empty pointer" in (caught.value.hint or "").lower()
    assert "base64" in (caught.value.hint or "").lower()


@pytest.mark.parametrize(
    ("raw", "media_type", "pointer", "representation", "subtype"),
    [
        (b'{"items":["first"]}', "application/json", "bad", "structure", "pointer_syntax_invalid"),
        (
            b'{"items":["first"]}',
            "application/json",
            "/items/01",
            "structure",
            "array_index_invalid",
        ),
        (b'{"value":7}', "application/json", "/value", "text", "text_selection_required"),
        (b"plain text", "text/plain", "/value", "structure", "json_pointer_required"),
    ],
)
def test_content_selection_failures_have_closed_subtypes(
    raw, media_type, pointer, representation, subtype
):
    from clinpgx_link.content.reader import read_content
    from clinpgx_link.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError) as caught:
        read_content(
            raw,
            media_type=media_type,
            pointer=pointer,
            representation=representation,
        )

    assert caught.value.subtype == subtype


@pytest.mark.parametrize(
    (
        "raw",
        "expected_type",
        "expected_bytes",
        "expected_length",
        "expected_unit",
        "digest_representation",
    ),
    [
        (
            b'{"x":"\\u03b1"}',
            "string",
            "\N{GREEK SMALL LETTER ALPHA}".encode(),
            1,
            "characters",
            "utf8_decoded_string",
        ),
        (b'{"x":1.25}', "number", b"1.25", 4, "bytes", "canonical_json_scalar"),
        (b'{"x":true}', "boolean", b"true", 4, "bytes", "canonical_json_scalar"),
        (b'{"x":null}', "null", b"null", 4, "bytes", "canonical_json_scalar"),
    ],
)
def test_scalar_structure_describes_selected_digest_and_length(
    raw,
    expected_type,
    expected_bytes,
    expected_length,
    expected_unit,
    digest_representation,
):
    from clinpgx_link.content.reader import read_content

    result = read_content(raw, media_type="application/json", pointer="/x")

    assert result["type"] == expected_type
    assert result["length"] == expected_length
    assert result["unit"] == expected_unit
    assert result["digest_representation"] == digest_representation
    assert result["sha256"] == hashlib.sha256(expected_bytes).hexdigest()
    assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    ("encoded", "expected"),
    [
        ('"short evidence"', "short evidence"),
        ("17", 17),
        ("1.25", 1.25),
        ("true", True),
        ("null", None),
    ],
)
def test_short_selected_scalars_are_inlined_without_losing_descriptor_metadata(encoded, expected):
    from clinpgx_link.content.reader import read_content

    raw = f'{{"x":{encoded}}}'.encode()
    result = read_content(raw, media_type="application/json", pointer="/x")

    assert "value" in result
    assert result["value"] == expected
    assert result["sha256"]
    assert result["length"] >= 1


def test_scalar_inline_threshold_counts_utf8_bytes_and_never_inlines_containers():
    from clinpgx_link.content.reader import read_content

    at_limit = "é" * 128
    over_limit = "é" * 129
    raw = json.dumps(
        {
            "at_limit": at_limit,
            "over_limit": over_limit,
            "object": {"nested": "not recursively inlined"},
            "array": ["not recursively inlined"],
        },
        ensure_ascii=False,
    ).encode()

    result = read_content(raw, media_type="application/json", length=4)
    by_key = {item["key"]: item for item in result["items"]}

    assert by_key["at_limit"]["value"] == at_limit
    assert "value" not in by_key["over_limit"]
    assert "value" not in by_key["object"]
    assert "value" not in by_key["array"]

    selected_over_limit = read_content(raw, media_type="application/json", pointer="/over_limit")
    assert "value" not in selected_over_limit


def test_scalar_children_also_include_selected_digest_metadata():
    from clinpgx_link.content.reader import read_content

    raw = b'{"x":"\\u03b1"}'
    result = read_content(raw, media_type="application/json", length=1)

    assert result["items"] == [
        {
            "key": "x",
            "pointer": "/x",
            "type": "string",
            "length": 1,
            "unit": "characters",
            "digest_representation": "utf8_decoded_string",
            "sha256": hashlib.sha256("\N{GREEK SMALL LETTER ALPHA}".encode()).hexdigest(),
            "value": "\N{GREEK SMALL LETTER ALPHA}",
        }
    ]


def test_scalar_children_inline_numeric_boolean_and_null_as_typed_values():
    from clinpgx_link.content.reader import read_content

    result = read_content(
        b'{"number":7,"boolean":false,"nothing":null}',
        media_type="application/json",
        length=3,
    )
    by_key = {item["key"]: item for item in result["items"]}

    assert by_key["number"]["value"] == 7
    assert by_key["boolean"]["value"] is False
    assert "value" in by_key["nothing"]
    assert by_key["nothing"]["value"] is None


def test_structure_pointer_at_character_limit_is_advertised_and_traversable():
    from clinpgx_link.content.reader import read_content

    key = "k" * 4095
    raw = json.dumps({key: "reachable"}).encode()
    structure = read_content(raw, media_type="application/json", length=1)
    child_pointer = structure["items"][0]["pointer"]

    assert len(child_pointer) == 4096
    selected = read_content(
        raw, media_type="application/json", pointer=child_pointer, representation="text"
    )
    assert selected["text"] == "reachable"


def test_structure_rejects_child_pointer_over_character_limit_with_byte_recovery():
    from clinpgx_link.content.reader import read_content
    from clinpgx_link.exceptions import ResponseTooLargeError

    raw = json.dumps({"k" * 4096: "unreachable"}).encode()
    with pytest.raises(ResponseTooLargeError) as caught:
        read_content(raw, media_type="application/json", length=1)

    assert caught.value.field == "pointer"
    assert "empty pointer" in (caught.value.hint or "").lower()
    assert "base64" in (caught.value.hint or "").lower()


def test_structure_rejects_child_beyond_segment_limit_without_advertising_it():
    from clinpgx_link.content.reader import read_content
    from clinpgx_link.exceptions import ResponseTooLargeError

    value = {"leaf": "unreachable"}
    for _ in range(128):
        value = {"a": value}
    raw = json.dumps(value).encode()
    pointer = "/a" * 128

    with pytest.raises(ResponseTooLargeError) as caught:
        read_content(raw, media_type="application/json", pointer=pointer, length=1)
    assert caught.value.field == "pointer"
    assert "empty pointer" in (caught.value.hint or "").lower()


def test_structure_pages_honor_serialized_budget_with_progressing_continuations():
    from clinpgx_link.content.reader import read_content

    source = {f"{index:03d}-" + "x" * 300: index for index in range(100)}
    raw = json.dumps(source).encode()
    start = 0
    returned_keys = []
    page_sizes = []
    while True:
        result = read_content(
            raw,
            media_type="application/json",
            representation="structure",
            start=start,
            length=100,
        )
        serialized_size = len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        page_sizes.append(result["returned"])
        assert serialized_size <= 32 * 1024
        assert result["returned"] > 0
        returned_keys.extend(item["key"] for item in result["items"])
        if not result["has_more"]:
            assert result["next_start"] is None
            break
        assert result["next_start"] == start + result["returned"]
        start = result["next_start"]

    assert page_sizes[0] < 100
    assert returned_keys == list(source)


def test_oversized_first_structure_descriptor_has_exact_byte_recovery():
    from clinpgx_link.content.reader import read_content
    from clinpgx_link.exceptions import ResponseTooLargeError

    key = "😀" * 4095
    raw = json.dumps({key: True}).encode()
    with pytest.raises(ResponseTooLargeError) as caught:
        read_content(raw, media_type="application/json", length=1)

    assert caught.value.subtype == "response_too_large"
    assert "empty pointer" in (caught.value.hint or "").lower()
    start = 0
    parts = []
    while True:
        original = read_content(
            raw,
            media_type="application/json",
            representation="base64",
            start=start,
            length=8192,
        )
        parts.append(base64.b64decode(original["base64"], validate=True))
        if not original["has_more"]:
            break
        assert original["next_start"] > start
        start = original["next_start"]
    assert b"".join(parts) == raw


def test_pointer_descent_through_scalar_is_rejected():
    from clinpgx_link.content.reader import read_content
    from clinpgx_link.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError):
        read_content(b'{"x":"abc"}', media_type="application/json", pointer="/x/0")


def test_late_structure_page_does_not_materialize_all_child_descriptors():
    import tracemalloc

    from clinpgx_link.content.reader import read_content

    raw = b"[" + b"0," * 49999 + b"0]"
    tracemalloc.start()
    try:
        result = read_content(raw, media_type="application/json", start=49999, length=1)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["items"][0]["key"] == 49999
    assert result["returned"] == 1
    assert result["has_more"] is False
    assert peak < len(raw) * 20


def test_wide_json_validation_does_not_duplicate_all_child_references():
    import tracemalloc

    from clinpgx_link.content.reader import read_content

    raw = b"[" + b"0," * 499999 + b"0]"
    tracemalloc.start()
    try:
        result = read_content(raw, media_type="application/json", start=499999, length=1)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["items"][0]["key"] == 499999
    assert peak < 7_000_000
