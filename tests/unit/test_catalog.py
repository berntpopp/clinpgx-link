"""Download registry and immutable local-source contracts."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[2]
REGISTRY = PROJECT_ROOT / "docs" / "research" / "clinpgx-download-registry-2026-09-05.json"
COVERAGE = PROJECT_ROOT / "clinpgx_link" / "data" / "coverage.json"


def test_catalog_accounts_for_all_registry_entries_without_inventing_rights() -> None:
    """Catch omitted files, collapsed source dates, or unreviewed licenses marked distributable."""
    from clinpgx_link.data.catalog import DownloadCatalog

    catalog = DownloadCatalog.load(REGISTRY, COVERAGE)
    entries = catalog.list_entries()
    genes = catalog.get("data/genes.zip")

    assert len(entries) == 120
    assert len({entry.dataset_id for entry in entries}) == 120
    assert genes.file_name == "genes.zip"
    assert genes.source_date == "2026-09-05T00:37:36-07:00"
    assert genes.reported_size == 2_905_541
    assert genes.license_tier == "needs_review"
    assert genes.distribution_allowed is False
    assert genes.evidence == ("docs/research/clinpgx-download-registry-2026-09-05.json",)
    assert len({entry.source_date for entry in entries}) > 1


def test_source_input_is_frozen_and_binds_exact_bytes_to_acquisition_evidence(tmp_path) -> None:
    """Catch mutable or path-only build inputs whose provenance can drift after validation."""
    from clinpgx_link.data.catalog import SourceInput

    raw = b"PK\x03\x04fixture"
    path = tmp_path / "genes.zip"
    path.write_bytes(raw)
    source = SourceInput.from_path(
        dataset_id="data/genes.zip",
        path=path,
        source_url="https://api.clinpgx.org/v1/download/file/data/genes.zip",
        retrieved_at="2026-09-05T08:00:00Z",
        published_at="2026-09-05T00:37:36-07:00",
        media_type="application/zip",
        license_id="clinpgx-cc-by-sa-4.0-policy",
        tier="approved_registry",
        etag='"4de1"',
        version_id="version-1",
    )

    assert source.sha256 == hashlib.sha256(raw).hexdigest()
    assert source.byte_count == len(raw)
    assert source.path == path
    assert source.etag == '"4de1"'
    with pytest.raises(dataclasses.FrozenInstanceError):
        source.sha256 = "0" * 64  # type: ignore[misc]


def test_source_input_rejects_changed_bytes_before_build(tmp_path) -> None:
    """Catch a local artifact changed after its immutable receipt was constructed."""
    from clinpgx_link.data.catalog import SourceInput
    from clinpgx_link.exceptions import DataValidationError

    path = tmp_path / "genes.zip"
    path.write_bytes(b"first")
    source = SourceInput.from_path(
        dataset_id="data/genes.zip",
        path=path,
        source_url="https://api.clinpgx.org/v1/download/file/data/genes.zip",
        retrieved_at="2026-09-05T08:00:00Z",
        published_at="2026-09-05T00:37:36-07:00",
        media_type="application/zip",
        license_id="operator-local-only",
        tier="approved_registry",
    )
    path.write_bytes(b"second")

    with pytest.raises(DataValidationError):
        source.read_verified()


@pytest.mark.parametrize(
    ("dataset_id", "source_url", "retrieved_at", "tier"),
    [
        (
            "../genes.zip",
            "https://api.clinpgx.org/v1/download/file/data/genes.zip",
            "2026-09-05T08:00:00Z",
            "approved_registry",
        ),
        (
            "data/genes.zip",
            "http://api.clinpgx.org/genes.zip",
            "2026-09-05T08:00:00Z",
            "approved_registry",
        ),
        ("data/genes.zip", "https://api.clinpgx.org/genes.zip", "today", "approved_registry"),
        ("data/genes.zip", "https://api.clinpgx.org/genes.zip", "2026-09-05T08:00:00Z", "full"),
    ],
)
def test_source_input_rejects_ambiguous_identity_or_acquisition_metadata(
    tmp_path: Path,
    dataset_id: str,
    source_url: str,
    retrieved_at: str,
    tier: str,
) -> None:
    """Catch traversal, mutable transport, invented time, or unsupported catalog tiers."""
    from clinpgx_link.data.catalog import SourceInput
    from clinpgx_link.exceptions import InvalidInputError

    path = tmp_path / "genes.zip"
    path.write_bytes(b"fixture")
    with pytest.raises(InvalidInputError):
        SourceInput.from_path(
            dataset_id=dataset_id,
            path=path,
            source_url=source_url,
            retrieved_at=retrieved_at,
            published_at=None,
            media_type="application/zip",
            license_id="operator-local-only",
            tier=tier,
        )


def test_direct_source_input_constructor_cannot_bypass_receipt_validation(tmp_path: Path) -> None:
    """Catch callers forging provenance by bypassing the from_path convenience constructor."""
    from clinpgx_link.data.catalog import SourceInput
    from clinpgx_link.exceptions import InvalidInputError

    path = tmp_path / "genes.zip"
    path.write_bytes(b"fixture")
    with pytest.raises(InvalidInputError):
        SourceInput(
            dataset_id="../../forged",
            file_name="forged",
            path=path,
            source_url="http://evil.invalid/forged",
            retrieved_at="not-a-time",
            published_at=None,
            sha256=hashlib.sha256(b"fixture").hexdigest(),
            byte_count=7,
            media_type="application/zip",
            license_id="operator-local-only",
            tier="approved_registry",
        )


def test_source_receipt_requires_url_and_filename_to_match_dataset_identity(tmp_path: Path) -> None:
    """Catch valid-looking but cross-wired source metadata entering an immutable manifest."""
    from clinpgx_link.data.catalog import SourceInput
    from clinpgx_link.exceptions import InvalidInputError

    path = tmp_path / "genes.zip"
    path.write_bytes(b"fixture")
    with pytest.raises(InvalidInputError):
        SourceInput.from_path(
            dataset_id="data/genes.zip",
            path=path,
            source_url="https://api.clinpgx.org/v1/download/file/data/drugs.zip",
            retrieved_at="2026-09-05T08:00:00Z",
            published_at=None,
            media_type="application/zip",
            license_id="operator-local-only",
            tier="approved_registry",
        )


@pytest.mark.parametrize("reported_size", [True, -1])
def test_catalog_rejects_boolean_or_negative_reported_sizes(
    tmp_path: Path, reported_size: object
) -> None:
    """Catch invalid size metadata being presented as a measured byte count."""
    from clinpgx_link.data.catalog import DownloadCatalog
    from clinpgx_link.exceptions import DataValidationError

    registry = tmp_path / "registry.json"
    coverage = tmp_path / "coverage.json"
    registry.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": "data/genes.zip",
                        "fileName": "genes.zip",
                        "lastModified": "2026-09-05T00:37:36-07:00",
                        "size": reported_size,
                    }
                ]
            }
        )
    )
    coverage.write_text(
        json.dumps(
            {
                "datasets": [
                    {"dataset_id": "data/genes.zip", "limitations": [], "evidence": []}
                ]
            }
        )
    )

    with pytest.raises(DataValidationError):
        DownloadCatalog.load(registry, coverage)


def test_catalog_rejects_duplicate_coverage_rows(tmp_path: Path) -> None:
    """Catch a duplicate ledger identity silently replacing earlier coverage evidence."""
    from clinpgx_link.data.catalog import DownloadCatalog
    from clinpgx_link.exceptions import DataValidationError

    registry = tmp_path / "registry.json"
    coverage = tmp_path / "coverage.json"
    registry.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": "data/genes.zip",
                        "fileName": "genes.zip",
                        "lastModified": "2026-09-05T00:37:36-07:00",
                        "size": 10,
                    }
                ]
            }
        )
    )
    coverage.write_text(
        json.dumps(
            {
                "datasets": [
                    {"dataset_id": "data/genes.zip", "limitations": [], "evidence": []},
                    {"dataset_id": "data/genes.zip", "limitations": ["replaced"], "evidence": []},
                ]
            }
        )
    )

    with pytest.raises(DataValidationError, match="duplicate"):
        DownloadCatalog.load(registry, coverage)
