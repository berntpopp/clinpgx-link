"""Strict fleet release manifest and independently trusted byte admission.

Model structure adapted from GeneFoundry Router release/data.py at
6568a0ad7d68925440aba550a2a678282ea5bb6b, MIT, Copyright 2026 Bernt Popp.
The unchanged schema and license are retained in vendor/genefoundry.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    model_validator,
)

from clinpgx_link.exceptions import DataValidationError

MAX_MANIFEST_BYTES = 1024 * 1024
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SemanticVersion = Annotated[
    str, Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


def _https(value: AnyHttpUrl) -> AnyHttpUrl:
    if value.scheme != "https":
        raise ValueError("HTTPS required")
    return value


def _timestamp(value: str) -> str:
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})",
        value,
    ):
        raise ValueError("RFC 3339 timestamp required")
    datetime.fromisoformat(value)
    return value


def _immutable_tag(value: str) -> str:
    if value.lower() in {"latest", "current", "stable", "main", "master", "head"}:
        raise ValueError("Immutable release required")
    return value


HttpsUrl = Annotated[AnyHttpUrl, AfterValidator(_https)]
Timestamp = Annotated[str, AfterValidator(_timestamp)]
ReleaseTag = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"), AfterValidator(_immutable_tag)
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class UpstreamSource(StrictModel):
    identifier: Annotated[str, Field(min_length=1, max_length=256)]
    url: HttpsUrl
    retrieved_at: Timestamp
    sha256: Sha256Hex
    etag: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    last_modified: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class DatasetIdentity(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=256)]
    release: ReleaseTag
    source: UpstreamSource


class TransformationIdentity(StrictModel):
    repository: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})/[A-Za-z0-9_.-]{1,100}$")
    ]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


def _version(value: str) -> tuple[int, ...]:
    if re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value) is None:
        raise ValueError("Invalid semantic version")
    return tuple(int(part) for part in value.split("."))


class CompatibilityRange(StrictModel):
    minimum: SemanticVersion
    maximum: SemanticVersion

    @model_validator(mode="after")
    def ordered(self) -> CompatibilityRange:
        if _version(self.minimum) > _version(self.maximum):
            raise ValueError("Compatibility range is reversed")
        return self

    def contains(self, version: str) -> bool:
        return _version(self.minimum) <= _version(version) <= _version(self.maximum)


class SchemaIdentity(CompatibilityRange):
    actual: SemanticVersion

    @model_validator(mode="after")
    def compatible(self) -> SchemaIdentity:
        if not self.contains(self.actual):
            raise ValueError("Actual schema is outside compatibility range")
        return self


class ArtifactIdentity(StrictModel):
    filename: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")]
    sha256: Sha256Hex
    compressed_size: PositiveInt
    max_compressed_size: PositiveInt
    expanded_tree_sha256: Sha256Hex
    expanded_size: PositiveInt
    max_expanded_size: PositiveInt
    member_count: PositiveInt
    max_members: PositiveInt

    @model_validator(mode="after")
    def bounded(self) -> ArtifactIdentity:
        if (
            self.compressed_size > self.max_compressed_size
            or self.expanded_size > self.max_expanded_size
            or self.member_count > self.max_members
        ):
            raise ValueError("Artifact exceeds declared limits")
        return self


class LicenseEvidence(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=256)]
    url: HttpsUrl
    redistribution_allowed: StrictBool
    reviewed_at: Timestamp
    reviewer: Annotated[str, Field(min_length=1, max_length=256)]


class DataReleaseManifest(StrictModel):
    schema_version: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    dataset: DatasetIdentity
    transformation: TransformationIdentity
    schema_identity: SchemaIdentity = Field(alias="schema", serialization_alias="schema")
    record_counts: Annotated[dict[str, NonNegativeInt], Field(min_length=1)]
    artifact: ArtifactIdentity
    license: LicenseEvidence
    previous_known_good_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    application_compatibility: CompatibilityRange
    disclaimer: Annotated[str, Field(min_length=20, max_length=1000)]

    def validate_publication(self) -> None:
        if not self.license.redistribution_allowed:
            raise DataValidationError(
                "Public distribution is not approved", subtype="publication_not_allowed"
            )


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest key")
        result[key] = value
    return result


def _invalid_number(value: str) -> None:
    raise ValueError("Nonfinite manifest number")


def validate_manifest(
    raw: bytes, expected_sha256: str, *, public: bool = False
) -> DataReleaseManifest:
    """Authenticate exact bounded bytes before decoding any manifest-controlled value.

    The supplied digest must come from an independent trusted operator/deployment
    input, not from a checksum file downloaded beside an untrusted manifest.
    This validates the outer fleet contract only, not artifact bytes or rights files.
    """
    if len(raw) > MAX_MANIFEST_BYTES:
        raise DataValidationError("Manifest exceeds byte limit", subtype="resource_limit")
    if (
        not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)
    ):
        raise DataValidationError("Manifest digest mismatch", subtype="manifest_digest")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_invalid_number
        )
        manifest = DataReleaseManifest.model_validate(value)
    except (ValueError, UnicodeError, RecursionError, ValidationError) as exc:
        raise DataValidationError(
            "Manifest contract is invalid", subtype="manifest_invalid"
        ) from exc
    if public:
        manifest.validate_publication()
    return manifest
