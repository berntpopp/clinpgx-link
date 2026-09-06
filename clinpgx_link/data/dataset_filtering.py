"""Validate dataset/member filters and build source-field query clauses."""

from __future__ import annotations

import json
from typing import Any

from clinpgx_link.data.coverage import field_metadata, known_filters
from clinpgx_link.data.search_diagnostics import CANONICAL_FILTERS
from clinpgx_link.exceptions import DatasetFilterError


def validated_dataset_filters(
    dataset_id: str,
    member: str | None,
    headers_json: object,
    selected_filters: dict[str, str],
    match: str,
) -> tuple[dict[str, str], dict[str, str], list[str], list[Any]]:
    """Return canonical filters plus safe clauses for declared source fields."""
    supported = known_filters(dataset_id, member)
    recovery_filters = tuple(sorted(supported))
    canonical_filters = {
        key: value for key, value in selected_filters.items() if key in CANONICAL_FILTERS
    }
    source_filters = {
        key: value for key, value in selected_filters.items() if key not in CANONICAL_FILTERS
    }
    if set(canonical_filters) - supported:
        raise DatasetFilterError(dataset_id=dataset_id, known_filters=recovery_filters)
    if not source_filters:
        return canonical_filters, source_filters, [], []
    if member is None or not isinstance(headers_json, str) or not headers_json:
        raise DatasetFilterError(dataset_id=dataset_id, known_filters=recovery_filters)
    headers = json.loads(headers_json)
    if not isinstance(headers, list) or not all(isinstance(item, str) for item in headers):
        raise DatasetFilterError(dataset_id=dataset_id, known_filters=recovery_filters)
    metadata = {
        str(item["name"]): item for item in field_metadata(dataset_id, member, tuple(headers))
    }
    clauses: list[str] = []
    parameters: list[Any] = []
    for key, value in source_filters.items():
        declared = metadata.get(key)
        if declared is None:
            raise DatasetFilterError(dataset_id=dataset_id, known_filters=recovery_filters)
        if match == "member":
            if "member" not in declared["match_modes"]:
                raise DatasetFilterError(dataset_id=dataset_id, known_filters=recovery_filters)
            clauses.append(
                "EXISTS (SELECT 1 FROM membership source_value "
                "WHERE source_value.record_pk=r.record_pk "
                "AND source_value.source_field=? AND source_value.value=? "
                "AND source_value.match_mode='member')"
            )
        else:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(r.fields_json) source_value "
                "WHERE source_value.key=? AND source_value.type='text' "
                "AND source_value.value=?)"
            )
        parameters.extend([key, value])
    return canonical_filters, source_filters, clauses, parameters


__all__ = ["validated_dataset_filters"]
