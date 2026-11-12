"""Reproducible offline builder for one public ClinPGx core data release.

Inputs are a frozen acquisition (archive bytes plus receipt), a reviewed owner
authorization file, and the exact source revision. Outputs are the four release
assets. Every gate fails loudly; nothing is truncated, retried, or skipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import zstandard

from clinpgx_link import __version__
from clinpgx_link.data.catalog import SourceInput
from clinpgx_link.data.record_profiles import RECORD_PROFILES
from clinpgx_link.data_probe import probe as data_probe
from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.ingest.builder import build_snapshot, transform_identity_contract
from clinpgx_link.releases.acquisition import load_acquisition
from clinpgx_link.releases.authorization import (
    LICENSE_ID,
    DistributionAuthorization,
    parse_authorization,
    render_licenses,
)
from clinpgx_link.releases.bundle import BundleLimits, pack_bundle, verify_and_extract_bundle
from clinpgx_link.releases.contract_json import canonical_value_bytes
from clinpgx_link.releases.identity import release_identity
from clinpgx_link.releases.licenses import validate_distribution, validate_source_identity
from clinpgx_link.releases.manifest import CompatibilityRange, validate_manifest
from clinpgx_link.releases.materialization import validate_semantics
from clinpgx_link.releases.materialize import ReleaseInput, install_release
from clinpgx_link.releases.runtime_identity import verify_runtime_identity
from clinpgx_link.releases.schema import RecordCounts, build_database_schema
from clinpgx_link.releases.schema import canonical_bytes as schema_bytes
from clinpgx_link.releases.source_manifest import parse_source_manifest
from clinpgx_link.releases.source_provenance import inspect_archive, render_source_manifest

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
SCHEMA_VERSION = "1.0.0"
SOURCE_DATE_EPOCH = 1
ARTIFACT_NAME = "clinpgx-core.tar.zst"
MANIFEST_NAME = "data-release-manifest.json"
REPORT_NAME = "validation-report.json"
SUMS_NAME = "SHA256SUMS"
RELEASE_ASSETS = (ARTIFACT_NAME, MANIFEST_NAME, REPORT_NAME, SUMS_NAME)
REPOSITORY = "berntpopp/clinpgx-link"
RELEASE_LIMITS = BundleLimits(
    max_compressed_bytes=256 * _MIB,
    max_expanded_bytes=2 * _GIB,
    max_member_bytes=2 * _GIB,
    max_window_bytes=8 * _MIB,
)
_COUNT_TABLES = ("dataset", "record", "source_archive", "source_member", "membership")
# Credentials that could plausibly leak from a CI or operator environment.
_SECRET_PATTERNS = re.compile(
    rb"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}"
    rb"|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abprs]-[A-Za-z0-9-]{10,}"
)


@dataclass(frozen=True)
class BuiltRelease:
    release_tag: str
    snapshot_id: str
    runtime_identity_sha256: str
    record_counts: dict[str, int]
    assets: dict[str, str]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_MIB):
            digest.update(chunk)
    return digest.hexdigest()


def _write_immutable(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    path.chmod(0o444)


def _reject_secrets(name: str, raw: bytes) -> None:
    if _SECRET_PATTERNS.search(raw):
        raise DataValidationError(f"{name} contains a credential-shaped value")


def _require_profile_gate(built_manifest: dict[str, Any]) -> None:
    """Header drift on any profiled member must stop the release, not degrade it."""
    receipt = built_manifest["record_profile_validation"]
    datasets = {artifact["dataset_id"] for artifact in built_manifest["artifacts"]}
    expected = {p.profile_id for p in RECORD_PROFILES if p.dataset_id in datasets}
    assessed = {item["profile_id"]: item for item in receipt["profiles"]}
    drifted = sorted(
        f"{item['dataset_id']}:{item['member']} missing {item['missing_required_fields']}"
        for item in assessed.values()
        if item["status"] != "active"
    )
    if receipt["gate_status"] != "passing" or drifted or set(assessed) != expected:
        missing = sorted(expected - set(assessed))
        raise DataValidationError(
            f"Record profile gate failed (schema/header drift): drifted={drifted} "
            f"unassessed={missing}"
        )


def _counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    try:
        return {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in _COUNT_TABLES
        }
    finally:
        connection.close()


def _outer_manifest(
    *,
    tag: str,
    authorization: DistributionAuthorization,
    registry: Any,
    source_sha: str,
    revision: str,
    counts: dict[str, int],
    packed: Any,
    limits: BundleLimits,
    application_version: str,
) -> bytes:
    return canonical_value_bytes(
        {
            "schema_version": 1,
            "dataset": {
                "name": "clinpgx-core",
                "release": tag,
                "source": {
                    "identifier": "clinpgx-core-source-set-v1",
                    "url": registry.url,
                    "retrieved_at": registry.retrieved_at,
                    "sha256": source_sha,
                    "etag": registry.etag,
                    "last_modified": registry.last_modified,
                },
            },
            "transformation": {"repository": REPOSITORY, "revision": revision},
            "schema": {
                "minimum": SCHEMA_VERSION,
                "maximum": SCHEMA_VERSION,
                "actual": SCHEMA_VERSION,
            },
            "record_counts": counts,
            "artifact": {
                "filename": ARTIFACT_NAME,
                "sha256": packed.sha256,
                "compressed_size": packed.compressed_size,
                "max_compressed_size": limits.max_compressed_bytes,
                "expanded_tree_sha256": packed.expanded_tree_sha256,
                "expanded_size": packed.expanded_size,
                "max_expanded_size": limits.max_expanded_bytes,
                "member_count": packed.member_count,
                "max_members": limits.max_members,
            },
            "license": {
                "name": authorization.license_name,
                "url": "https://api.clinpgx.org/",
                "redistribution_allowed": True,
                "reviewed_at": authorization.reviewed_at,
                "reviewer": authorization.reviewer,
            },
            # Each release is installed into a fresh volume: bootstrap names itself.
            "previous_known_good_digest": f"sha256:{packed.sha256}",
            "application_compatibility": {
                "minimum": application_version,
                "maximum": application_version,
            },
            "disclaimer": authorization.disclaimer,
        }
    )


def build_release(
    *,
    acquisition_dir: Path,
    authorization_path: Path,
    output_dir: Path,
    revision: str,
    application_version: str = __version__,
    limits: BundleLimits = RELEASE_LIMITS,
) -> BuiltRelease:
    """Build, pack, install-verify and checksum one release into an empty directory."""
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise DataValidationError("Build revision must be a full 40-character Git SHA")
    output_dir = output_dir.resolve()
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise DataValidationError("Release output directory must be empty")
    receipt = load_acquisition(acquisition_dir.resolve())
    authorization_raw = authorization_path.read_bytes()
    _reject_secrets("authorization", authorization_raw)
    authorization = parse_authorization(authorization_raw)
    sources_dir = acquisition_dir.resolve() / "sources"
    evidence = {s.file_name: inspect_archive(sources_dir / s.file_name) for s in receipt.sources}
    notices = {item.notice for item in evidence.values()}
    if len(notices) != 1:
        raise DataValidationError("Archive license notices differ; rights cannot be collapsed")
    notice = notices.pop()
    if hashlib.sha256(notice.encode()).hexdigest() != authorization.notice_sha256:
        raise DataValidationError("Upstream license notice changed; a new rights review is needed")
    rights_raw, rights = render_licenses(
        authorization, notice, [s.dataset_id for s in receipt.sources]
    )
    sources = [
        SourceInput.from_path(
            dataset_id=s.dataset_id,
            path=sources_dir / s.file_name,
            source_url=s.acquisition_url,
            retrieved_at=s.retrieved_at,
            published_at=s.registry_last_modified,
            media_type=s.media_type,
            license_id=LICENSE_ID,
            tier="canonical_page",
            etag=s.http_etag,
            last_modified=s.http_last_modified,
            version_id=s.http_version_id,
        )
        for s in receipt.sources
    ]
    transform_sha = transform_identity_contract().rsplit("sha256:", 1)[1]
    key = release_identity(
        profile="core",
        sources=[(source.dataset_id, source.sha256) for source in sources],
        transformation_sha256=transform_sha,
        schema_version=SCHEMA_VERSION,
        licenses_sha256=hashlib.sha256(rights_raw).hexdigest(),
    )
    work = output_dir / ".work"
    work.mkdir(mode=0o700)
    built = build_snapshot(sources, work / "candidates", key.tag)
    _require_profile_gate(built.manifest)
    counts = _counts(built.database)
    source_raw = render_source_manifest(
        receipt=receipt,
        evidence=evidence,
        built_manifest=built.manifest,
        database=built.database,
        source_set_identity=key.source_set_identity,
        transformation_sha256=transform_sha,
        revision=revision,
        parser_version=__version__,
    )
    source_model = parse_source_manifest(source_raw)
    validate_source_identity(source_model, rights)
    validate_distribution(source_model, rights, "public")
    schema_raw = schema_bytes(build_database_schema(SCHEMA_VERSION, RecordCounts(**counts)))
    bundle_source = work / "bundle-source"
    bundle_source.mkdir(mode=0o700)
    os.replace(built.database, bundle_source / "clinpgx.sqlite")
    (bundle_source / "clinpgx.sqlite").chmod(0o444)
    for name, raw in (
        ("licenses.json", rights_raw),
        ("schema.json", schema_raw),
        ("source-manifest.json", source_raw),
    ):
        _reject_secrets(name, raw)
        _write_immutable(bundle_source / name, raw)
    # The size ceiling is enforced by pack_bundle's bounded writer: it raises, never truncates.
    packed = pack_bundle(
        bundle_source,
        work / ARTIFACT_NAME,
        source_date_epoch=SOURCE_DATE_EPOCH,
        limits=limits,
    )
    manifest_raw = _outer_manifest(
        tag=key.tag,
        authorization=authorization,
        registry=receipt.registry,
        source_sha=hashlib.sha256(source_raw).hexdigest(),
        revision=revision,
        counts=counts,
        packed=packed,
        limits=limits,
        application_version=application_version,
    )
    _reject_secrets(MANIFEST_NAME, manifest_raw)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    manifest = validate_manifest(manifest_raw, manifest_sha, public=True)
    semantic = validate_semantics(bundle_source, manifest)
    extracted = work / "verified-extraction"
    verify_and_extract_bundle(work / ARTIFACT_NAME, extracted, manifest.artifact, limits=limits)
    if validate_semantics(extracted, manifest).snapshot_id != semantic.snapshot_id:
        raise DataValidationError("Extracted bundle differs from its source tree")
    data_root = work / "data-root"
    data_root.mkdir(mode=0o700)
    installed = install_release(
        ReleaseInput(manifest_raw, manifest_sha, work / ARTIFACT_NAME),
        data_root=data_root,
        application_version=application_version,
        supported_schema=CompatibilityRange(minimum=SCHEMA_VERSION, maximum=SCHEMA_VERSION),
        limits=limits,
    )
    runtime = verify_runtime_identity(
        installed.path,
        expected_digest=installed.runtime_identity_sha256,
        expected_release_tag=key.tag,
    )
    probe = data_probe(installed.path / "clinpgx.sqlite", expected_snapshot=built.snapshot_id)
    if probe["record_count"] != counts["record"]:
        raise DataValidationError("Installed semantic probe disagrees with built counts")
    report = {
        "schema_version": 1,
        "approval": {
            "reviewer": authorization.reviewer,
            "reviewed_at": authorization.reviewed_at,
            "reference": authorization.reference,
            "quoted_authorization": authorization.quote,
            "interpretation": authorization.interpretation,
        },
        "profile": "core",
        "release_tag": key.tag,
        "source_set_identity": key.source_set_identity,
        "builder_source_revision": revision,
        "source_manifest_sha256": hashlib.sha256(source_raw).hexdigest(),
        "licenses_sha256": hashlib.sha256(rights_raw).hexdigest(),
        "schema_sha256": hashlib.sha256(schema_raw).hexdigest(),
        "artifact": {
            "filename": ARTIFACT_NAME,
            "sha256": packed.sha256,
            "compressed_size": packed.compressed_size,
            "expanded_tree_sha256": packed.expanded_tree_sha256,
            "expanded_size": packed.expanded_size,
            "member_count": packed.member_count,
        },
        "manifest_sha256": manifest_sha,
        "snapshot_id": built.snapshot_id,
        "runtime_identity_sha256": runtime["digest"],
        "record_counts": counts,
        "validation": {
            "acquired_archive_bytes_match_receipt": "passed",
            "embedded_notice_matches_authorization": "passed",
            "record_profile_gate": "passed",
            "rights_source_identity_and_public_distribution": "passed",
            "source_tree_semantics": "passed",
            "bundle_digest_and_expanded_tree": "passed",
            "extracted_tree_semantics": "passed",
            "fresh_volume_materialization": "passed",
            "runtime_identity": "passed",
            "semantic_probe": probe,
            "upstream_requests_made": False,
        },
        "toolchain": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "zstandard_python": zstandard.__version__,
            "application_package_version_during_build": __version__,
        },
    }
    report_raw = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    _reject_secrets(REPORT_NAME, report_raw)
    os.replace(work / ARTIFACT_NAME, output_dir / ARTIFACT_NAME)
    _write_immutable(output_dir / MANIFEST_NAME, manifest_raw)
    _write_immutable(output_dir / REPORT_NAME, report_raw)
    digests = {name: _sha256_file(output_dir / name) for name in RELEASE_ASSETS[:3]}
    sums = "".join(f"{digests[name]}  {name}\n" for name in sorted(digests))
    _write_immutable(output_dir / SUMS_NAME, sums.encode())
    digests[SUMS_NAME] = _sha256_file(output_dir / SUMS_NAME)
    _remove_tree(work)
    return BuiltRelease(
        release_tag=key.tag,
        snapshot_id=built.snapshot_id,
        runtime_identity_sha256=runtime["digest"],
        record_counts=counts,
        assets=digests,
    )


def _remove_tree(root: Path) -> None:
    """Read-only (0444/0555) build intermediates must be made writable to be removed."""
    for directory, names, files in os.walk(root):
        os.chmod(directory, 0o700)
        for name in names + files:
            path = Path(directory) / name
            if not path.is_symlink():
                path.chmod(0o700)
    shutil.rmtree(root)


__all__ = ["RELEASE_ASSETS", "RELEASE_LIMITS", "BuiltRelease", "build_release"]
