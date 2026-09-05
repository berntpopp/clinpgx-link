"""Shared data-plane contracts for ClinPGx source adapters and repositories."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RetrievalTimeKind = Literal["upstream_acquisition", "local_admission", "unknown"]
SourceScope = Literal["snapshot", "dataset", "member", "response"]
RetrievalTimeScope = Literal["aggregate_snapshot", "source_recorded"]


@dataclass(frozen=True)
class SourceInfo:
    """Immutable provenance for one API, website, cache, or download result."""

    source: str
    url: str
    retrieved_at: str
    sha256: str
    data_source: str
    published_at: str | None = None
    release_tag: str | None = None
    coverage: str = "unknown"
    warnings: tuple[str, ...] = ()
    retrieval_time_kind: RetrievalTimeKind = "unknown"
    acquired_at: str | None = None
    admitted_at: str | None = None
    source_scope: SourceScope = "response"
    retrieval_time_scope: RetrievalTimeScope = "source_recorded"


@dataclass
class SourceResponse:
    """Complete domain value and its provenance, before MCP envelope shaping."""

    value: Any
    source: SourceInfo
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BoundRequest:
    """A registry-validated upstream request safe for the HTTP client to execute."""

    method: str
    path: str
    params: dict[str, Any]
    form: dict[str, Any] | None = None
    representation: str = "json"


__all__ = [
    "BoundRequest",
    "RetrievalTimeKind",
    "RetrievalTimeScope",
    "SourceInfo",
    "SourceResponse",
    "SourceScope",
]
