"""Developer-owned field profiles for safely shaping indexed dataset rows."""

from __future__ import annotations

import re
from math import isfinite
from typing import Any

from clinpgx_link.data.record_profiles import profile_for_row

_STAR_ALLELE = r"\*[0-9]+[A-Za-z]?(?:x(?:[0-9]+|≥[0-9]+))?"
_NUCLEOTIDE_CHANGE = (
    r"[0-9]+(?:[-+][0-9]+)?(?:_[0-9]+)?"
    r"(?:[ACGT]>(?:[ACGT]|del(?:\+C[nN])?)|(?:delins|del|dup|ins)[ACGT]*)"
)
_CODING_ALLELE = (
    rf"c\.{_NUCLEOTIDE_CHANGE}(?:, c\.{_NUCLEOTIDE_CHANGE})?"
    rf"(?: \((?:{_STAR_ALLELE}|HapB[0-9]+)\))?"
)
_PHARMCAT_ALLELE_KEY = re.compile(
    rf"(?:Reference|{_STAR_ALLELE}(?: ?\+ ?{_STAR_ALLELE})*|{_CODING_ALLELE})"
)


def _is_pharmcat_diplotype_row(row: dict[str, Any]) -> bool:
    profile = profile_for_row(
        str(row.get("dataset_id", "")),
        str(row.get("member", "")),
        row.get("json_pointer") if isinstance(row.get("json_pointer"), str) else None,
    )
    return profile is not None and profile.profile_id == "pharmcat.diplotype.v1"


def trusted_fields_for_row(row: dict[str, Any]) -> frozenset[str]:
    """Return only a profile shipped by this program, never source metadata."""
    profile = profile_for_row(
        str(row.get("dataset_id", "")),
        str(row.get("member", "")),
        row.get("json_pointer") if isinstance(row.get("json_pointer"), str) else None,
    )
    return profile.trusted_fields if profile is not None else frozenset()


def profiled_nested_fields_are_safe(row: dict[str, Any]) -> bool:
    """Validate the sole profiled dynamic-key map before allowing inline output."""
    if not _is_pharmcat_diplotype_row(row):
        return True
    profile = profile_for_row(
        str(row.get("dataset_id", "")),
        str(row.get("member", "")),
        str(row["json_pointer"]),
    )
    assert profile is not None
    required = frozenset(profile.required_fields)
    fields = row.get("fields")
    if (
        not isinstance(fields, dict)
        or not required <= set(fields) <= profile.trusted_fields
        or any(
            not isinstance(fields[name], str) or len(fields[name]) > 512
            for name in required - {"diplotypekey"}
        )
    ):
        return False
    key = fields.get("diplotypekey")
    activity_score = fields.get("activityScore")
    return _safe_diplotype_key(key) and (
        activity_score is None
        or (
            isinstance(activity_score, (str, int, float))
            and not isinstance(activity_score, bool)
            and (not isinstance(activity_score, str) or len(activity_score) <= 128)
            and (not isinstance(activity_score, float) or isfinite(activity_score))
        )
    )


def _safe_diplotype_key(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and 1 <= len(value) <= 2
        and all(
            isinstance(name, str)
            and _PHARMCAT_ALLELE_KEY.fullmatch(name) is not None
            and type(count) is int
            and count in {1, 2}
            for name, count in value.items()
        )
    )


def profiled_selected_nested_value_is_safe(
    row: dict[str, Any], field_name: str, value: Any
) -> bool:
    """Authorize only the declared PharmCAT dynamic-key map in isolation."""
    if not isinstance(value, (dict, list)):
        return True
    return (
        _is_pharmcat_diplotype_row(row)
        and field_name == "diplotypekey"
        and _safe_diplotype_key(value)
    )


__all__ = [
    "profiled_nested_fields_are_safe",
    "profiled_selected_nested_value_is_safe",
    "trusted_fields_for_row",
]
