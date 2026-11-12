"""Opt-in, caller-controlled shaping over one retained live API row list (issue #2).

Shaping never runs without an explicit ``shape`` argument. It reorders or drops rows of the
whole retained upstream response, never one page of it, and every row keeps its original
``row_index``/``source_pointer`` so it stays traceable to the unshaped source bytes.
"""

# ruff: noqa: N815 -- enum members spell the upstream camelCase row keys verbatim.
from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from clinpgx_link.exceptions import InvalidInputError
from clinpgx_link.mcp.untrusted_content import fence_text
from clinpgx_link.models import SourceResponse


# Closed top-level keys seen in live connectedObjects, pair and data-search rows; the schema
# enumerates them so it is never more permissive than runtime.
class ShapeKey(StrEnum):
    accessionId = "accessionId"
    alleleGenotype = "alleleGenotype"
    allelePhenotypes = "allelePhenotypes"
    connectedObject = "connectedObject"
    connectionTypes = "connectionTypes"
    id = "id"
    levelOfEvidence = "levelOfEvidence"
    literature = "literature"
    location = "location"
    name = "name"
    pediatric = "pediatric"
    phenotypeCategories = "phenotypeCategories"
    phenotypes = "phenotypes"
    relatedChemicals = "relatedChemicals"
    relatedDiseases = "relatedDiseases"
    relatedGenes = "relatedGenes"
    score = "score"
    sentence = "sentence"
    significance = "significance"
    source = "source"
    types = "types"


FilterValue = Annotated[str, Field(max_length=128)] | float | bool
# Dict/list values compare by their first labelled scalar, so levelOfEvidence -> "1A".
_LABELS = ("term", "name", "symbol", "displayName", "id")
_MAX_ATOMS = 256
_MAX_FACETS = 25


def _drop_null_defaults(schema: dict[str, Any]) -> None:
    for prop in schema["properties"].values():
        if prop.get("default", "") is None:
            del prop["default"]


class Shape(BaseModel):
    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, json_schema_extra=_drop_null_defaults
    )

    # SkipJsonSchema keeps the advertised schema free of null branches (Tool-Surface Budget).
    filter: (
        Annotated[
            dict[ShapeKey, Annotated[list[FilterValue], Field(min_length=1, max_length=20)]],
            Field(max_length=4),
        ]
        | SkipJsonSchema[None]
    ) = None
    sort: ShapeKey | SkipJsonSchema[None] = None
    order: Literal["asc", "desc"] = "asc"
    fields: Annotated[list[ShapeKey], Field(min_length=1, max_length=12)] | SkipJsonSchema[None] = (
        None
    )
    facet: ShapeKey | SkipJsonSchema[None] = None

    def selector(self) -> dict[str, Any]:
        """Cursor-bound shape; empty when no operation was requested."""
        return self.model_dump(exclude_none=True) if self.applied() else {}

    def applied(self) -> list[str]:
        return [op for op in ("filter", "sort", "fields", "facet") if getattr(self, op)]


ShapeArg = Annotated[
    Shape | SkipJsonSchema[None],
    Field(
        description=(
            "Opt-in, API only: shapes the whole row list fetched with filters, not just this "
            "page. filter: AND across keys, OR within; dicts match term/name, lists any item; "
            "case-insensitive. sort: stable, missing last. fields: projection. facet: "
            "counts. See _meta.shaping."
        ),
        examples=[{"filter": {"levelOfEvidence": ["1A"]}, "sort": "score", "order": "desc"}],
    ),
]


def require_api_rows(shape: Shape | None) -> None:
    """Local snapshot reads page in SQL; shaping them would silently shape one page only."""
    if shape is not None and shape.applied():
        raise InvalidInputError(
            "Shaping needs a retained API row list.",
            field="shape",
            subtype="shape_requires_api_rows",
        )


def _atom(value: Any) -> tuple[int, Any, str] | None:
    if isinstance(value, dict):
        value = next((value[k] for k in _LABELS if k in value and value[k] is not None), None)
    if isinstance(value, bool):
        return (0, value, str(value).lower())
    if isinstance(value, int | float):
        return (1, float(value), str(value))
    if isinstance(value, str):
        return (2, value.casefold(), value)
    return None


def _atoms(row: Any, key: str) -> list[tuple[int, Any, str]] | None:
    """Comparable values of one top-level key; None when the row has no such key."""
    if not isinstance(row, dict) or key not in row:
        return None
    values = row[key] if isinstance(row[key], list) else [row[key]]
    return [atom for atom in map(_atom, values[:_MAX_ATOMS]) if atom is not None]


def shape_rows(
    response: SourceResponse, shape: Shape
) -> tuple[list[int], tuple[str, ...] | None, dict[str, Any]]:
    """Return shaped original indices, the projection and the transparent ``_meta.shaping``."""
    rows = response.value
    if not isinstance(rows, list):
        raise InvalidInputError("Shaping needs a row list.", field="shape")
    keys = {*(shape.filter or {}), *(shape.fields or ()), shape.sort, shape.facet} - {None}
    absent = sorted(k for k in keys if all(_atoms(row, k) is None for row in rows))
    order = list(range(len(rows)))
    meta: dict[str, Any] = {
        "applied": shape.applied(),
        "scope": "whole_retained_upstream_response",
        "rows_before": len(rows),
    }
    if shape.filter:
        wanted = {k: {a[:2] for v in vs if (a := _atom(v))} for k, vs in shape.filter.items()}
        hits = {
            k: {i for i in order if {a[:2] for a in _atoms(rows[i], k) or ()} & want}
            for k, want in wanted.items()
        }
        meta["filter_matches"] = {k: len(v) for k, v in hits.items()}
        order = [i for i in order if all(i in v for v in hits.values())]
    if shape.sort:
        keyed = [(atoms[0][:2], i) for i in order if (atoms := _atoms(rows[i], shape.sort))]
        # Python's sort is stable even reversed: ties keep upstream order.
        keyed.sort(key=lambda pair: pair[0], reverse=shape.order == "desc")
        ranked = {i for _key, i in keyed}
        order = [i for _key, i in keyed] + [i for i in order if i not in ranked]
    if shape.facet:
        counts: Counter[tuple[int, Any]] = Counter()
        labels: dict[tuple[int, Any], str] = {}
        for i in order:
            for atom in {a[:2]: a for a in _atoms(rows[i], shape.facet) or ()}.values():
                counts[atom[:2]] += 1
                labels.setdefault(atom[:2], atom[2])
        top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:_MAX_FACETS]
        reference = response.details["content_ref"]
        meta["facets"] = {
            "field": shape.facet,
            "values": [
                {
                    "value": fence_text(labels[v], source=response.source, record_id=reference),
                    "count": n,
                }
                for v, n in top
            ],
            "distinct_values": len(counts),
            "truncated": len(counts) > _MAX_FACETS,
        }
    meta.update(rows_after=len(order), absent_fields=absent)
    return order, tuple(shape.fields) if shape.fields else None, meta


__all__ = ["Shape", "ShapeArg", "ShapeKey", "require_api_rows", "shape_rows"]
