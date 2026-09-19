"""Stable oracle text: NFC, quote folding, collapsed whitespace."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import hashlib
import unicodedata

_QUOTE_CHARS = frozenset("«»„“”\"‚‘’'")


def canonicalize_text(value: str | None) -> str:
    """Fold quotes and whitespace so oracle comparison is order-stable."""

    text = unicodedata.normalize("NFC", value or "")
    folded: list[str] = []
    for char in text:
        if char in _QUOTE_CHARS:
            folded.append('"')
        elif char.isspace():
            if folded and folded[-1] != " ":
                folded.append(" ")
        else:
            folded.append(char)
    return "".join(folded).strip()


def canonicalize_pairs(
    pairs: Sequence[tuple[str, str]] | None,
) -> list[list[str]]:
    """Keep production order; only normalize each side of a pair."""

    return [
        [canonicalize_text(left), canonicalize_text(right)]
        for left, right in (pairs or ())
    ]


def stable_sorted(values: Iterable[str]) -> list[str]:
    return sorted(canonicalize_text(item) for item in values if canonicalize_text(item))


def text_hash(value: str | None) -> str:
    payload = canonicalize_text(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
