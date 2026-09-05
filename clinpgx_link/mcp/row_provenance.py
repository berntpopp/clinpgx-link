"""Compact owning-dataset provenance shared by local row presentations."""

from __future__ import annotations

from typing import Any

from clinpgx_link.models import SourceInfo


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
