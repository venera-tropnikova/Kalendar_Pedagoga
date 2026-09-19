"""Lossless shadow atomization of SOURCE. No kind/predicate, no production use."""

from __future__ import annotations

import re
from typing import Protocol

from calendar_pedagoga.semantic_atom.models import (
    AtomizationResult,
    ObjectStatus,
    Provenance,
    SourceAtom,
    SourceDelimiter,
    SourceSpan,
    fingerprint_source,
    make_object_id,
)

ADAPTER_NAME = "atom_c4"

_ATOM_ADAPTER_CALLS = 0

_ABBREV_RE = re.compile(
    r"(?ix)"
    r"(?:(?<=^)|(?<=\s)|(?<=[(«\"„]))"
    r"(?:г|ул|пр|пер|обл|р-н|с|п|д|пос|т|пгт|св|им|др|см|рис|стр|"
    r"т\.?\s*д|т\.?\s*п|т\.?\s*е|т\.?\s*к|и\s*т\.?\s*д|н\.?\s*э)"
    r"\."
)
_INITIAL_RE = re.compile(r"[А-ЯЁA-Z]\.(?:\s*[А-ЯЁA-Z]\.)*")
_DATE_RE = re.compile(
    r"\d{1,2}\s*[.\-/–—]\s*\d{1,2}(?:\s*[.\-/–—]\s*\d{2,4})?"
)
_DECIMAL_RE = re.compile(r"\d+[,.]\d+")
_NUMBER_DOT_RE = re.compile(r"\d+\.")
_ELLIPSIS_RE = re.compile(r"\.{3}|…")
_EVENT_RE = re.compile(
    r"(?i)(?:день|праздник|фестиваль|конкурс|выставка)\s+"
    r"[«\"„]?[^\s,.;:!?…][^,.;:!?…]{0,80}"
)

_PAIR_MARKERS = (
    ("«", "»"),
    ("„", "“"),
    ("“", "”"),
    ("(", ")"),
    ("[", "]"),
)


class _IdentityRow(Protocol):
    lesson_type: str
    planned_result: str
    assessment_method: str


def atom_adapter_calls() -> int:
    return _ATOM_ADAPTER_CALLS


def reset_atom_adapter_calls() -> None:
    global _ATOM_ADAPTER_CALLS
    _ATOM_ADAPTER_CALLS = 0


def identity_fields(row: _IdentityRow) -> tuple[str, str, str]:
    """TYPE/RESULT/CONTROL stay an identity copy of the old row."""

    return (row.lesson_type, row.planned_result, row.assessment_method)


def reconstruct_source(result: AtomizationResult) -> str:
    pieces = [
        (item.span.start, item.span.end, item.text)
        for item in (*result.atoms, *result.delimiters)
    ]
    pieces.sort(key=lambda item: (item[0], item[1]))
    return "".join(text for _start, _end, text in pieces)


def atomize(source: object) -> AtomizationResult:
    """Split SOURCE into lossless SourceAtom[] plus saved delimiters."""

    global _ATOM_ADAPTER_CALLS
    _ATOM_ADAPTER_CALLS += 1
    if source is None:
        return _explicit("", ObjectStatus.UNRESOLVED, "empty")
    if not isinstance(source, str):
        return _explicit("", ObjectStatus.UNRESOLVED, "invalid_source")
    if source == "":
        return _explicit("", ObjectStatus.UNRESOLVED, "empty")
    if source.strip() == "":
        return _from_ranges(source, (), note="empty")

    mask = _protected_mask(source)
    if mask is None:
        return _from_ranges(
            source,
            ((0, len(source)),),
            note="unbalanced_protected_region",
        )
    ranges = _atom_ranges(source, mask)
    return _from_ranges(source, tuple(ranges))


def _explicit(source: str, status: ObjectStatus, note: str) -> AtomizationResult:
    return AtomizationResult(
        source=source,
        atoms=(),
        delimiters=(),
        status=status,
        note=note,
    )


def _protected_mask(text: str) -> list[bool] | None:
    mask = [False] * len(text)
    if not _mark_pairs(text, mask):
        return None
    if not _mark_same_quotes(text, mask, '"') or not _mark_same_quotes(text, mask, "'"):
        return None
    for pattern in (
        _ELLIPSIS_RE,
        _ABBREV_RE,
        _INITIAL_RE,
        _DATE_RE,
        _DECIMAL_RE,
        _NUMBER_DOT_RE,
        _EVENT_RE,
    ):
        for match in pattern.finditer(text):
            _fill(mask, match.start(), match.end())
    return mask


def _mark_pairs(text: str, mask: list[bool]) -> bool:
    for left, right in _PAIR_MARKERS:
        index = 0
        length = len(text)
        while index < length:
            if text[index] != left:
                index += 1
                continue
            depth = 1
            cursor = index + 1
            closed = False
            while cursor < length:
                if text[cursor] == left:
                    depth += 1
                elif text[cursor] == right:
                    depth -= 1
                    if depth == 0:
                        _fill(mask, index, cursor + 1)
                        index = cursor
                        closed = True
                        break
                cursor += 1
            if not closed:
                return False
            index += 1
    return True


