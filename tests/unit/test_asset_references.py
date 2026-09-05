"""Offline source references remain deterministic and bounded across restarts."""

import pytest

from clinpgx_link.exceptions import InvalidInputError


def test_asset_reference_roundtrip_includes_snapshot_member_and_exact_digest():
    from clinpgx_link.content.assets import AssetReference

    reference = AssetReference(
        "sha256:" + "a" * 64, "data/genes.zip", "nested/évidence.tsv", "b" * 64
    )
    encoded = reference.encode()
    assert AssetReference.decode(encoded) == reference
    assert reference.encode() == encoded
    assert (
        AssetReference(
            "sha256:" + "c" * 64, "data/genes.zip", "nested/évidence.tsv", "b" * 64
        ).encode()
        != encoded
    )
    archive = AssetReference("sha256:" + "a" * 64, "data/genes.zip", None, "b" * 64)
    assert AssetReference.decode(archive.encode()).member is None


def test_asset_reference_rejects_corruption_noncanonical_encoding_and_excess_size():
    from clinpgx_link.content.assets import AssetReference

    reference = AssetReference(
        "sha256:" + "a" * 64, "data/genes.zip", "genes.tsv", "b" * 64
    ).encode()
    for invalid in (
        reference + "=",
        reference[:-1] + "!",
        "asset:" + "x" * 12000,
        "https://example.org/data",
    ):
        with pytest.raises(InvalidInputError):
            AssetReference.decode(invalid)


@pytest.mark.parametrize(
    "snapshot,dataset,member,digest",
    [
        ("invalid", "data/genes.zip", None, "b" * 64),
        ("sha256:" + "a" * 64, "", None, "b" * 64),
        ("sha256:" + "a" * 64, "data/genes.zip", "x" * 4097, "b" * 64),
        ("sha256:" + "a" * 64, "data/genes.zip", None, "invalid"),
    ],
)
def test_asset_reference_rejects_invalid_identity_components(snapshot, dataset, member, digest):
    from clinpgx_link.content.assets import AssetReference

    with pytest.raises(InvalidInputError):
        AssetReference(snapshot, dataset, member, digest).encode()
