"""Strict canonical source-manifest provenance and coverage contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def coverage(operation_id: str = "GET /data/gene") -> dict[str, object]:
    return {
        "domain": "gene",
        "operation_id": operation_id,
        "status": "local",
        "fallback_reason": "Live detail contains fields absent from the export.",
        "fallback_operation_id": "GET /data/gene/{id}",
    }


def member(path: str = "genes.tsv") -> dict[str, object]:
    return {
        "path": path,
        "compressed_size": 80,
        "uncompressed_size": 120,
        "sha256": "b" * 64,
        "consumed": True,
        "indexed": True,
    }


def artifact(logical_name: str = "data/genes.zip") -> dict[str, object]:
    return {
        "logical_name": logical_name,
        "registry_filename": logical_name.removeprefix("data/"),
        "tier": "canonical_page",
        "acquisition_url": "https://api.clinpgx.org/v1/download/file/" + logical_name,
        "final_url": "https://s3.pgkb.org/" + logical_name,
        "registry_last_modified": "2026-09-05T01:02:03-07:00",
        "registry_size": 100,
        "http_last_modified": "Fri, 05 Sep 2026 08:02:03 GMT",
        "http_etag": '"source-etag"',
        "http_version_id": None,
        "http_content_length": 100,
        "sha256": "a" * 64,
        "byte_count": 100,
        "retrieved_at": "2026-09-05T10:02:04.125+02:00",
        "embedded_marker": "CREATED_2026-09-05.txt",
        "embedded_created_at": "2026-09-05T00:00:00Z",
        "members": [member()],
        "parser": {"name": "genes-tsv", "version": "1.0.0"},
        "transformation_sha256": None,
        "status": "indexed",
        "reason": None,
        "imported_counts": {"rows": 25041, "documents": 0},
        "rejected_rows": 0,
        "validation_result": "passed_with_warnings",
        "license_id": "clinpgx-cc-by-sa-4.0",
        "coverage": [coverage()],
    }


def manifest_value() -> dict[str, object]:
    return {
        "schema_version": 1,
        "profile": "core",
        "source_set_identity": "sha256:" + "c" * 64,
        "transformation": {
            "sha256": "d" * 64,
            "revision": "e" * 40,
            "schema_version": "1.0.0",
        },
        "registry": {
            "url": "https://api.clinpgx.org/v1/data/file/data/?view=min",
            "retrieved_at": "2026-09-05T10:00:00Z",
            "sha256": "f" * 64,
            "etag": None,
            "last_modified": None,
            "parser_version": "1.0.0",
        },
        "artifacts": [artifact()],
        "coverage": [coverage()],
        "anomalies": [
            {
                "anomaly_id": "broken_gene_vip_flag",
                "affected_sources": ["data/genes.zip"],
                "count": 25041,
            }
        ],
    }


def test_source_manifest_canonical_unicode_roundtrip_preserves_observations() -> None:
    from clinpgx_link.releases.source_manifest import canonical_bytes, parse_source_manifest

    value = manifest_value()
    value["artifacts"][0]["embedded_marker"] = "CREATED_café_2026-09-05.txt"  # type: ignore[index]
    raw = _canonical(value)
    model = parse_source_manifest(raw)

    assert canonical_bytes(model) == raw
    assert model.registry.etag is None
    assert model.artifacts[0].http_version_id is None
    assert model.artifacts[0].retrieved_at == "2026-09-05T10:02:04.125+02:00"
    assert model.artifacts[0].embedded_marker == "CREATED_café_2026-09-05.txt"


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("schema_version",), True),
        (("profile",), "full"),
        (("source_set_identity",), "sha256:" + "A" * 64),
        (("unexpected",), "extra"),
        (("registry", "unexpected"), "extra"),
        (("registry", "retrieved_at"), "2026-09-05"),
        (("registry", "etag"), "x" * 513),
        (("artifacts", 0, "registry_size"), True),
        (("artifacts", 0, "http_content_length"), False),
        (("artifacts", 0, "byte_count"), True),
        (("artifacts", 0, "members", 0, "path"), "../genes.tsv"),
        (("artifacts", 0, "members", 0, "indexed"), "true"),
        (("artifacts", 0, "validation_result"), "successful"),
        (("artifacts", 0, "license_id"), ""),
        (("anomalies", 0, "anomaly_id"), "everything_is_fine"),
    ],
)
def test_source_manifest_rejects_wrong_types_shapes_and_values(
    path: tuple[object, ...], replacement: object
) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.source_manifest import parse_source_manifest

    value: object = manifest_value()
    target = value
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = replacement  # type: ignore[index]
    with pytest.raises(DataValidationError):
        parse_source_manifest(_canonical(value))


def test_source_manifest_requires_nullable_http_fields_to_be_present() -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.source_manifest import parse_source_manifest

    value = manifest_value()
    del value["artifacts"][0]["http_etag"]  # type: ignore[index]
    with pytest.raises(DataValidationError):
        parse_source_manifest(_canonical(value))


def test_source_manifest_requires_schema_version_to_be_present() -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.source_manifest import parse_source_manifest

    value = manifest_value()
    del value["schema_version"]
    with pytest.raises(DataValidationError):
        parse_source_manifest(_canonical(value))


@pytest.mark.parametrize("inventory", ["artifacts", "coverage", "members", "anomalies"])
def test_source_manifest_rejects_unsorted_or_duplicate_inventories(inventory: str) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.source_manifest import parse_source_manifest

    value = manifest_value()
    if inventory == "artifacts":
        value["artifacts"] = [artifact("data/z.zip"), artifact("data/a.zip")]
    elif inventory == "coverage":
        value["coverage"] = [coverage("GET /data/z"), coverage("GET /data/a")]
    elif inventory == "members":
        value["artifacts"][0]["members"] = [member("z.tsv"), member("a.tsv")]  # type: ignore[index]
    else:
        value["anomalies"] = [copy.deepcopy(value["anomalies"][0])] * 2  # type: ignore[index]
    with pytest.raises(DataValidationError):
        parse_source_manifest(_canonical(value))


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "live_fallback_only", "fallback_reason": None},
        {"status": "excluded", "fallback_reason": "wrong", "fallback_operation_id": "GET /x"},
        {"status": "local", "fallback_reason": "missing", "fallback_operation_id": None},
    ],
)
def test_source_manifest_rejects_contradictory_coverage(changes: dict[str, object]) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.source_manifest import parse_source_manifest

    value = manifest_value()
    value["coverage"][0].update(changes)  # type: ignore[index,union-attr]
    with pytest.raises(DataValidationError):
        parse_source_manifest(_canonical(value))


@pytest.mark.parametrize(
    "changes",
    [
        {"parser": None, "transformation_sha256": None},
        {"parser": {"name": "genes", "version": "1.0.0"}, "transformation_sha256": "f" * 64},
        {"status": "indexed", "validation_result": "failed"},
        {"status": "quarantined", "validation_result": "passed", "reason": "bad"},
        {"status": "excluded", "reason": None},
    ],
)
def test_source_manifest_rejects_contradictory_artifact_state(changes: dict[str, object]) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.source_manifest import parse_source_manifest

    value = manifest_value()
    value["artifacts"][0].update(changes)  # type: ignore[index,union-attr]
    with pytest.raises(DataValidationError):
        parse_source_manifest(_canonical(value))


def test_quarantined_artifact_cannot_claim_an_indexed_member() -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.source_manifest import parse_source_manifest

    value = manifest_value()
    value["artifacts"][0].update(  # type: ignore[index,union-attr]
        status="quarantined",
        reason="Validation failed.",
        imported_counts={"rows": 0, "documents": 0},
        validation_result="failed",
    )
    with pytest.raises(DataValidationError):
        parse_source_manifest(_canonical(value))


def test_nonindexed_opaque_artifact_can_have_no_inspectable_members() -> None:
    from clinpgx_link.releases.source_manifest import parse_source_manifest

    value = manifest_value()
    value["artifacts"][0].update(  # type: ignore[index,union-attr]
        members=[],
        status="quarantined",
        reason="Archive could not be inspected.",
        imported_counts={"rows": 0, "documents": 0},
        validation_result="failed",
    )
    model = parse_source_manifest(_canonical(value))
    assert model.artifacts[0].members == ()


def test_source_manifest_rejects_noncanonical_duplicate_nonfinite_and_bounded_input() -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.source_manifest import parse_source_manifest

    valid = manifest_value()
    nested = b"[" * 10_000 + b"0" + b"]" * 10_000 + b"\n"
    with pytest.raises(RecursionError):
        json.loads(nested)

    inputs = [
        json.dumps(valid, indent=2).encode(),
        b'{"schema_version":1,"schema_version":1}\n',
        b'{"value":NaN}\n',
        nested,
        b"x" * (4 * 1024 * 1024 + 1),
        b"\xff",
    ]
    for raw in inputs:
        with pytest.raises(DataValidationError):
            parse_source_manifest(raw)


def _assert_recursive_objects_closed(value: object) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
        for child in value.values():
            _assert_recursive_objects_closed(child)
    elif isinstance(value, list):
        for child in value:
            _assert_recursive_objects_closed(child)


def _array_schemas(value: object) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    if isinstance(value, dict):
        if value.get("type") == "array":
            found.append(value)
        for child in value.values():
            found.extend(_array_schemas(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_array_schemas(child))
    return found


def test_source_manifest_generated_schema_matches_canonical_vendor_bytes() -> None:
    from clinpgx_link.releases.source_manifest import source_manifest_schema_bytes

    expected = (
        Path(__file__).parents[2] / "vendor/clinpgx/source-manifest.schema.json"
    ).read_bytes()
    generated = source_manifest_schema_bytes()
    assert generated == expected
    schema = json.loads(generated)
    _assert_recursive_objects_closed(schema)


def test_source_schema_externally_enforces_every_array_bound() -> None:
    from clinpgx_link.releases.source_manifest import source_manifest_schema_bytes

    schema = json.loads(source_manifest_schema_bytes())
    arrays = _array_schemas(schema)
    assert len(arrays) == 6
    assert all("minItems" in item and "maxItems" in item for item in arrays)
    assert all("minLength" not in item and "maxLength" not in item for item in arrays)

    validator = Draft202012Validator(schema)
    validator.validate(manifest_value())
    value = manifest_value()
    value["artifacts"] = []
    errors = list(validator.iter_errors(value))
    assert any(error.validator == "minItems" for error in errors)


def test_source_parser_classifies_nonbytes_as_invalid_data_not_resource_limit() -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.source_manifest import parse_source_manifest

    with pytest.raises(DataValidationError) as caught:
        parse_source_manifest("{}\n")  # type: ignore[arg-type]
    assert caught.value.subtype == "data_invalid"
