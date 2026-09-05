"""Bounded, source-agnostic scalar selection contracts."""

from dataclasses import FrozenInstanceError

import pytest


def test_validate_pointers_preserves_order_and_none_means_no_selection():
    from clinpgx_link.mcp.selection import validate_pointers

    assert validate_pointers(None) is None
    assert validate_pointers(["/second", "/first", ""]) == ("/second", "/first", "")


@pytest.mark.parametrize(
    "pointers",
    [
        [],
        ["/same", "/same"],
        [f"/{index}" for index in range(13)],
        ["x"],
        ["/bad~"],
        ["/bad~2"],
        ["/" + "x" * 4096],
        ["/x" * 129],
        ["/\ud800"],
        ["/ok", 1],
    ],
)
def test_validate_pointers_rejects_empty_duplicate_unbounded_or_malformed_lists(pointers):
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.mcp.selection import validate_pointers

    with pytest.raises(InvalidInputError) as caught:
        validate_pointers(pointers)

    assert caught.value.field == "pointers"
    assert "\ud800" not in str(caught.value)


def test_validate_pointers_checks_trailing_syntax_after_a_missing_ancestor():
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.mcp.selection import validate_pointers

    with pytest.raises(InvalidInputError) as caught:
        validate_pointers(["/missing/still~2malformed"])

    assert "RFC 6901" in (caught.value.hint or "")


@pytest.mark.parametrize(
    "incompatible",
    [
        {"pointer": "/legacy"},
        {"cursor": "opaque"},
        {"offset": 1},
        {"include_fields": ["Symbol"]},
    ],
)
def test_validate_pointers_rejects_every_incompatible_selection_option(incompatible):
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.mcp.selection import validate_pointers

    with pytest.raises(InvalidInputError) as caught:
        validate_pointers(["/id"], **incompatible)

    assert caught.value.field == "pointers"


def test_resolve_scalars_returns_ordered_values_absence_null_and_escaped_keys():
    from clinpgx_link.mcp.selection import resolve_scalars

    selected = resolve_scalars(
        {
            "second": 2,
            "first": "one",
            "stored_null": None,
            "a/b~c": True,
            "\ud800": {"unselected": float("nan")},
        },
        ("/first", "/missing", "/stored_null", "/second", "/a~1b~0c"),
    )

    assert tuple((entry.pointer, entry.status) for entry in selected) == (
        ("/first", "value"),
        ("/missing", "absent"),
        ("/stored_null", "value"),
        ("/second", "value"),
        ("/a~1b~0c", "value"),
    )
    assert selected[0].value == "one"
    assert selected[2].value is None
    assert selected[3].value == 2
    assert type(selected[3].value) is int
    assert selected[4].value is True
    with pytest.raises(FrozenInstanceError):
        selected[0].status = "absent"


def test_resolve_scalars_uses_strict_array_indices_and_marks_valid_misses_absent():
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.mcp.selection import resolve_scalars

    selected = resolve_scalars({"items": ["first"]}, ("/items/0", "/items/9", "/missing/01"))
    assert tuple((entry.status, entry.value) for entry in selected) == (
        ("value", "first"),
        ("absent", None),
        ("absent", None),
    )

    with pytest.raises(InvalidInputError):
        resolve_scalars({"items": ["first"]}, ("/items/01",))
    with pytest.raises(InvalidInputError):
        resolve_scalars({"items": ["first"]}, ("/items/-",))


def test_resolve_scalars_rejects_any_selected_container_as_one_call():
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.mcp.selection import resolve_scalars

    with pytest.raises(InvalidInputError) as caught:
        resolve_scalars({"safe": "first", "nested": {"child": 1}}, ("/safe", "/nested"))

    assert caught.value.field == "pointers"
    assert caught.value.subtype == "scalar_selection_required"


def test_resolve_scalars_detects_a_later_container_before_serializing_values():
    from clinpgx_link.exceptions import InvalidInputError
    from clinpgx_link.mcp.selection import resolve_scalars

    with pytest.raises(InvalidInputError) as caught:
        resolve_scalars({"invalid": float("nan"), "nested": []}, ("/invalid", "/nested"))

    assert caught.value.subtype == "scalar_selection_required"


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), "\ud800"])
def test_resolve_scalars_normalizes_invalid_source_scalars(bad_value):
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.mcp.selection import resolve_scalars

    with pytest.raises(DataValidationError) as caught:
        resolve_scalars({"unsafe-source-value": bad_value}, ("/unsafe-source-value",))

    assert str(caught.value) == "Source value is not finite UTF-8 JSON."
    assert "unsafe-source-value" not in str(caught.value)


def test_finite_json_bytes_is_sorted_compact_utf8_and_normalizes_failures():
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.mcp.selection import finite_json_bytes

    assert finite_json_bytes({"z": "é", "a": [True, None]}) == b'{"a":[true,null],"z":"\xc3\xa9"}'
    with pytest.raises(DataValidationError):
        finite_json_bytes({"bad": object()})
    with pytest.raises(DataValidationError):
        finite_json_bytes("\ud800")


def test_resolve_scalars_defers_oversized_first_value_but_inlines_smaller_later_values():
    from clinpgx_link.mcp.selection import resolve_scalars

    selected = resolve_scalars(
        {"large": "ééé", "number": 1, "flag": True},
        ("/large", "/number", "/flag"),
        max_inline_bytes=5,
    )

    assert tuple(entry.status for entry in selected) == ("deferred", "value", "value")
    assert selected[0].value is None
    assert selected[0].byte_length == 8
    assert selected[0].sha256 == "d750db8fb580691faf4d590786b722210145aa8aefaf5f11fd033c48a7212bd4"
    assert selected[1].value == 1
    assert type(selected[1].value) is int
    assert selected[2].value is True


def test_resolve_scalars_counts_exact_multibyte_and_numeric_json_boundaries():
    from clinpgx_link.mcp.selection import resolve_scalars

    exact = resolve_scalars({"text": "é", "number": 12}, ("/text", "/number"), max_inline_bytes=6)
    short = resolve_scalars({"text": "é", "number": 12}, ("/text", "/number"), max_inline_bytes=5)

    assert tuple((entry.status, entry.value) for entry in exact) == (
        ("value", "é"),
        ("value", 12),
    )
    assert tuple(entry.status for entry in short) == ("value", "deferred")
    assert short[1].byte_length == 2
    assert short[1].sha256 == "6b51d431df5d7f141cbececcf79edf3dd861c3b4069f0b11661a3eefacbba918"
