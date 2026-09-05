"""Compact owning-dataset provenance shared by local row presentations."""

from __future__ import annotations

import hashlib
from typing import Any

from clinpgx_link.models import SourceInfo


def derived_record_source(source: SourceInfo, record_id: str, raw: bytes) -> SourceInfo:
    """Preserve the owning receipt when retaining a derived dataset row."""
    return SourceInfo(
        "ClinPGx Link derived dataset record",
        "clinpgx://dataset-record/" + record_id,
        source.retrieved_at,
        hashlib.sha256(raw).hexdigest(),
        "derived",
        published_at=source.published_at,
        release_tag=source.release_tag,
        coverage="derived_not_original",
        warnings=source.warnings,
        retrieval_time_kind=source.retrieval_time_kind,
        acquired_at=source.acquired_at,
        admitted_at=source.admitted_at,
    )


def row_provenance(source: SourceInfo) -> dict[str, Any]:
    """Expose the stored archive receipt without reclassifying its timestamp."""
    return {
        "dataset_source_url": source.url,
        "archive_sha256": source.sha256,
        "published_at": source.published_at,
        "retrieved_at": source.retrieved_at,
        "retrieval_time_kind": source.retrieval_time_kind,
        "acquired_at": source.acquired_at,
        "admitted_at": source.admitted_at,
        "source_scope": source.source_scope,
        "retrieval_time_scope": source.retrieval_time_scope,
    }


__all__ = ["row_provenance"]
