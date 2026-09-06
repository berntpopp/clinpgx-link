"""Repository guard and metadata for candidate-bound gene alias membership."""

from __future__ import annotations

import sqlite3
from typing import Any

from clinpgx_link.data.gene_alias_membership import (
    GENE_ALIAS_DATASET,
    GENE_ALIAS_FIELDS,
    GENE_ALIAS_MEMBER,
    GENE_ALIAS_MEMBERSHIP_LIMITATION,
    LoadedGeneAliasMembership,
    load_gene_alias_membership,
)
from clinpgx_link.exceptions import UpstreamUnavailableError


class RepositoryGeneAliasSupport:
    """Mixin that loads compatibility once and guards only affected lookup paths."""

    _connection: sqlite3.Connection
    _snapshot_id: str
    _gene_alias_membership: LoadedGeneAliasMembership
    _has_gene_alias_member: bool

    def _load_gene_alias_membership(self) -> None:
        has_member_inventory = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_member'"
        ).fetchone()
        self._has_gene_alias_member = bool(
            has_member_inventory is not None
            and self._connection.execute(
                "SELECT 1 FROM source_member WHERE dataset_id=? AND path=?",
                (GENE_ALIAS_DATASET, GENE_ALIAS_MEMBER),
            ).fetchone()
            is not None
        )
        receipt = self._connection.execute(
            "SELECT value FROM metadata WHERE key='gene_alias_membership_json'"
        ).fetchone()
        self._gene_alias_membership = load_gene_alias_membership(
            str(receipt[0]) if receipt is not None else None, self._snapshot_id
        )

    def _require_gene_alias_membership(self) -> None:
        if self._has_gene_alias_member and not self._gene_alias_membership.compatible:
            raise UpstreamUnavailableError(
                "Installed gene alias membership is incompatible with this application.",
                subtype="membership_profile_mismatch",
            )

    def _guard_dataset_gene_alias_membership(
        self,
        dataset_id: str,
        member: str | None,
        match: str,
        canonical_filters: dict[str, str],
        source_filters: dict[str, str],
    ) -> None:
        if (
            match != "member"
            or dataset_id != GENE_ALIAS_DATASET
            or member not in {None, GENE_ALIAS_MEMBER}
        ):
            return
        canonical_alias = bool({"gene", "name"} & canonical_filters.keys())
        source_alias = member == GENE_ALIAS_MEMBER and bool(
            GENE_ALIAS_FIELDS & source_filters.keys()
        )
        if canonical_alias or source_alias:
            self._require_gene_alias_membership()

    def _guard_unscoped_gene_alias_membership(
        self, entity_type: str, filters: dict[str, str]
    ) -> None:
        if entity_type == "gene" and {"gene", "name"} & filters.keys():
            self._require_gene_alias_membership()

    def _gene_alias_field_metadata(
        self, dataset_id: str, member: str, fields: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if (
            dataset_id != GENE_ALIAS_DATASET
            or member != GENE_ALIAS_MEMBER
            or self._gene_alias_membership.compatible
        ):
            return fields
        result: list[dict[str, Any]] = []
        for field in fields:
            value = dict(field)
            if value.get("name") in GENE_ALIAS_FIELDS:
                value["match_modes"] = ["exact"]
                value["tokenizer"] = None
            result.append(value)
        return result

    def _gene_alias_metadata(self, dataset_id: str, member: str | None = None) -> dict[str, str]:
        if dataset_id != GENE_ALIAS_DATASET or (member is not None and member != GENE_ALIAS_MEMBER):
            return {}
        result: dict[str, str] = {
            "gene_alias_membership_status": self._gene_alias_membership.status
        }
        if not self._gene_alias_membership.compatible:
            result["gene_alias_membership_limitation"] = GENE_ALIAS_MEMBERSHIP_LIMITATION
        return result


__all__ = ["RepositoryGeneAliasSupport"]
