"""Strict per-artifact rights evidence and fail-closed distribution gates."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import BeforeValidator, ConfigDict, Field, StrictBool, model_validator

from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases.contract_json import (
    SCHEMA_METADATA,
    HttpsUrl,
    LongText,
    SchemaVersion,
    Sha256Hex,
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
from clinpgx_link.releases.identity import ReleaseIdentity, release_identity
from clinpgx_link.releases.source_manifest import SourceManifest

MAX_LICENSES_BYTES = 1024 * 1024
DistributionMode = Literal["public", "controlled", "operator_local"]
ObligationKind = Literal["attribution", "citation", "change_indication", "policy", "share_alike"]


class NoticeEvidence(StrictModel):
    text: LongText | None
    sha256: Sha256Hex | None
    reference: HttpsUrl | None

    @model_validator(mode="after")
    def exact_form(self) -> NoticeEvidence:
        text_form = self.text is not None and self.sha256 is None and self.reference is None
        digest_form = self.text is None and self.sha256 is not None and self.reference is not None
        if not (text_form or digest_form):
            raise ValueError("notice requires exact text or digest plus immutable reference")
        return self


class Obligation(StrictModel):
    kind: ObligationKind
    text: LongText


class ReviewDecision(StrictModel):
    allowed: StrictBool
    reviewed_at: Timestamp | None
    reviewer: ShortText | None
    rationale: LongText | None

    @model_validator(mode="after")
    def evidence_complete(self) -> ReviewDecision:
        evidence = (self.reviewed_at, self.reviewer, self.rationale)
        complete = all(item is not None for item in evidence)
        empty = all(item is None for item in evidence)
        if self.allowed and not complete:
            raise ValueError("approval requires complete review evidence")
        if not self.allowed and not (complete or empty):
            raise ValueError("review evidence must be complete or explicitly absent")
        return self


class DistributionReviews(StrictModel):
    public: ReviewDecision
    controlled: ReviewDecision
    operator_local: ReviewDecision

    def decision(self, mode: DistributionMode) -> ReviewDecision:
        if mode == "public":
            return self.public
        if mode == "controlled":
            return self.controlled
        return self.operator_local


class LicenseRecord(StrictModel):
    license_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")]
    name: ShortText
    spdx_expression: Annotated[str, Field(min_length=1, max_length=512)] | None
    urls: Annotated[
        tuple[HttpsUrl, ...], BeforeValidator(json_array), Field(min_length=1, max_length=32)
    ]
    notice: NoticeEvidence
    obligations: Annotated[
        tuple[Obligation, ...], BeforeValidator(json_array), Field(max_length=100)
    ]
    distribution: DistributionReviews
    affected_artifacts: Annotated[
        tuple[Annotated[str, Field(min_length=6, max_length=512)], ...],
        BeforeValidator(json_array),
        Field(min_length=1, max_length=10_000),
    ]
    needs_confirmation: StrictBool

    @model_validator(mode="after")
    def consistent(self) -> LicenseRecord:
        if list(self.urls) != sorted(set(self.urls)):
            raise ValueError("license URLs must be sorted and unique")
        obligation_keys = [(item.kind, item.text) for item in self.obligations]
        if obligation_keys != sorted(set(obligation_keys)):
            raise ValueError("obligations must be sorted and unique")
        if list(self.affected_artifacts) != sorted(set(self.affected_artifacts)) or any(
            not _artifact_name(item) for item in self.affected_artifacts
        ):
            raise ValueError("affected artifacts must be safe, sorted and unique")
        if self.needs_confirmation and (
            self.distribution.public.allowed or self.distribution.controlled.allowed
        ):
            raise ValueError("unconfirmed rights cannot approve public or controlled distribution")
        return self


def _artifact_name(value: str) -> bool:
    return value.startswith("data/") and not (
        value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


class LicensesManifest(StrictModel):
    """Reviewed evidence records; no aggregate legal conclusion is inferred."""

    model_config = ConfigDict(**StrictModel.model_config, json_schema_extra=SCHEMA_METADATA)

    schema_version: SchemaVersion
    licenses: Annotated[
        tuple[LicenseRecord, ...],
        BeforeValidator(json_array),
        Field(min_length=1, max_length=10_000),
    ]

    @model_validator(mode="after")
    def licenses_ordered(self) -> LicensesManifest:
        keys = [item.license_id for item in self.licenses]
        if keys != sorted(set(keys)):
            raise ValueError("licenses must be sorted and unique")
        return self


def parse_licenses(raw: bytes) -> LicensesManifest:
    return parse_canonical(raw, maximum=MAX_LICENSES_BYTES, model=LicensesManifest)


def canonical_bytes(model: LicensesManifest) -> bytes:
    return _canonical_bytes(model)


def licenses_schema_bytes() -> bytes:
    return schema_bytes(LicensesManifest)


def validate_distribution(
    source: SourceManifest, rights: LicensesManifest, mode: DistributionMode
) -> None:
    """Require exact bidirectional rights mapping and affirmative per-mode approval."""
    if mode not in {"public", "controlled", "operator_local"}:
        raise DataValidationError("Unknown distribution mode.", subtype="rights_invalid")
    artifacts = {item.logical_name: item for item in source.artifacts}
    licenses = {item.license_id: item for item in rights.licenses}
    affected: dict[str, str] = {}
    for license_record in rights.licenses:
        for name in license_record.affected_artifacts:
            if name not in artifacts or name in affected:
                raise DataValidationError(
                    "Rights evidence does not map exactly to retained artifacts.",
                    subtype="rights_invalid",
                )
            affected[name] = license_record.license_id
    for name, artifact in artifacts.items():
        mapped_license = licenses.get(artifact.license_id)
        if (
            mapped_license is None
            or affected.get(name) != artifact.license_id
            or not mapped_license.distribution.decision(mode).allowed
        ):
            raise DataValidationError(
                "Distribution lacks affirmative artifact rights evidence.",
                subtype="publication_not_allowed",
            )


def validate_source_identity(source: SourceManifest, rights: LicensesManifest) -> ReleaseIdentity:
    """Recompute the stable key from every approved identity input and compare it."""
    rights_sha256 = hashlib.sha256(canonical_bytes(rights)).hexdigest()
    identity = release_identity(
        profile=source.profile,
        sources=[(item.logical_name, item.sha256) for item in source.artifacts],
        transformation_sha256=source.transformation.sha256,
        schema_version=source.transformation.schema_version,
        licenses_sha256=rights_sha256,
    )
    if identity.source_set_identity != source.source_set_identity:
        raise DataValidationError(
            "Source manifest stable identity does not match its contracts.",
            subtype="release_identity_mismatch",
        )
    return identity


__all__ = [
    "LicensesManifest",
    "canonical_bytes",
    "licenses_schema_bytes",
    "parse_licenses",
    "validate_distribution",
    "validate_source_identity",
]
