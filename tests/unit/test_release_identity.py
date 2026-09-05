"""Stable release keys bind content and contracts, not observation time."""

import json

import pytest


def _identity(**overrides):
    from clinpgx_link.releases.identity import release_identity

    arguments = {
        "profile": "core",
        "sources": [("data/genes.zip", "a" * 64), ("data/chemicals.zip", "b" * 64)],
        "transformation_sha256": "c" * 64,
        "schema_version": "1.0.0",
        "licenses_sha256": "d" * 64,
    }
    arguments.update(overrides)
    return release_identity(**arguments)


def test_release_key_is_order_independent_and_uses_only_stable_inputs():
    original = _identity()
    reordered = _identity(sources=[("data/chemicals.zip", "b" * 64), ("data/genes.zip", "a" * 64)])
    assert original == reordered
    assert json.loads(original.canonical_key) == {
        "profile": "core",
        "sources": [
            {"logical_name": "data/chemicals.zip", "sha256": "b" * 64},
            {"logical_name": "data/genes.zip", "sha256": "a" * 64},
        ],
        "transformation_sha256": "c" * 64,
        "schema_version": "1.0.0",
        "licenses_sha256": "d" * 64,
    }
    assert original.canonical_key.endswith(b"\n")
    assert original.tag.startswith("data-clinpgx-core-")
    assert len(original.tag.removeprefix("data-clinpgx-core-")) == 16


def test_release_key_golden_utf8_bytes_digest_and_immutable_tag():
    # This literal freezes wire identity, including UTF-8, ordering and whitespace.
    result = _identity(sources=[("data/é.zip", "a" * 64)])
    expected = (
        b'{"licenses_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",'
        b'"profile":"core","schema_version":"1.0.0","sources":[{"logical_name":"data/\xc3\xa9.zip",'
        b'"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],'
        b'"transformation_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}\n'
    )
    assert result.canonical_key == expected
    assert result.source_set_identity == (
        "sha256:f2f2541a9c5e4204e5e149fe4692ea9eddcb80d39522a8e0cfd67b099e0b5725"
    )
    assert result.tag == "data-clinpgx-core-f2f2541a9c5e4204"


@pytest.mark.parametrize(
    "changes",
    [
        {"profile": "extended"},
        {"sources": [("data/genes.zip", "e" * 64), ("data/chemicals.zip", "b" * 64)]},
        {"sources": [("data/variants.zip", "a" * 64), ("data/chemicals.zip", "b" * 64)]},
        {"transformation_sha256": "e" * 64},
        {"schema_version": "1.1.0"},
        {"licenses_sha256": "e" * 64},
    ],
)
def test_release_identity_changes_for_each_authoritative_input(changes):
    changed = _identity(**changes)
    assert changed.source_set_identity != _identity().source_set_identity
    assert changed.tag != _identity().tag


@pytest.mark.parametrize(
    "changes",
    [
        {"profile": "full"},
        {"sources": []},
        {"sources": [("data/genes.zip", "a" * 64), ("data/genes.zip", "b" * 64)]},
        {"sources": [("", "a" * 64)]},
        {"sources": [("data/genes.zip", "A" * 64)]},
        {"sources": [("data/genes.zip",)]},
        {"sources": [None]},
        {"sources": ["ab"]},
        {"sources": [{"data/genes.zip": "a" * 64, "other": "b" * 64}]},
        {"schema_version": "01.0.0"},
        {"licenses_sha256": "unknown"},
        {"transformation_sha256": "main"},
    ],
)
def test_invalid_or_ambiguous_release_key_inputs_are_rejected(changes):
    from clinpgx_link.exceptions import DataValidationError

    with pytest.raises(DataValidationError) as failure:
        _identity(**changes)
    assert failure.value.subtype == "release_key_invalid"
