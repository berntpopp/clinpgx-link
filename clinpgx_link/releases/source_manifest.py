"""Strict frozen provenance contract for retained ClinPGx source bytes.

Only acquired artifacts with retained bytes belong in ``artifacts``. Registry-only
or unavailable sources remain accounted for by the separate catalog and by explicit
aggregate excluded coverage/anomalies; this model never fabricates their digests or
receipts. Parsing this inner contract does not authenticate its enclosing bundle.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BeforeValidator, ConfigDict, Field, StrictBool, model_validator

from clinpgx_link.releases.contract_json import (
    SCHEMA_METADATA,
    HttpsUrl,
    NonNegativeInt,
    PositiveInt,
    SchemaVersion,
    SemanticVersion,
    Sha256Hex,
    Sha256Identity,
    ShortText,
    StrictModel,
    Timestamp,
    json_array,
    parse_canonical,
    schema_bytes,
)
from clinpgx_link.releases.contract_json import (
    canonical_bytes as _canonical_bytes,
)

MAX_SOURCE_MANIFEST_BYTES = 4 * 1024 * 1024
ArtifactTier = Literal[
    "canonical_page", "approved_registry", "experimental_or_legacy", "related_project"
]
ArtifactStatus = Literal["indexed", "metadata_only", "quarantined", "excluded"]
ValidationResult = Literal["passed", "passed_with_warnings", "failed", "not_run"]
CoverageStatus = Literal["local", "live_fallback_only", "metadata_only", "excluded"]
AnomalyId = Literal[
    "api_schema_response_drift",
    "api_source_enum_drift",
    "broken_gene_vip_flag",
    "broken_haplotype_readme",
    "corrupt_legacy_haplotype_archive",
    "empty_cpic_guidelines",
    "header_only_cpic_alleles",
    "source_count_discrepancy",
]
LogicalName = Annotated[str, Field(min_length=6, max_length=512)]
MemberPath = Annotated[str, Field(min_length=1, max_length=1024)]
Metadata = Annotated[str, Field(min_length=1, max_length=512)]
OperationId = Annotated[str, Field(min_length=1, max_length=512)]


def _safe_relative(value: str, *, data_prefix: bool) -> bool:
    if data_prefix and not value.startswith("data/"):
        return False
    return not (
        value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _ordered_unique[KeyT: (str, tuple[str, str])](
    values: Sequence[object], keys: Sequence[KeyT]
) -> bool:
    return len(keys) == len(set(keys)) and keys == sorted(keys)


class TransformationContract(StrictModel):
    sha256: Sha256Hex
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    schema_version: SemanticVersion


class RegistryObservation(StrictModel):
    url: HttpsUrl
    retrieved_at: Timestamp
    sha256: Sha256Hex
    etag: Metadata | None
    last_modified: Metadata | None
    parser_version: SemanticVersion


class ImportedCounts(StrictModel):
    rows: NonNegativeInt
    documents: NonNegativeInt


class MemberProvenance(StrictModel):
    path: MemberPath
    compressed_size: NonNegativeInt
    uncompressed_size: NonNegativeInt
    sha256: Sha256Hex
    consumed: StrictBool
    indexed: StrictBool

    @model_validator(mode="after")
    def consistent(self) -> MemberProvenance:
        if not _safe_relative(self.path, data_prefix=False) or (self.indexed and not self.consumed):
            raise ValueError("unsafe or contradictory member provenance")
        return self


class ParserIdentity(StrictModel):
    name: ShortText
    version: SemanticVersion


class CoverageEntry(StrictModel):
    domain: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    operation_id: OperationId
    status: CoverageStatus
    fallback_reason: ShortText | None
    fallback_operation_id: OperationId | None

    @model_validator(mode="after")
    def fallback_consistent(self) -> CoverageEntry:
        has_reason = self.fallback_reason is not None
        has_operation = self.fallback_operation_id is not None
        if has_reason != has_operation:
            raise ValueError("fallback reason and operation must be paired")
        if self.status == "live_fallback_only" and not has_reason:
            raise ValueError("live fallback coverage requires an explicit fallback")
        if self.status in {"metadata_only", "excluded"} and has_reason:
            raise ValueError("this coverage status cannot claim fallback behavior")
        return self


class ArtifactProvenance(StrictModel):
    logical_name: LogicalName
    registry_filename: Annotated[str, Field(min_length=1, max_length=256)]
    tier: ArtifactTier
    acquisition_url: HttpsUrl
    final_url: HttpsUrl
    registry_last_modified: Timestamp
    registry_size: NonNegativeInt
    http_last_modified: Metadata | None
    http_etag: Metadata | None
    http_version_id: Metadata | None
    http_content_length: NonNegativeInt | None
    sha256: Sha256Hex
    byte_count: NonNegativeInt
    retrieved_at: Timestamp
    embedded_marker: Annotated[str, Field(min_length=1, max_length=512)] | None
    embedded_created_at: Timestamp | None
    members: Annotated[
        tuple[MemberProvenance, ...],
        Field(min_length=0, max_length=10_000),
        BeforeValidator(json_array),
    ]
    parser: ParserIdentity | None
    transformation_sha256: Sha256Hex | None
    status: ArtifactStatus
    reason: ShortText | None
    imported_counts: ImportedCounts
    rejected_rows: NonNegativeInt
    validation_result: ValidationResult
    license_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")]
    coverage: Annotated[
        tuple[CoverageEntry, ...],
        Field(min_length=0, max_length=1000),
        BeforeValidator(json_array),
    ]

    @model_validator(mode="after")
    def consistent(self) -> ArtifactProvenance:
        if not _safe_relative(self.logical_name, data_prefix=True):
            raise ValueError("unsafe artifact logical name")
        if (
            "/" in self.registry_filename
            or self.registry_filename != self.logical_name.rsplit("/", 1)[1]
        ):
            raise ValueError("registry filename does not match logical name")
        if (self.parser is None) == (self.transformation_sha256 is None):
            raise ValueError("exactly one parser or transformation digest is required")
        member_keys = [item.path for item in self.members]
        coverage_keys = [(item.domain, item.operation_id) for item in self.coverage]
        if not _ordered_unique(self.members, member_keys) or not _ordered_unique(
            self.coverage, coverage_keys
        ):
            raise ValueError("artifact inventories must be sorted and unique")
        imported = self.imported_counts.rows + self.imported_counts.documents
        if self.embedded_created_at is not None and self.embedded_marker is None:
            raise ValueError("parsed embedded timestamp requires its raw marker")
        if self.status == "indexed":
            if (
                self.reason is not None
                or self.validation_result not in {"passed", "passed_with_warnings"}
                or imported == 0
                or not any(member.indexed for member in self.members)
            ):
                raise ValueError("indexed artifact state is contradictory")
        elif self.status == "quarantined":
            if (
                self.reason is None
                or self.validation_result != "failed"
                or imported
                or any(member.indexed for member in self.members)
            ):
                raise ValueError("quarantined artifact state is contradictory")
        elif self.reason is None or imported or any(member.indexed for member in self.members):
            raise ValueError("non-indexed artifact state is contradictory")
        return self


class SourceAnomaly(StrictModel):
    anomaly_id: AnomalyId
    affected_sources: Annotated[
        tuple[LogicalName, ...],
        Field(min_length=1, max_length=10_000),
        BeforeValidator(json_array),
    ]
    count: PositiveInt

    @model_validator(mode="after")
    def sources_ordered(self) -> SourceAnomaly:
        if not all(_safe_relative(item, data_prefix=True) for item in self.affected_sources):
            raise ValueError("unsafe anomaly source")
        if list(self.affected_sources) != sorted(set(self.affected_sources)):
            raise ValueError("anomaly sources must be sorted and unique")
        return self


class SourceManifest(StrictModel):
    """Frozen acquisition receipt, transformation identity, and honest coverage."""

    model_config = ConfigDict(**StrictModel.model_config, json_schema_extra=SCHEMA_METADATA)

    schema_version: SchemaVersion
    profile: Literal["core", "extended"]
    source_set_identity: Sha256Identity
    transformation: TransformationContract
    registry: RegistryObservation
    artifacts: Annotated[
        tuple[ArtifactProvenance, ...],
        Field(min_length=1, max_length=10_000),
        BeforeValidator(json_array),
    ]
    coverage: Annotated[
        tuple[CoverageEntry, ...],
        Field(min_length=0, max_length=10_000),
        BeforeValidator(json_array),
    ]
    anomalies: Annotated[
        tuple[SourceAnomaly, ...],
        Field(min_length=0, max_length=10_000),
        BeforeValidator(json_array),
    ]

    @model_validator(mode="after")
    def inventories_ordered(self) -> SourceManifest:
        artifact_keys = [item.logical_name for item in self.artifacts]
        coverage_keys = [(item.domain, item.operation_id) for item in self.coverage]
        anomaly_keys = [item.anomaly_id for item in self.anomalies]
        if not _ordered_unique(self.artifacts, artifact_keys):
            raise ValueError("artifacts must be sorted and unique")
        if not _ordered_unique(self.coverage, coverage_keys):
            raise ValueError("coverage must be sorted and unique")
        if not _ordered_unique(self.anomalies, anomaly_keys):
            raise ValueError("anomalies must be sorted and unique")
        return self


def parse_source_manifest(raw: bytes) -> SourceManifest:
    return parse_canonical(raw, maximum=MAX_SOURCE_MANIFEST_BYTES, model=SourceManifest)


def canonical_bytes(model: SourceManifest) -> bytes:
    return _canonical_bytes(model)


def source_manifest_schema_bytes() -> bytes:
    return schema_bytes(SourceManifest)


__all__ = [
    "ArtifactProvenance",
    "CoverageEntry",
    "SourceManifest",
    "canonical_bytes",
    "parse_source_manifest",
    "source_manifest_schema_bytes",
]
