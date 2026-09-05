"""Read exact retained source assets from a caller-owned immutable repository."""

from __future__ import annotations

import hashlib

from clinpgx_link.content.assets import AssetReference
from clinpgx_link.content.reader import read_content
from clinpgx_link.data.repository import DatasetRepository
from clinpgx_link.exceptions import DataValidationError, InvalidInputError, RecoverableContentError
from clinpgx_link.models import SourceResponse


def read_repository_content(
    repository: DatasetRepository,
    content_ref: str,
    *,
    pointer: str,
    representation: str,
    start: int,
    length: int,
) -> SourceResponse:
    reference = AssetReference.decode(content_ref)
    if representation == "base64":
        response = repository.read_asset(
            reference.dataset_id,
            member=reference.member,
            start=start,
            length=length,
            expected_snapshot=reference.snapshot_id,
        )
        value = response.value
        if start > value["total_bytes"]:
            raise InvalidInputError("Start exceeds source byte length.", field="start")
        if response.source.sha256 != reference.sha256:
            raise DataValidationError("Retained source identity does not match its reference.")
        if pointer:
            raise RecoverableContentError(content_ref=content_ref)
        payload = {
            "source_sha256": response.source.sha256,
            "sha256": response.source.sha256,
            "media_type": value["media_type"],
            "pointer": "",
            "representation": "base64",
            "start": start,
            "returned": value["returned"],
            "total": value["total_bytes"],
            "has_more": value["next_offset"] is not None,
            "next_start": value["next_offset"],
            "unit": "bytes",
            "derived": False,
            "base64": value["data"],
        }
    else:
        response = repository.asset_content(
            reference.dataset_id,
            member=reference.member,
            expected_snapshot=reference.snapshot_id,
        )
        if hashlib.sha256(response.value).hexdigest() != reference.sha256:
            raise DataValidationError("Retained source bytes do not match their reference.")
        payload = read_content(
            response.value,
            media_type=response.details["media_type"],
            pointer=pointer,
            representation=representation,
            start=start,
            length=length,
        )
    if response.source.sha256 != reference.sha256:
        raise DataValidationError("Retained source identity does not match its reference.")
    payload.update(
        content_ref=content_ref,
        snapshot_id=reference.snapshot_id,
        offline_available=True,
        expires_at=None,
        retention="while_snapshot_retained",
    )
    return SourceResponse(payload, response.source, response.details)
