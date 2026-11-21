"""Star-allele names (CYP2D6*10, TPMT *3A, *1/*10) inside an allele text query (#57)."""

from __future__ import annotations

import re

from clinpgx_link.exceptions import InvalidInputError

_DESIGNATION = r"\*[0-9A-Za-z:.]{1,16}"
# Optional gene symbol, then one allele or one diplotype.
STAR_TOKEN = re.compile(rf"([A-Za-z][A-Za-z0-9-]{{0,19}})?({_DESIGNATION}(?:/{_DESIGNATION})?)")


class StarAlleleQueryError(InvalidInputError):
    """One query names several star alleles; each needs its own search."""

    def __init__(self, alleles: tuple[str, ...]) -> None:
        super().__init__(
            "A query can name one star allele or diplotype",
            field="query",
            hint="Search each star allele separately.",
            subtype="multiple_star_alleles",
        )
        self.alleles = alleles


def split_star_allele(query: str) -> tuple[str | None, tuple[str, str]] | None:
    """Return (remaining text query, two exact allele values), or None to leave the query."""
    words = query.split()
    starred = [word for word in words if "*" in word]
    matches = [STAR_TOKEN.fullmatch(word) for word in starred]
    if not starred or None in matches:
        return None  # malformed star syntax stays a wildcard rejection
    if len(starred) > 1:
        raise StarAlleleQueryError(tuple(starred))
    match = matches[0]
    assert match is not None
    text = " ".join([*(word for word in words if "*" not in word), *filter(None, [match[1]])])
    first, _, second = match[2].partition("/")
    # A diplotype is unordered: *10/*1 is the source's *1/*10.
    alleles = (match[2], f"{second}/{first}") if second else (first, first)
    return text or None, alleles