def _mark_same_quotes(text: str, mask: list[bool], quote: str) -> bool:
    pending: int | None = None
    for index, char in enumerate(text):
        if char != quote:
            continue
        if pending is None:
            pending = index
            continue
        _fill(mask, pending, index + 1)
        pending = None
    return pending is None


def _fill(mask: list[bool], start: int, end: int) -> None:
    last = min(end, len(mask))
    for index in range(max(0, start), last):
        mask[index] = True


def _atom_ranges(text: str, mask: list[bool]) -> list[tuple[int, int]]:
    length = len(text)
    ends: list[int] = []
    for index, char in enumerate(text):
        if mask[index]:
            continue
        nxt = text[index + 1] if index + 1 < length else ""
        if char in ".!?" and (nxt == "" or nxt.isspace()):
            ends.append(index + 1)
        elif char == "\n":
            ends.append(index)
        elif char == ";" and not _semicolon_inside_colon_list(text, mask, ends, index):
            ends.append(index)
    if not ends or ends[-1] != length:
        ends.append(length)

    ranges: list[tuple[int, int]] = []
    pos = 0
    for end in ends:
        start = pos
        while start < end and text[start].isspace():
            start += 1
        stop = end
        while stop > start and text[stop - 1].isspace():
            stop -= 1
        if stop > start and not _delimiter_only(text[start:stop]):
            ranges.append((start, stop))
        pos = end
    if not ranges:
        stripped = text.strip()
        if stripped:
            start = text.find(stripped)
            ranges.append((start, start + len(stripped)))
    return ranges


def _semicolon_inside_colon_list(
    text: str,
    mask: list[bool],
    ends: list[int],
    index: int,
) -> bool:
    """Keep ``NP: A; B; C`` as one atom; still split bare clause semicolons."""

    start = ends[-1] if ends else 0
    return any(
        text[cursor] == ":" and not mask[cursor] for cursor in range(start, index)
    )


def _delimiter_only(fragment: str) -> bool:
    return fragment != "" and all(char in "; \t\r\n" for char in fragment)


def _from_ranges(
    source: str,
    ranges: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    *,
    note: str = "",
) -> AtomizationResult:
    length = len(source)
    occupied = [False] * length
    atoms: list[SourceAtom] = []
    source_fp = fingerprint_source(source)
    clause_id = make_object_id("clause", source_fp, "c4")
    for index, (start, end) in enumerate(ranges):
        if not (0 <= start <= end <= length):
            return _explicit(source, ObjectStatus.UNRESOLVED, "broken_span")
        for cursor in range(start, end):
            occupied[cursor] = True
        atoms.append(_atom(source, start, end, index, clause_id))

    delimiters: list[SourceDelimiter] = []
    cursor = 0
    delim_index = 0
    while cursor < length:
        if occupied[cursor]:
            cursor += 1
            continue
        start = cursor
        while cursor < length and not occupied[cursor]:
            cursor += 1
        delimiters.append(_delimiter(source, start, cursor, delim_index))
        delim_index += 1

    status = ObjectStatus.UNASSESSED if atoms else ObjectStatus.UNRESOLVED
    if not atoms and not note:
        note = "empty"
    return AtomizationResult(
        source=source,
        atoms=tuple(atoms),
        delimiters=tuple(delimiters),
        status=status,
        note=note,
    )


def _span(source: str, start: int, end: int, role: str, index: int) -> SourceSpan:
    fingerprint = fingerprint_source(source, (start, end))
    return SourceSpan(
        id=make_object_id("span", fingerprint, role, str(start), str(end), str(index)),
        start=start,
        end=end,
        source_fingerprint=fingerprint,
        provenance=Provenance(adapter=ADAPTER_NAME, role=role, clause_index=index),
        status=ObjectStatus.UNASSESSED,
        document="",
    )


def _atom(source: str, start: int, end: int, index: int, clause_id: str) -> SourceAtom:
    span = _span(source, start, end, "atom", index)
    text = source[start:end]
    return SourceAtom(
        id=make_object_id("atom", span.source_fingerprint, text, str(start), str(end)),
        span=span,
        source_fingerprint=span.source_fingerprint,
        provenance=Provenance(adapter=ADAPTER_NAME, role="atom", clause_index=index),
        status=ObjectStatus.UNASSESSED,
        text=text,
        clause_id=clause_id,
        transitional=True,
    )


def _delimiter(source: str, start: int, end: int, index: int) -> SourceDelimiter:
    span = _span(source, start, end, "delimiter", index)
    return SourceDelimiter(
        id=make_object_id("delim", span.source_fingerprint, str(start), str(end)),
        span=span,
        source_fingerprint=span.source_fingerprint,
        provenance=Provenance(adapter=ADAPTER_NAME, role="delimiter", clause_index=index),
        status=ObjectStatus.UNASSESSED,
        text=source[start:end],
    )
