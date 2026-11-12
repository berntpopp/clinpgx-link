"""Owner distribution authorization input and the rights evidence rendered from it."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases.contract_json import (
    LongText,
    SchemaVersion,
    Sha256Hex,
    ShortText,
    StrictModel,
    Timestamp,
    canonical_value_bytes,
)
from clinpgx_link.releases.licenses import LicensesManifest, canonical_bytes, parse_licenses

MAX_AUTHORIZATION_BYTES = 64 * 1024
LICENSE_ID = "clinpgx-cc-by-sa-4.0"


class DistributionAuthorization(StrictModel):
    """One human owner decision, bound to the exact upstream notice it covers."""

    schema_version: SchemaVersion
    reviewer: ShortText
    reviewed_at: Timestamp
    quote: LongText
    reference: ShortText
    notice_sha256: Sha256Hex
    interpretation: LongText
    public_rationale: LongText
    operator_local_rationale: LongText
    policy_obligation: LongText
    license_name: ShortText
    disclaimer: LongText


def parse_authorization(raw: bytes) -> DistributionAuthorization:
    if len(raw) > MAX_AUTHORIZATION_BYTES:
        raise DataValidationError("Distribution authorization exceeds its byte bound")
    try:
        return DistributionAuthorization.model_validate_json(raw)
    except ValidationError as exc:
        raise DataValidationError("Distribution authorization is invalid") from exc


def render_licenses(
    authorization: DistributionAuthorization, notice: str, artifacts: Sequence[str]
) -> tuple[bytes, LicensesManifest]:
    """Render canonical rights evidence; the notice text is the archives' own LICENSE.txt."""
    review = {"reviewed_at": authorization.reviewed_at, "reviewer": authorization.reviewer}
    value = {
        "schema_version": 1,
        "licenses": [
            {
                "license_id": LICENSE_ID,
                "name": "Creative Commons Attribution-ShareAlike 4.0 International",
                "spdx_expression": "CC-BY-SA-4.0",
                "urls": [
                    "https://creativecommons.org/licenses/by-sa/4.0/",
                    "https://www.clinpgx.org/whatIsPharmgkb/gsi",
                ],
                "notice": {"text": notice, "sha256": None, "reference": None},
                "obligations": [
                    {
                        "kind": "attribution",
                        "text": "Preserve the attribution and license link required by the "
                        "embedded LICENSE.txt.",
                    },
                    {
                        "kind": "change_indication",
                        "text": "Indicate modifications when redistributing adapted material.",
                    },
                    {"kind": "policy", "text": authorization.policy_obligation},
                    {
                        "kind": "share_alike",
                        "text": "If distributing adapted material, apply the same license as "
                        "required by the embedded LICENSE.txt.",
                    },
                ],
                "distribution": {
                    "public": {
                        "allowed": True,
                        **review,
                        "rationale": authorization.public_rationale,
                    },
                    "controlled": {
                        "allowed": False,
                        "reviewed_at": None,
                        "reviewer": None,
                        "rationale": None,
                    },
                    "operator_local": {
                        "allowed": True,
                        **review,
                        "rationale": authorization.operator_local_rationale,
                    },
                },
                "affected_artifacts": sorted(artifacts),
                "needs_confirmation": False,
            }
        ],
    }
    rights = parse_licenses(canonical_value_bytes(value))
    return canonical_bytes(rights), rights


__all__ = ["DistributionAuthorization", "parse_authorization", "render_licenses"]
