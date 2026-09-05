"""Source identities for immutable repository snapshots and retained assets."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Protocol

from clinpgx_link.exceptions import UpstreamUnavailableError
from clinpgx_link.models import SourceInfo

_DOWNLOADS_COLLECTION_URL = "https://www.clinpgx.org/downloads"


class DatasetProvenanceRow(Protocol):
    """The provenance columns required from a repository dataset row."""

    def __getitem__(self, key: str) -> Any: ...


def snapshot_source(
    connection: sqlite3.Connection, snapshot_id: str, release_tag: str
) -> SourceInfo:
    """Describe one aggregate snapshot without borrowing an archive identity."""
    row = connection.execute("SELECT MAX(retrieved_at) AS retrieved_at FROM dataset").fetchone()
    if row is None or row["retrieved_at"] is None:
        raise UpstreamUnavailableError(
            "Snapshot dataset provenance is incomplete", subtype="snapshot_invalid"
        )
    return SourceInfo(
        source="ClinPGx local snapshot",
        url=_DOWNLOADS_COLLECTION_URL,
        retrieved_at=str(row["retrieved_at"]),
        sha256=snapshot_id.removeprefix("sha256:"),
        data_source="download",
        release_tag=release_tag,
        coverage="installed_snapshot",
        source_scope="snapshot",
        retrieval_time_scope="aggregate_snapshot",
    )


def dataset_source(
    row: DatasetProvenanceRow,
    release_tag: str,
    *,
    digest: str | None = None,
    member: bool = False,
) -> SourceInfo:
    """Describe an owning archive or exact retained member from its stored receipt."""
    return SourceInfo(
        source="ClinPGx local snapshot",
        url=str(row["source_url"]),
        retrieved_at=str(row["retrieved_at"]),
        sha256=digest or str(row["sha256"]),
        data_source="download",
        published_at=str(row["published_at"]) if row["published_at"] is not None else None,
        release_tag=release_tag,
        coverage="installed_snapshot",
        warnings=tuple(json.loads(row["warnings_json"])),
        source_scope="member" if member else "dataset",
        retrieval_time_scope="source_recorded",
    )


__all__ = ["dataset_source", "snapshot_source"]
