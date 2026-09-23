"""Distribute confirmed overlay SOURCE across weekly slots of one UTP topic.

Applies only to USER_CONFIRMED overlay parts. Known-program auto-path is
untouched. Does not invent missing markers or broadcast a full topic SOURCE
to every week of that topic.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
import re

from calendar_pedagoga.matching import MatchStatus


UNRESOLVED_PREFIX = "UNRESOLVED:"
UNRESOLVED_MIXED_NO_MARKERS = (
    f"{UNRESOLVED_PREFIX} mixed theory/practice hours without a "
    "Теория/Практика/Практические работы marker; SOURCE was not split."
)
UNRESOLVED_EMPTY_THEORY_SLOT = (
    f"{UNRESOLVED_PREFIX} no remaining theory units for this weekly slot."
)
UNRESOLVED_EMPTY_PRACTICE_SLOT = (
    f"{UNRESOLVED_PREFIX} no remaining practice units for this weekly slot."
)
UNRESOLVED_LEFTOVER_THEORY = (
    f"{UNRESOLVED_PREFIX} theory units remain without a theory-bearing weekly slot."
)
UNRESOLVED_LEFTOVER_PRACTICE = (
    f"{UNRESOLVED_PREFIX} practice units remain without a practice-bearing weekly slot."
)

_MARKER_RE = re.compile(
    r"(?i)^\s*(?P<marker>"
    r"теория|практические\s+работы|практические\s+занятия|практика"
    r")\s*(?P<sep>[.:])?\s*(?P<rest>.*)$"
)
_QUOTED_RE = re.compile(r"«([^»]+)»|“([^”]+)”|\"([^\"]+)\"")
# Next catalog item starts at `», «` or at a comma before the next opening quote.
_CATALOG_BOUNDARY_RE = re.compile(r"(?:»\s*)?,\s*(?=«)")
_QUOTE_CHARS_RE = re.compile(r"[«»„“”\"]")
_CATALOG_LABEL_RE = re.compile(r"(?i)^темы?$")
_SKIP_UNITS = frozenset(
    {
        "теория",
        "практика",
        "практические работы",
        "практические занятия",
        "темы",
        "продолжение",
    }
)


@dataclass(frozen=True)
class ConfirmedChannelSplit:
    theory_units: tuple[str, ...]
    practice_units: tuple[str, ...]
    unresolved_reason: str | None = None


@dataclass(frozen=True)
class ConfirmedSlotAssignment:
    content: str
    warnings: tuple[str, ...]
    weekly_content_assigned: bool
    theory_units: tuple[str, ...]
    practice_units: tuple[str, ...]
    unresolved_reason: str | None = None


def _hour_weight(hours: object) -> int:
    try:
        value = Decimal(str(hours))
    except Exception:
        return 0
    if value <= 0:
        return 0
    return int((value * 1000).to_integral_value(rounding=ROUND_DOWN))


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _marker_channel(label: str) -> str:
    folded = re.sub(r"\s+", " ", label.casefold())
    if folded == "теория":
        return "theory"
    return "practice"


def _parse_marker_line(line: str) -> tuple[str, str] | None:
    match = _MARKER_RE.match(line.strip())
    if not match:
        return None
    rest = _normalize_spaces(match.group("rest") or "")
    return _marker_channel(match.group("marker")), rest


def _join_units(units: tuple[str, ...]) -> str:
    pieces = []
    for unit in units:
        text = _normalize_spaces(unit).rstrip(" .")
        if text:
            pieces.append(text)
    if not pieces:
        return ""
    body = ". ".join(pieces)
    return body if body.endswith((".", "!", "?")) else body + "."


def format_allocated_units(units: tuple[str, ...]) -> str:
    """Join allocated weekly units for SOURCE display and SentenceFrame."""

    return _join_units(units)


def _quoted_titles(text: str) -> tuple[str, ...]:
    titles: list[str] = []
    for match in _QUOTED_RE.finditer(text):
        title = next((group for group in match.groups() if group), "").strip()
        if title and not _QUOTE_CHARS_RE.search(title):
            titles.append(f"«{title}»")
    return tuple(titles)


def _safe_recovered_work_title(title: str) -> bool:
    """A recovered catalog item is a nominal title, not a sentence or a short quote."""

    if not title or not title[0].isupper():
        return False
    if re.search(r"[.!?]", title):
        return False
    words = title.split()
    return 2 <= len(words) <= 12


def recover_quoted_catalog_works(text: str) -> tuple[str, ...] | None:
    """Split a quoted work list on the next `«` so one broken quote cannot swallow it.

    Returns None unless at least two items share that boundary. A lone unclosed
    quote, a short incidental quote, and descriptive prose stay on the ordinary
    path and are not promoted to work titles.
    """

    cleaned = _normalize_spaces(text)
    if "«" not in cleaned or _CATALOG_BOUNDARY_RE.search(cleaned) is None:
        return None
    first = cleaned.find("«")
    prefix = cleaned[:first].strip(" .;:")
    if prefix and _CATALOG_LABEL_RE.fullmatch(prefix) is None:
        return None
    parts = _CATALOG_BOUNDARY_RE.split(cleaned[first:])
    if len(parts) < 2:
        return None
    titles: list[str] = []
    for part in parts:
        title = _normalize_spaces(_QUOTE_CHARS_RE.sub(" ", part)).strip(" .,;:")
        if not _safe_recovered_work_title(title):
            return None
        titles.append(f"«{title}»")
    return tuple(titles)


_EMPTY_UNIT_RE = re.compile(r"^[\s,;:.\-–—/]*$")


def _skip_structural_unit(text: str) -> bool:
    cleaned = _normalize_spaces(text)
    if not cleaned or _EMPTY_UNIT_RE.match(cleaned):
        return True
    folded = cleaned.casefold().strip(" .:;,-")
    if not folded or folded in _SKIP_UNITS:
        return True
    if not re.search(r"[0-9a-zа-яё]", folded, flags=re.IGNORECASE):
        return True
    return False


def _catalog_item_units(text: str) -> tuple[str, ...] | None:
    from calendar_pedagoga.practice_slots import (
        format_catalog_part,
        parse_splittable_catalog,
    )

    catalog = parse_splittable_catalog(text)
    if catalog is None:
        return None
    head, separator, items = catalog
    return tuple(format_catalog_part(head, separator, (item,)) for item in items)


def _expand_segment(segment: str) -> tuple[str, ...]:
    from calendar_pedagoga.lesson_content import _clause_units

    cleaned = _normalize_spaces(segment)
    if not cleaned or _skip_structural_unit(cleaned):
        return ()
    catalog_units = _catalog_item_units(cleaned)
    if catalog_units is not None:
        return catalog_units
    recovered = recover_quoted_catalog_works(cleaned)
    if recovered is not None:
        remainder = _QUOTE_CHARS_RE.sub(" ", _QUOTED_RE.sub(" ", cleaned))
        extra: list[str] = []
        for raw in _clause_units(remainder):
            if _skip_structural_unit(raw):
                continue
            nested = _catalog_item_units(raw)
            if nested is not None:
                extra.extend(nested)
                continue
            extra.append(raw)
        return tuple((*recovered, *extra))
    quoted = _quoted_titles(cleaned)
    if quoted:
        remainder = _QUOTED_RE.sub(" ", cleaned)
        extra: list[str] = []
        for raw in _clause_units(remainder):
            if _skip_structural_unit(raw):
                continue
            nested = _catalog_item_units(raw)
            if nested is not None:
                extra.extend(nested)
                continue
            extra.append(raw)
        return tuple((*quoted, *extra))
    units: list[str] = []
    for raw in _clause_units(cleaned):
        if _skip_structural_unit(raw):
            continue
        nested = _catalog_item_units(raw)
        if nested is not None:
            units.extend(nested)
            continue
        nested_quoted = _quoted_titles(raw)
        if nested_quoted:
            units.extend(nested_quoted)
            continue
        units.append(raw)
    return tuple(units)


def _expand_channel_units(text: str) -> tuple[str, ...]:
    """Catalog and quoted titles first; a period inside quotes is not a boundary."""

    units: list[str] = []
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    segments = lines or [_normalize_spaces(text or "")]
    for segment in segments:
        units.extend(_expand_segment(segment))
    return tuple(dict.fromkeys(unit for unit in units if unit.strip()))


def split_confirmed_source(
    content: str,
    *,
    topic_theory_hours: object,
    topic_practice_hours: object,
) -> ConfirmedChannelSplit:
    """Split overlay SOURCE into ordered theory/practice units, or abstain."""

    theory_hours = _hour_weight(topic_theory_hours)
    practice_hours = _hour_weight(topic_practice_hours)
    raw = (content or "").strip()
    if not raw:
        return ConfirmedChannelSplit((), ())

    theory_chunks: list[str] = []
    practice_chunks: list[str] = []
    untyped_chunks: list[str] = []
    mode: str | None = None
    saw_marker = False
    for line in raw.splitlines():
        cleaned = _normalize_spaces(line)
        if not cleaned:
            continue
        parsed = _parse_marker_line(cleaned)
        if parsed is not None:
            mode, rest = parsed
            saw_marker = True
            if rest:
                if mode == "theory":
                    theory_chunks.append(rest)
                else:
                    practice_chunks.append(rest)
            continue
        if mode == "theory":
            theory_chunks.append(cleaned)
        elif mode == "practice":
            practice_chunks.append(cleaned)
        else:
            untyped_chunks.append(cleaned)

    if not saw_marker:
        if theory_hours and practice_hours:
            return ConfirmedChannelSplit((), (), UNRESOLVED_MIXED_NO_MARKERS)
        if practice_hours and not theory_hours:
            return ConfirmedChannelSplit((), _expand_channel_units("\n".join(untyped_chunks)))
        return ConfirmedChannelSplit(_expand_channel_units("\n".join(untyped_chunks)), ())

    # Untyped text before the first marker is the theory channel.
    theory_text = "\n".join((*untyped_chunks, *theory_chunks))
    practice_text = "\n".join(practice_chunks)
    return ConfirmedChannelSplit(
        _expand_channel_units(theory_text),
        _expand_channel_units(practice_text),
    )


def _ceil_share(count: int, prefix: int, total: int) -> int:
    if prefix <= 0:
        return 0
    if prefix >= total:
        return count
    return (count * prefix + total - 1) // total


def allocate_contiguous(
    units: tuple[str, ...],
    weights: tuple[int, ...],
) -> tuple[tuple[str, ...], ...]:
    """Assign each unit to at most one slot, preserving SOURCE order."""

    if not weights:
        return ()
    total = sum(weights)
    count = len(units)
    if count == 0 or total <= 0:
        return tuple(() for _ in weights)
    prefix = 0
    assigned: list[tuple[str, ...]] = []
    for weight in weights:
        start = _ceil_share(count, prefix, total)
        prefix += weight
        end = _ceil_share(count, prefix, total)
        if end < start:
            end = start
        assigned.append(tuple(units[start:end]))
    return tuple(assigned)


def _format_part_content(
    *,
    theory_units: tuple[str, ...],
    practice_units: tuple[str, ...],
    theory_hours: object,
    practice_hours: object,
) -> str:
    chunks: list[str] = []
    theory_text = _join_units(theory_units) if _hour_weight(theory_hours) else ""
    practice_text = _join_units(practice_units) if _hour_weight(practice_hours) else ""
    if theory_text:
        chunks.append(theory_text)
    if practice_text:
        if theory_text:
            chunks.append(f"Практика.\n{practice_text}")
        else:
            chunks.append(practice_text)
    return "\n".join(chunks)


def assign_confirmed_topic_slots(
    *,
    source_content: str,
    match_statuses: tuple[MatchStatus, ...],
    already_assigned: tuple[bool, ...],
    theory_hours: tuple[object, ...],
    practice_hours: tuple[object, ...],
) -> tuple[ConfirmedSlotAssignment, ...] | None:
    """Return per-part SOURCE segments, or None to keep the auto-path."""

    if not match_statuses:
        return None
    if any(status is not MatchStatus.USER_CONFIRMED for status in match_statuses):
        return None
    if any(already_assigned):
        return None

    topic_theory = sum(_hour_weight(hours) for hours in theory_hours)
    topic_practice = sum(_hour_weight(hours) for hours in practice_hours)
    split = split_confirmed_source(
        source_content,
        topic_theory_hours=topic_theory,
        topic_practice_hours=topic_practice,
    )
    if split.unresolved_reason:
        return tuple(
            ConfirmedSlotAssignment(
                content="",
                warnings=(split.unresolved_reason,),
                weekly_content_assigned=True,
                theory_units=(),
                practice_units=(),
                unresolved_reason=split.unresolved_reason,
            )
            for _ in match_statuses
        )

    theory_indexes = [
        index for index, hours in enumerate(theory_hours) if _hour_weight(hours)
    ]
    practice_indexes = [
        index for index, hours in enumerate(practice_hours) if _hour_weight(hours)
    ]
    leftover_warnings: list[str] = []
    if split.theory_units and not theory_indexes:
        leftover_warnings.append(UNRESOLVED_LEFTOVER_THEORY)
    if split.practice_units and not practice_indexes:
        leftover_warnings.append(UNRESOLVED_LEFTOVER_PRACTICE)

    theory_slots = allocate_contiguous(
        split.theory_units,
        tuple(_hour_weight(theory_hours[index]) for index in theory_indexes),
    )
    practice_slots = allocate_contiguous(
        split.practice_units,
        tuple(_hour_weight(practice_hours[index]) for index in practice_indexes),
    )
    theory_by_part = [() for _ in match_statuses]
    practice_by_part = [() for _ in match_statuses]
    for slot_index, part_index in enumerate(theory_indexes):
        theory_by_part[part_index] = theory_slots[slot_index]
    for slot_index, part_index in enumerate(practice_indexes):
        practice_by_part[part_index] = practice_slots[slot_index]

    assigned: list[ConfirmedSlotAssignment] = []
    for index, _status in enumerate(match_statuses):
        warnings: list[str] = list(leftover_warnings)
        reason = None
        if _hour_weight(theory_hours[index]) and not theory_by_part[index]:
            reason = UNRESOLVED_EMPTY_THEORY_SLOT
            warnings.append(reason)
        if _hour_weight(practice_hours[index]) and not practice_by_part[index]:
            reason = UNRESOLVED_EMPTY_PRACTICE_SLOT
            warnings.append(reason)
        content = _format_part_content(
            theory_units=theory_by_part[index],
            practice_units=practice_by_part[index],
            theory_hours=theory_hours[index],
            practice_hours=practice_hours[index],
        )
        assigned.append(
            ConfirmedSlotAssignment(
                content=content,
                warnings=tuple(dict.fromkeys(warnings)),
                weekly_content_assigned=True,
                theory_units=theory_by_part[index],
                practice_units=practice_by_part[index],
                unresolved_reason=reason,
            )
        )
    return tuple(assigned)


def assignment_records(
    assignments: tuple[ConfirmedSlotAssignment, ...],
) -> tuple[tuple[str, str, int], ...]:
    """(unit, channel, part_index) for every assigned SOURCE unit."""

    records: list[tuple[str, str, int]] = []
    for index, item in enumerate(assignments):
        records.extend((unit, "theory", index) for unit in item.theory_units)
        records.extend((unit, "practice", index) for unit in item.practice_units)
    return tuple(records)
