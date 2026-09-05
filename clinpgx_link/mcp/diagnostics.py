"""Safe operational diagnostics, separate from release acceptance/readiness proof."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from pydantic import Field

from clinpgx_link.api.website import WebsiteClient
from clinpgx_link.data.catalog import is_canonical_release_tag
from clinpgx_link.data.repository import DatasetRepository
from clinpgx_link.exceptions import ClinPGxError
from clinpgx_link.mcp.admission import Admission, run_sync
from clinpgx_link.mcp.envelope import error_result, success_result
from clinpgx_link.models import SourceInfo
from clinpgx_link.services.api import ApiService


def _local_status(repository: DatasetRepository | None) -> dict[str, Any]:
    if repository is None:
        return {"ready": False, "reason": "not_configured"}
    try:
        status = repository.status()
        identity = status.get("snapshot_id")
        release_tag = status.get("release_tag")
        if not isinstance(identity, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", identity) is None:
            return {"ready": False, "reason": "invalid_identity"}
        if not is_canonical_release_tag(release_tag):
            return {"ready": False, "reason": "invalid_identity"}
        datasets = repository.list_datasets().value
        return {
            "ready": bool(datasets),
            "snapshot_id": identity,
            "release_tag": release_tag,
            "dataset_count": len(datasets),
            "scope": "repository_handle_not_release_validation",
        }
    except Exception:
        return {"ready": False, "reason": "unavailable"}


def register_diagnostics(
    server: FastMCP,
    api: ApiService | None,
    website: WebsiteClient | None,
    repository: DatasetRepository | None,
    admission: Admission | None = None,
) -> None:
    @server.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        tags={"metadata"},
        output_schema=None,
    )
    async def get_diagnostics(
        probe_upstream: Annotated[
            bool, Field(description="Request fixed gene evidence; cached hits are identified.")
        ] = False,
        response_mode: Annotated[
            Literal["minimal", "compact", "standard", "full"],
            Field(description="Response detail mode."),
        ] = "compact",
    ) -> ToolResult:
        """Inspect source configuration and pinned data without revealing local paths or secrets."""
        try:
            probe: dict[str, Any] = {"status": "not_requested"}
            if probe_upstream:
                if api is None:
                    probe = {"status": "not_configured"}
                else:
                    try:
                        evidence = await api.get("gene", "PA124", view="min")
                        probe = {
                            "status": "cached_evidence"
                            if evidence.source.data_source == "cache"
                            else "reachable",
                            "source_sha256": evidence.source.sha256,
                            "retrieved_at": evidence.source.retrieved_at,
                            "content_ref": evidence.details["content_ref"],
                        }
                    except ClinPGxError as exc:
                        probe = {"status": "unavailable", "error_code": exc.error_code}
            result = {
                "api_configured": api is not None,
                "website_configured": website is not None,
                "local_snapshot": await run_sync(_local_status, repository),
                "upstream_probe": probe,
                "implementation_status": "in_progress",
                "response_mode": response_mode,
                "admission": admission.snapshot() if admission is not None else None,
            }
            raw = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
            source = SourceInfo(
                "ClinPGx Link diagnostics",
                "clinpgx://diagnostics",
                datetime.now(UTC).isoformat(),
                hashlib.sha256(raw).hexdigest(),
                "server",
                coverage="operational_status_not_source_completeness",
            )
            return success_result(result, source=source)
        except Exception:
            return error_result(ClinPGxError("Diagnostics unavailable."))
