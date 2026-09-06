"""Closed validation for dataset-filter recovery context."""

from __future__ import annotations

import json
from pathlib import Path

from clinpgx_link.data.coverage import known_filter_contracts


def _registered_dataset_ids() -> frozenset[str]:
    try:
        payload = json.loads(Path(__file__).with_name("coverage.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return frozenset()
    rows = payload.get("datasets") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return frozenset()
    identities: set[str] = set()
    for row in rows:
        dataset_id = row.get("dataset_id") if isinstance(row, dict) else None
        if not isinstance(dataset_id, str):
            return frozenset()
        identities.add(dataset_id)
    return frozenset(identities)


_REGISTERED_DATASET_IDS = _registered_dataset_ids()


def is_known_dataset_filter_contract(dataset_id: object, filters: object) -> bool:
    """Accept only a registry identity and one exact code-owned filter contract."""
    return (
        isinstance(dataset_id, str)
        and dataset_id in _REGISTERED_DATASET_IDS
        and type(filters) is tuple
        and all(isinstance(item, str) for item in filters)
        and filters in known_filter_contracts(dataset_id)
    )


__all__ = ["is_known_dataset_filter_contract"]
