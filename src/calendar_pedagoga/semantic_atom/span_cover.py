"""Exact SOURCE-span coverage for ACTION frames. No builders, no production."""

from __future__ import annotations

from dataclasses import replace
import re

from calendar_pedagoga import content_engine_v2 as _ce2
from calendar_pedagoga.semantic_atom.canonicalize import canonicalize_text
from calendar_pedagoga.semantic_atom.lexical import _lemmas, tokenize
from calendar_pedagoga.semantic_atom.models import FrameKind, ObjectStatus, SemanticFrame, SourceAtom

_QUOTE_RE = re.compile(r"«([^»]+)»|\"([^\"]+)\"")
_FUNCTION = frozenset(
    {
        "а",
        "без",
        "в",
        "во",
        "да",
        "для",
        "и",
        "из",
        "или",
        "к",
        "на",
        "о",
        "об",
        "от",
        "по",
        "с",
        "со",
        "у",
    }
)
_PREPS = frozenset(
    {
        "без",
        "в",
        "во",
        "для",
        "до",
        "за",
        "из",
        "к",
        "ко",
        "на",
        "над",
        "о",
        "об",
        "от",
        "по",
        "под",
        "при",
        "про",
        "с",
        "со",
        "у",
        "через",
    }
)
_MIN_LEN = 3


def _fold(text: str) -> str:
    return canonicalize_text(text or "").casefold()


def _core_text(text: str) -> str:
    tokens = list(tokenize(text))
    cut = next(
        (index for index, token in enumerate(tokens) if index and token.casefold() in _PREPS),
        None,
    )
    if cut:
        return " ".join(tokens[:cut])
    return text


def _content_lemmas(text: str) -> frozenset[str]:
    found: set[str] = set()
    for token in tokenize(text):
        core = token.casefold()
        if len(core) < _MIN_LEN or core in _FUNCTION:
            continue
        conjugated = _ce2._conjugate_verbal_noun(token)
        if conjugated:
            found.update(_lemmas(conjugated))
            continue
        lemmas = _lemmas(token)
        found.update(lemmas)
        verbal = _ce2._verbal_noun_lemma(token)
        if verbal:
            mapped = _ce2._conjugate_verbal_noun(verbal)
            if mapped:
                found.update(_lemmas(mapped))
            else:
                found.update(_lemmas(verbal))
        if not lemmas:
            found.add(core)
    return frozenset(found)


def _split_members(text: str) -> list[str]:
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if not cleaned:
        return []
    if ";" in cleaned:
        parts = [part.strip(" .") for part in cleaned.split(";") if part.strip(" .")]
        if parts:
            return parts
    coordinated = [
        part.strip(" .")
        for part in _ce2._split_coordinating_и_outside_quotes(cleaned)
        if part.strip(" .")
    ]
    if len(coordinated) >= 2:
        return coordinated
    listed = _ce2._split_source_list(cleaned)
    if len(listed) >= 2:
        return listed
    return [cleaned]


def _refine_quoted(parts: list[str]) -> list[str]:
    refined: list[str] = []
    for part in parts:
        quotes = [item for pair in _QUOTE_RE.findall(part) for item in pair if item]
        if not quotes:
            refined.append(part)
            continue
        leftover = _QUOTE_RE.sub("", part).strip(" ,.;:—–-")
        if leftover:
            refined.append(leftover)
        refined.extend(quotes)
    return [item for item in refined if item]


def action_source_parts(atom_text: str) -> list[str]:
    cleaned = _ce2._normalize_spaces(atom_text).strip(" .")
    if not cleaned:
        return []
    if ":" in cleaned:
        head, tail = cleaned.split(":", 1)
        head, tail = head.strip(), tail.strip(" .")
        members = _refine_quoted(_split_members(tail) if tail else [])
        parts = ([head] if head else []) + members
        if len(parts) >= 2:
            return parts
    members = _refine_quoted(_split_members(cleaned))
    return members or [cleaned]


def _used_fields(frame: object) -> str:
    return _ce2._normalize_spaces(
        " ".join(
            part
            for part in (
                str(getattr(frame, "projected_result", "") or ""),
                str(getattr(frame, "predicate", "") or ""),
                str(getattr(frame, "object", "") or ""),
                str(getattr(frame, "complement", "") or ""),
            )
            if part.strip()
        )
    )


def part_attested_in_frame(part: str, frame: object) -> bool:
    used = _used_fields(frame)
    if not used or not part.strip():
        return False
    folded_part = _fold(part)
    folded_used = _fold(used)
    if folded_part and folded_part in folded_used:
        return True
    used_lemmas = _content_lemmas(used)
    if not used_lemmas:
        return False
    for probe in (part, _core_text(part)):
        part_lemmas = _content_lemmas(probe)
        if part_lemmas and part_lemmas <= used_lemmas:
            return True
    return False


def attested_action_parts(atom_text: str, frame: object) -> list[str]:
    return [part for part in action_source_parts(atom_text) if part_attested_in_frame(part, frame)]


def action_cover_text(atom_text: str, frame: object) -> str:
    parts = action_source_parts(atom_text)
    if not parts:
        return ""
    attested = [part for part in parts if part_attested_in_frame(part, frame)]
    if not attested:
        return ""
    if len(attested) == len(parts):
        return atom_text
    return ", ".join(attested)


def _span_for_part(atom: SourceAtom, part: str):
    hay = atom.text
    needle = _ce2._normalize_spaces(part).strip(" .")
    if not needle:
        return None
    index = hay.find(needle)
    if index < 0:
        index = hay.casefold().find(needle.casefold())
        if index < 0:
            return None
        if hay[index : index + len(needle)].casefold() != needle.casefold():
            return None
    start = atom.span.start + index
    end = start + len(needle)
    stripped_end = len(hay.rstrip(" ."))
    if index == 0 and index + len(needle) >= stripped_end:
        return atom.span
    return replace(atom.span, start=start, end=end)


def narrow_action_frame(atom: SourceAtom, frame: SemanticFrame) -> SemanticFrame:
    if frame.status is not ObjectStatus.PROVEN:
        return frame
    if frame.kind is not FrameKind.ACTION:
        return frame
    parts = action_source_parts(atom.text)
    attested = [part for part in parts if part_attested_in_frame(part, frame)]
    if not attested or len(attested) == len(parts):
        return frame
    span = _span_for_part(atom, attested[0])
    if span is None:
        return frame
    return replace(frame, span=span)
