"""GeneFoundry v1.1 structural fencing for externally sourced prose."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable
from typing import Literal, TypedDict

from clinpgx_link.exceptions import ResponseTooLargeError
from clinpgx_link.models import SourceInfo

FORBIDDEN_CODEPOINTS = frozenset(
    {
        *range(0x0000, 0x0009),
        *range(0x000B, 0x000D),
        *range(0x000E, 0x0020),
        *range(0x007F, 0x00A0),
        0x200B,
        0x200C,
        0x200D,
        0x2060,
        0xFEFF,
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
    }
)


class Provenance(TypedDict):
    source: str
    record_id: str
    retrieved_at: str


class UntrustedText(TypedDict):
    kind: Literal["untrusted_text"]
    text: str
    provenance: Provenance
    raw_sha256: str


def _sanitize(raw: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFC", raw) if ord(char) not in FORBIDDEN_CODEPOINTS
    )


def fence_text(raw: str, *, source: SourceInfo, record_id: str) -> UntrustedText:
    """Fence source prose using acquisition time, never the time of a cache hit."""
    return {
        "kind": "untrusted_text",
        "text": _sanitize(raw),
        "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "provenance": {
            "source": source.source,
            "record_id": record_id,
            "retrieved_at": source.retrieved_at,
        },
    }


def enforce_limits(
    objects: Iterable[UntrustedText],
    *,
    max_objects: int = 128,
    max_text_bytes: int = 2_097_152,
    max_total_bytes: int = 8_388_608,
) -> None:
    """Fail explicitly at the v1.1 count/per-text/aggregate byte ceilings.

    Each text is a leaf, so untrusted subtree depth is one by construction.
    A caller must include every emitted fence, including metadata source prose.
    """
    total = 0
    for count, obj in enumerate(objects, 1):
        size = len(obj["text"].encode("utf-8"))
        total += size
        if count > max_objects or size > max_text_bytes or total > max_total_bytes:
            raise ResponseTooLargeError(
                "Source text exceeds a response fencing limit.",
                hint="Request fewer records or retrieve source content in bounded chunks.",
            )


def sanitize_message(message: str) -> str:
    """Backstop for developer-authored messages; never sanitize-and-forward upstream errors."""
    return _sanitize(message)[:280]
