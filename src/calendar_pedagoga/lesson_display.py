"""Краткое содержание занятия для колонок DOCX без усечения полного источника."""

from __future__ import annotations

import re

_PRACTICE_MARKERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:^|\n)Практические занятия\.\s*", re.IGNORECASE), "block"),
    (re.compile(r"\bПрактика\.\s*", re.IGNORECASE), "split"),
)

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
        practice_block = _normalize_spaces(content[match.end() :])
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


def format_practice_cell(
    display_number: str,
    topic_title: str,
    content: str,
    hours: int,
) -> str:
    if hours <= 0:
        return ""

    summary, practice_kind = brief_practice_summary(content)
    if summary:
        if practice_kind == "split" and topic_title.casefold() not in summary.casefold():
            body = f"{display_number}. {topic_title}. {summary}"
        else:
            body = summary
        return f"{body} ({hours})"

    return f"{display_number}. {topic_title} ({hours})"
