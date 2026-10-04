"""Recheck retained data/application compatibility before production startup."""

from __future__ import annotations

from pathlib import Path

from clinpgx_link import __version__
from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases.manifest import validate_manifest
from clinpgx_link.releases.materialization import parse_materialization, read_immutable
from clinpgx_link.releases.schema import MAX_DATABASE_SCHEMA_BYTES, parse_database_schema

_SUPPORTED_DATABASE_SCHEMA = "1.0.0"


def validate_runtime_release_contract(
    generation: Path,
    *,
    expected_release_tag: str,
    expected_snapshot: str,
    application_version: str = __version__,
) -> None:
    """Require the retained tree's authenticated release to fit this app/schema."""
    try:
        materialization = parse_materialization(
            read_immutable(generation, "materialization.json", 2 * 1024 * 1024)
        )
        schema = parse_database_schema(
            read_immutable(generation, "schema.json", MAX_DATABASE_SCHEMA_BYTES)
        )
        raw_manifest = materialization.outer_manifest_text.encode("utf-8")
        manifest = validate_manifest(raw_manifest, materialization.outer_manifest_sha256)
        schema_version = schema.database_schema_version
        if (
            materialization.release_tag != expected_release_tag
            or materialization.snapshot_id != expected_snapshot
            or materialization.database_schema_version != schema_version
            or materialization.schema_minimum != manifest.schema_identity.minimum
            or materialization.schema_maximum != manifest.schema_identity.maximum
            or materialization.artifact_sha256 != manifest.artifact.sha256
            or materialization.expanded_tree_sha256 != manifest.artifact.expanded_tree_sha256
            or manifest.dataset.release != expected_release_tag
            or manifest.schema_identity.actual != schema_version
            or schema_version != _SUPPORTED_DATABASE_SCHEMA
            or not manifest.application_compatibility.contains(application_version)
        ):
            raise ValueError("retained release is incompatible")
    except Exception as exc:
        raise DataValidationError(
            "Installed data release is incompatible with this application.",
            subtype="runtime_identity_mismatch",
        ) from exc


__all__ = ["validate_runtime_release_contract"]
