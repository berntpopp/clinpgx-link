"""Strict rights evidence, publication gates, and stable identity binding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.unit.test_source_manifest import _canonical, manifest_value


def decision(allowed: bool = False, *, reviewed: bool = False) -> dict[str, object | None]:
    return {
        "allowed": allowed,
        "reviewed_at": "2026-09-05T10:00:00Z" if reviewed else None,
        "reviewer": "Synthetic rights reviewer" if reviewed else None,
        "rationale": "Approved only for this synthetic test mode." if reviewed else None,
    }


def license_value(license_id: str = "clinpgx-cc-by-sa-4.0") -> dict[str, object]:
    return {
        "license_id": license_id,
        "name": "Synthetic ClinPGx rights evidence",
        "spdx_expression": "CC-BY-SA-4.0",
        "urls": [
            "https://creativecommons.org/licenses/by-sa/4.0/",
            "https://www.clinpgx.org/page/dataUsagePolicy",
        ],
        "notice": {"text": "Exact synthetic notice text.", "sha256": None, "reference": None},
        "obligations": [
            {"kind": "attribution", "text": "Retain source attribution."},
            {"kind": "share_alike", "text": "Preserve reviewed ShareAlike obligations."},
        ],
        "distribution": {
            "public": decision(),
            "controlled": decision(),
            "operator_local": decision(True, reviewed=True),
        },
        "affected_artifacts": ["data/genes.zip"],
        "needs_confirmation": False,
    }


def licenses_value() -> dict[str, object]:
    return {"schema_version": 1, "licenses": [license_value()]}


def _models() -> tuple[object, object]:
    from clinpgx_link.releases.identity import release_identity
    from clinpgx_link.releases.licenses import canonical_bytes, parse_licenses
    from clinpgx_link.releases.source_manifest import parse_source_manifest

    rights = parse_licenses(_canonical(licenses_value()))
    source = manifest_value()
    identity = release_identity(
        profile="core",
        sources=[("data/genes.zip", "a" * 64)],
        transformation_sha256="d" * 64,
        schema_version="1.0.0",
        licenses_sha256=hashlib.sha256(canonical_bytes(rights)).hexdigest(),
    )
    source["source_set_identity"] = identity.source_set_identity
    return parse_source_manifest(_canonical(source)), rights


def test_licenses_canonical_unicode_roundtrip_and_digest_notice() -> None:
    from clinpgx_link.releases.licenses import canonical_bytes, parse_licenses

    value = licenses_value()
    value["licenses"][0]["name"] = "Données synthétiques"  # type: ignore[index]
    notice = value["licenses"][0]["notice"]  # type: ignore[index]
    notice.update(  # type: ignore[union-attr]
        text=None,
        sha256="a" * 64,
        reference="https://example.test/notices/sha256-a",
    )
    raw = _canonical(value)
    model = parse_licenses(raw)
    assert canonical_bytes(model) == raw
    assert model.licenses[0].notice.sha256 == "a" * 64


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("schema_version",), True),
        (("unexpected",), "extra"),
        (("licenses", 0, "unexpected"), "extra"),
        (("licenses", 0, "spdx_expression"), 123),
        (("licenses", 0, "urls", 0), "http://example.test/terms"),
        (("licenses", 0, "distribution", "public", "allowed"), "false"),
        (("licenses", 0, "affected_artifacts", 0), ""),
    ],
)
def test_licenses_reject_wrong_types_shapes_and_values(
    path: tuple[object, ...], replacement: object
) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.licenses import parse_licenses

    value: object = licenses_value()
    target = value
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = replacement  # type: ignore[index]
    with pytest.raises(DataValidationError):
        parse_licenses(_canonical(value))


def test_licenses_requires_schema_version_to_be_present() -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.licenses import parse_licenses

    value = licenses_value()
    del value["schema_version"]
    with pytest.raises(DataValidationError):
        parse_licenses(_canonical(value))


@pytest.mark.parametrize("inventory", ["licenses", "urls", "obligations", "artifacts"])
def test_licenses_reject_unsorted_or_duplicate_inventories(inventory: str) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.licenses import parse_licenses

    value = licenses_value()
    item = value["licenses"][0]  # type: ignore[index]
    if inventory == "licenses":
        value["licenses"] = [license_value("z-license"), license_value("a-license")]
    elif inventory == "urls":
        item["urls"] = list(reversed(item["urls"]))  # type: ignore[index]
    elif inventory == "obligations":
        item["obligations"] = list(reversed(item["obligations"]))  # type: ignore[index]
    else:
        item["affected_artifacts"] = ["data/z.zip", "data/a.zip"]  # type: ignore[index]
    with pytest.raises(DataValidationError):
        parse_licenses(_canonical(value))


@pytest.mark.parametrize(
    "change",
    [
        {"allowed": True},
        {"reviewed_at": "2026-09-05T10:00:00Z"},
        {"reviewer": "Reviewer only"},
    ],
)
def test_licenses_reject_incomplete_review_evidence(change: dict[str, object]) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.licenses import parse_licenses

    value = licenses_value()
    value["licenses"][0]["distribution"]["public"].update(change)  # type: ignore[index,union-attr]
    with pytest.raises(DataValidationError):
        parse_licenses(_canonical(value))


def test_needs_confirmation_prevents_public_or_controlled_approval() -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.licenses import parse_licenses

    for mode in ("public", "controlled"):
        value = licenses_value()
        item = value["licenses"][0]  # type: ignore[index]
        item["needs_confirmation"] = True  # type: ignore[index]
        item["distribution"][mode] = decision(True, reviewed=True)  # type: ignore[index]
        with pytest.raises(DataValidationError):
            parse_licenses(_canonical(value))


@pytest.mark.parametrize(
    "notice",
    [
        {"text": None, "sha256": None, "reference": None},
        {"text": "notice", "sha256": "a" * 64, "reference": None},
        {"text": None, "sha256": "a" * 64, "reference": None},
    ],
)
def test_notice_requires_exact_text_or_digest_with_reference(notice: dict[str, object]) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.licenses import parse_licenses

    value = licenses_value()
    value["licenses"][0]["notice"] = notice  # type: ignore[index]
    with pytest.raises(DataValidationError):
        parse_licenses(_canonical(value))


def test_distribution_gate_requires_exact_bidirectional_rights_mapping() -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.licenses import validate_distribution

    source, rights = _models()
    validate_distribution(source, rights, "operator_local")
    with pytest.raises(DataValidationError):
        validate_distribution(source, rights, "public")

    for mutation in ("unknown_license", "wrong_affected", "unknown_affected"):
        source_value = manifest_value()
        rights_value = licenses_value()
        if mutation == "unknown_license":
            source_value["artifacts"][0]["license_id"] = "missing"  # type: ignore[index]
        elif mutation == "wrong_affected":
            rights_value["licenses"][0]["affected_artifacts"] = ["data/other.zip"]  # type: ignore[index]
        else:
            rights_value["licenses"][0]["affected_artifacts"] = [  # type: ignore[index]
                "data/genes.zip",
                "data/other.zip",
            ]
        from clinpgx_link.releases.licenses import parse_licenses
        from clinpgx_link.releases.source_manifest import parse_source_manifest

        with pytest.raises(DataValidationError):
            validate_distribution(
                parse_source_manifest(_canonical(source_value)),
                parse_licenses(_canonical(rights_value)),
                "operator_local",
            )


@pytest.mark.parametrize(
    "mutation",
    ["profile", "logical_name", "source_digest", "transformation", "schema", "rights"],
)
def test_stable_identity_rejects_mutation_of_every_release_key_input(mutation: str) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.licenses import validate_source_identity

    source, rights = _models()
    if mutation == "rights":
        rights = rights.model_copy(
            update={
                "licenses": (
                    rights.licenses[0].model_copy(update={"name": "Changed rights evidence"}),
                )
            }
        )
    elif mutation == "profile":
        source = source.model_copy(update={"profile": "extended"})
    elif mutation == "transformation":
        source = source.model_copy(
            update={"transformation": source.transformation.model_copy(update={"sha256": "0" * 64})}
        )
    elif mutation == "schema":
        source = source.model_copy(
            update={
                "transformation": source.transformation.model_copy(
                    update={"schema_version": "1.1.0"}
                )
            }
        )
    else:
        artifact_model = source.artifacts[0].model_copy(
            update={
                "logical_name" if mutation == "logical_name" else "sha256": (
                    "data/other.zip" if mutation == "logical_name" else "0" * 64
                )
            }
        )
        source = source.model_copy(update={"artifacts": (artifact_model,)})
    with pytest.raises(DataValidationError):
        validate_source_identity(source, rights)


def test_stable_identity_accepts_exact_projection_and_ignores_observation_time() -> None:
    from clinpgx_link.releases.licenses import validate_source_identity

    source, rights = _models()
    identity = validate_source_identity(source, rights)
    later = source.model_copy(
        update={
            "registry": source.registry.model_copy(update={"retrieved_at": "2030-01-01T00:00:00Z"})
        }
    )
    assert validate_source_identity(later, rights) == identity


def test_licenses_reject_duplicate_nonfinite_noncanonical_and_oversized_input() -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.licenses import parse_licenses

    for raw in (
        b'{"schema_version":1,"schema_version":1}\n',
        b'{"value":Infinity}\n',
        json.dumps(licenses_value(), indent=2).encode(),
        b"x" * (1024 * 1024 + 1),
    ):
        with pytest.raises(DataValidationError):
            parse_licenses(raw)


def _assert_recursive_objects_closed(value: object) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
        for child in value.values():
            _assert_recursive_objects_closed(child)
    elif isinstance(value, list):
        for child in value:
            _assert_recursive_objects_closed(child)


def test_licenses_generated_schema_matches_canonical_vendor_bytes() -> None:
    from clinpgx_link.releases.licenses import licenses_schema_bytes

    expected = (Path(__file__).parents[2] / "vendor/clinpgx/licenses.schema.json").read_bytes()
    generated = licenses_schema_bytes()
    assert generated == expected
    schema = json.loads(generated)
    _assert_recursive_objects_closed(schema)
