"""Краткое содержание занятия для колонок DOCX без усечения полного источника."""

from __future__ import annotations

import re

from calendar_pedagoga.lesson_content import _strip_leading_item_colon

# «Практические занятия» matches CE2 `_split_explicit_practice` (period/colon
# optional). «Практика.» stays a word-boundary split so in-paragraph theory/
# practice still splits.
_PRACTICE_MARKERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:^|\n)\s*Практические занятия\.?\s*:?\s*", re.IGNORECASE), "block"),
    (re.compile(r"\bПрактика\.\s*", re.IGNORECASE), "split"),
    # «Практика» отдельной строкой — тот же раздел, точка в источнике не обязательна.
    (re.compile(r"(?:^|\n)[ \t]*Практика[ \t]*(?:\n|$)", re.IGNORECASE), "split"),
)

_PRACTICE_MARKER_WORDS = frozenset({"практика", "практические занятия"})

_PRACTICE_SENTENCE_RE = re.compile(
    r"(?:составлен|разработ|отработ|подбор|выбор|выполн|изучен|определ|"
    r"организа|приготов|уклад|ремонт|изготов|обработ|оформлен|проведен|"
    r"наблюден|сравнен|измерен|работ|отчёт|движен|ориентир|применен|"
    r"закупк|фасовк|упаковк|сдач|подготовк|выступлен)",
    re.IGNORECASE,
)


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_sentences(text: str) -> tuple[str, ...]:
    parts = re.split(r"(?<=[.!?])\s+", _normalize_spaces(text))
    return tuple(part.strip() for part in parts if part.strip())


def _join_sentences(sentences: tuple[str, ...]) -> str:
    cleaned: list[str] = []
    for sentence in sentences:
        value = sentence.strip().rstrip(".")
        if value:
            cleaned.append(value)
    return ". ".join(cleaned)


def _practice_marker_match(content: str) -> tuple[re.Match[str], str] | None:
    earliest: tuple[int, re.Match[str], str] | None = None
    for pattern, kind in _PRACTICE_MARKERS:
        match = pattern.search(content)
        if match is None:
            continue
        if earliest is None or match.start() < earliest[0]:
            earliest = (match.start(), match, kind)
    if earliest is None:
        return None
    return earliest[1], earliest[2]


def _without_marker_lines(text: str) -> str:
    """Служебные строки «Практика»/«Практические занятия» не идут в ячейку."""
    return "\n".join(
        line
        for line in text.splitlines()
        if line.strip().casefold().strip(" .") not in _PRACTICE_MARKER_WORDS
    )


def brief_theory_fragment(content: str) -> str:
    """Теоретический фрагмент до явной границы практики."""
    match_info = _practice_marker_match(content)
    if match_info is None:
        return ""
    match, kind = match_info
    if kind != "split":
        return ""
    return _normalize_spaces(content[: match.start()])


def brief_practice_summary(content: str) -> tuple[str, str]:
    """Краткое практическое содержание и тип границы практики."""
    if not content.strip():
        return "", ""

    match_info = _practice_marker_match(content)
    if match_info is not None:
        match, kind = match_info
        practice_block = _normalize_spaces(
            _without_marker_lines(_strip_leading_item_colon(content[match.end() :]))
        )
        if practice_block and practice_block[0].islower() and ":" in match.group(0):
            practice_block = practice_block[0].upper() + practice_block[1:]
        sentences = _split_sentences(practice_block)
        if sentences:
            return _join_sentences(sentences), kind
        return practice_block, kind

    sentences = _split_sentences(content)
    practice_sentences = tuple(
        sentence for sentence in sentences if _PRACTICE_SENTENCE_RE.search(sentence)
    )
    if practice_sentences:
        return _join_sentences(practice_sentences), ""
    return "", ""


def format_theory_cell(
    display_number: str,
    topic_title: str,
    content: str,
    hours: int,
) -> str:
    if hours <= 0:
        return ""

    label = f"{display_number}. {topic_title}"
    fragment = brief_theory_fragment(content)
    if fragment and fragment.casefold() not in label.casefold():
        return f"{label}. {fragment} ({hours})"
    return f"{label} ({hours})"


def _clause_units_from_practice(content: str, *, theory_hours: int, practice_hours: int) -> list[str]:
    from calendar_pedagoga.lesson_content import _clause_units, _split_explicit_practice

    practice_text = ""
    explicit = _split_explicit_practice(content) if content else None
    if explicit:
        _theory, practice_source = explicit
        if practice_hours:
            practice_text = practice_source
    elif practice_hours and not theory_hours:
        practice_text = content
    return _clause_units(practice_text) if practice_text else []


def week_practice_content(
    content: str,
    *,
    theory_hours: int,
    practice_hours: int,
) -> str:
    """Всё практическое содержание недели без служебного маркера «Практика»."""

    units = [
        unit
        for unit in _clause_units_from_practice(
            content, theory_hours=theory_hours, practice_hours=practice_hours
        )
        if unit.casefold().strip(" .") not in _PRACTICE_MARKER_WORDS
    ]
    return ". ".join(unit.rstrip(" .") for unit in units)


def selected_practice_clause(
    *,
    topic_title: str,
    content: str,
    theory_hours: int,
    practice_hours: int,
    occurrence_index: int,
    appearance_count: int = 0,
) -> str:
    """Return the CE2 practice clause for this week, or empty if theory-led."""

    if practice_hours <= 0:
        return ""

    from calendar_pedagoga.practice_slots import (
        assign_distributed_practice_slots,
        format_slot_practice_text,
        practice_units_from_content,
    )

    units = practice_units_from_content(
        content, theory_hours=theory_hours, practice_hours=practice_hours
    )
    if appearance_count > 1 and units:
        slots, flags = assign_distributed_practice_slots(units, appearance_count)
        index = min(occurrence_index, len(slots) - 1)
        return format_slot_practice_text(
            slots[index],
            continuation=flags[index] if index < len(flags) else False,
        )

    from calendar_pedagoga.content_engine_v2 import select_source_clause
    from calendar_pedagoga.lesson_content import _split_explicit_practice

    theory_text = ""
    practice_text = ""
    explicit = _split_explicit_practice(content) if content else None
    if explicit:
        theory_source, practice_source = explicit
        if theory_hours:
            theory_text = theory_source
        if practice_hours:
            practice_text = practice_source
    elif practice_hours and not theory_hours:
        practice_text = content
    elif theory_hours and practice_hours:
        theory_text = content
    clause, theory_only, _pool = select_source_clause(
        topic_title=topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=content or "",
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        occurrence_index=occurrence_index,
    )
    if theory_only:
        return ""
    return _normalize_spaces(clause)


def format_practice_cell(
    display_number: str,
    topic_title: str,
    content: str,
    hours: int,
    selected_clause: str = "",
) -> str:
    if hours <= 0:
        return ""

    if selected_clause.strip():
        body = _normalize_spaces(selected_clause).rstrip(" .")
        return f"{body} ({hours})"

    summary, practice_kind = brief_practice_summary(content)
    if summary:
        if practice_kind == "split" and topic_title.casefold() not in summary.casefold():
            body = f"{display_number}. {topic_title}. {summary}"
        else:
            body = summary
        return f"{body} ({hours})"

    return f"{display_number}. {topic_title} ({hours})"
