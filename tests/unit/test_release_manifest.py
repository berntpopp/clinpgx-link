"""Trusted bytes, strict fleet shapes and semantic release constraints."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker


def manifest_value():
    return {
        "schema_version": 1,
        "dataset": {
            "name": "clinpgx-core",
            "release": "data-clinpgx-core-0123456789abcdef",
            "source": {
                "identifier": "clinpgx-core-source-set-v1",
                "url": "https://api.clinpgx.org/v1/download",
                "retrieved_at": "2026-09-05T10:00:00Z",
                "sha256": "a" * 64,
            },
        },
        "transformation": {"repository": "berntpopp/clinpgx-link", "revision": "b" * 40},
        "schema": {"minimum": "1.0.0", "maximum": "1.0.0", "actual": "1.0.0"},
        "record_counts": {"source_record": 42},
        "artifact": {
            "filename": "clinpgx-core.tar.zst",
            "sha256": "c" * 64,
            "compressed_size": 100,
            "max_compressed_size": 200,
            "expanded_tree_sha256": "d" * 64,
            "expanded_size": 400,
            "max_expanded_size": 800,
            "member_count": 4,
            "max_members": 4,
        },
        "license": {
            "name": "Synthetic operator-local test",
            "url": "https://example.test/license",
            "redistribution_allowed": False,
            "reviewed_at": "2026-09-05T10:00:00Z",
            "reviewer": "Synthetic fixture, not publication approval",
        },
        "previous_known_good_digest": "sha256:" + "c" * 64,
        "application_compatibility": {"minimum": "0.1.0", "maximum": "0.1.9"},
        "disclaimer": "Research only. Not for clinical decisions. Source dates differ.",
    }


def _validate(value, **kwargs):
    from clinpgx_link.releases.manifest import validate_manifest

    raw = json.dumps(value).encode()
    return validate_manifest(raw, hashlib.sha256(raw).hexdigest(), **kwargs)


def test_release_manifest_roundtrip_matches_vendored_fleet_schema():
    value = manifest_value()
    model = _validate(value)
    schema = json.loads(
        (
            Path(__file__).parents[2] / "vendor/genefoundry/data-release-manifest.schema.json"
        ).read_text()
    )
    serialized = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert serialized == value
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(serialized)
    assert model.application_compatibility.contains("0.1.5")
    assert not model.application_compatibility.contains("0.2.0")


def test_generated_schema_exactly_matches_pinned_router_contract():
    from clinpgx_link.releases.manifest import DataReleaseManifest

    vendored = json.loads(
        (
            Path(__file__).parents[2] / "vendor/genefoundry/data-release-manifest.schema.json"
        ).read_text()
    )
    generated = DataReleaseManifest.model_json_schema()

    assert generated == vendored
    assert generated["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert generated["properties"]["schema_version"]["const"] == 1
    assert generated["$defs"]["DatasetIdentity"]["properties"]["release"]["not"] == {
        "enum": ["latest", "main", "master", "head", "stable", "current"]
    }
    for model_name, field_name in (
        ("UpstreamSource", "retrieved_at"),
        ("LicenseEvidence", "reviewed_at"),
    ):
        timestamp_schema = generated["$defs"][model_name]["properties"][field_name]
        assert timestamp_schema["format"] == "date-time"
        assert timestamp_schema["pattern"].startswith("^")
    Draft202012Validator.check_schema(generated)


def test_timestamps_are_timezone_aware_datetimes_with_json_roundtrip():
    value = manifest_value()
    value["dataset"]["source"]["retrieved_at"] = "2026-09-05T12:30:45.125+02:30"

    model = _validate(value)

    retrieved_at = model.dataset.source.retrieved_at
    reviewed_at = model.license.reviewed_at
    assert isinstance(retrieved_at, datetime)
    assert retrieved_at.utcoffset() == timedelta(hours=2, minutes=30)
    assert isinstance(reviewed_at, datetime)
    assert reviewed_at.utcoffset() == timedelta(0)
    assert (
        model.model_dump(mode="json", by_alias=True)["dataset"]["source"]["retrieved_at"]
        == "2026-09-05T12:30:45.125000+02:30"
    )


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("schema_version",), True),
        (("unexpected",), "extra"),
        (("artifact", "unexpected"), "extra"),
        (("artifact", "compressed_size"), 201),
        (("artifact", "expanded_size"), 801),
        (("artifact", "member_count"), 5),
        (("artifact", "compressed_size"), "100"),
        (("artifact", "compressed_size"), True),
        (("schema", "actual"), "2.0.0"),
        (("application_compatibility", "minimum"), "9.0.0"),
        (("dataset", "release"), "latest"),
        (("dataset", "source", "url"), "http://example.test"),
        (("dataset", "source", "retrieved_at"), "2026-02-30T10:00:00Z"),
        (("transformation", "revision"), "main"),
        (("transformation", "repository"), ".invalid/repo"),
        (("record_counts", "source_record"), -1),
        (("license", "redistribution_allowed"), "true"),
    ],
)
def test_release_manifest_rejects_invalid_contracts(path, replacement):
    from clinpgx_link.exceptions import DataValidationError

    value = manifest_value()
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    with pytest.raises(DataValidationError):
        _validate(value)


def test_release_manifest_digest_is_checked_before_json_parsing():
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.manifest import validate_manifest

    with pytest.raises(DataValidationError) as failure:
        validate_manifest(b"not JSON", "0" * 64)
    assert failure.value.subtype == "manifest_digest"


def test_release_manifest_rejects_duplicate_keys_and_negative_public_rights():
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.manifest import validate_manifest

    raw = b'{"schema_version":1,"schema_version":1}'
    with pytest.raises(DataValidationError):
        validate_manifest(raw, hashlib.sha256(raw).hexdigest())
    with pytest.raises(DataValidationError) as failure:
        _validate(manifest_value(), public=True)
    assert failure.value.subtype == "publication_not_allowed"
