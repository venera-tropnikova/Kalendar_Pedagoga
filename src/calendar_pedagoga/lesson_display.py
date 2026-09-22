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
    selected_clause: str = "",
) -> str:
    if hours <= 0:
        return ""

    label = f"{display_number}. {topic_title}"
    if selected_clause.strip():
        fragment = _normalize_spaces(selected_clause).rstrip(" .")
        if fragment and fragment.casefold() not in label.casefold():
            return f"{label}. {fragment} ({hours})"
        return f"{label} ({hours})"

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


_DISPLAY_DOSAGE_UNIT_RE = re.compile(
    r"(?i)\b(?:раз(?:а|ов)?|мин(?:ут(?:а|ы)?)?\.?|сек(?:унд(?:а|ы)?)?\.?|"
    r"круг(?:а|ов)?|подход(?:а|ов)?|трасс(?:а|ы)?|повтор(?:а|ов)?)\b"
)
_DISPLAY_PROTECTED_POSTFIX_RE = re.compile(
    r"(?i)\b(?:безопасн\w*|запрещ\w*|не\s+допуска\w*|"
    r"аттестац\w*|зач[её]т\w*|контрольн\w*|итогов\w*)\b"
)


def _display_punctuation_unambiguous(sentence: str) -> bool:
    """Reject malformed delimiters before display-only restructuring."""

    if sentence.count("(") != sentence.count(")"):
        return False
    if sentence.count("«") != sentence.count("»"):
        return False
    for match in re.finditer("«", sentence):
        prefix = sentence[: match.start()].rstrip()
        if (
            prefix
            and prefix[-1] not in "(:,;"
            and any(delimiter in prefix for delimiter in ":,")
        ):
            return False
    return True


def _display_trailing_dosage(sentence: str) -> tuple[str, str, str] | None:
    """Return exact base, marker and dosage shape for one trailing marker."""

    core = _normalize_spaces(sentence).rstrip(" .")
    if not core.endswith(")"):
        return None
    marker_start = core.rfind("(")
    if marker_start <= 0:
        return None
    base = core[:marker_start].rstrip()
    marker = core[marker_start + 1 : -1].strip()
    if not base or "(" in marker or ")" in marker:
        return None
    if not re.search(r"\d", marker) or not _DISPLAY_DOSAGE_UNIT_RE.search(marker):
        return None
    shape = re.sub(r"\d+", "#", marker.casefold())
    shape = re.sub(
        _DISPLAY_DOSAGE_UNIT_RE,
        lambda match: re.match(r"[a-zа-яё]+", match.group(0).casefold()).group(0),
        shape,
    )
    shape = re.sub(r"(?i)\bраз(?:а|ов)?\b", "раз", shape)
    shape = re.sub(r"(?i)\bмин(?:ут(?:а|ы)?)?\.?\b", "мин", shape)
    shape = re.sub(r"(?i)\bсек(?:унд(?:а|ы)?)?\.?\b", "сек", shape)
    shape = re.sub(r"(?i)\bкруг(?:а|ов)?\b", "круг", shape)
    shape = re.sub(r"(?i)\bподход(?:а|ов)?\b", "подход", shape)
    shape = re.sub(r"(?i)\bтрасс(?:а|ы)?\b", "трасс", shape)
    shape = re.sub(r"(?i)\bповтор(?:а|ов)?\b", "повтор", shape)
    return base, marker, _normalize_spaces(shape)


def _display_postfix_frame(sentence: str) -> tuple[str, str] | None:
    if (
        not _display_punctuation_unambiguous(sentence)
        or _DISPLAY_PROTECTED_POSTFIX_RE.search(sentence)
    ):
        return None
    dosage = _display_trailing_dosage(sentence)
    if dosage is None:
        return _normalize_spaces(sentence).rstrip(" .").casefold(), ""
    base, _marker, shape = dosage
    return base.casefold(), shape


def _display_postfix_item(
    variants: list[str],
    *,
    block_count: int,
) -> str | None:
    frames = [_display_postfix_frame(sentence) for sentence in variants]
    if not frames or frames[0] is None or any(frame != frames[0] for frame in frames[1:]):
        return None
    dosages = [_display_trailing_dosage(sentence) for sentence in variants]
    if all(dosage is None for dosage in dosages):
        return _normalize_spaces(variants[0]).rstrip(" .")
    if any(dosage is None for dosage in dosages):
        return None
    parsed = [dosage for dosage in dosages if dosage is not None]
    markers = [marker for _base, marker, _shape in parsed]
    if len({_normalize_spaces(marker).casefold() for marker in markers}) == 1:
        return _normalize_spaces(variants[0]).rstrip(" .")
    base = parsed[0][0]
    numbers = [re.findall(r"\d+", marker) for marker in markers]
    if all(len(values) == 1 for values in numbers):
        first_number = re.search(r"\d+", markers[0])
        assert first_number is not None
        prefix = markers[0][: first_number.start()]
        suffix = markers[0][first_number.end() :]
        values = "/".join(matches[0] for matches in numbers)
        dosage_vector = f"{prefix}{values}{suffix}"
    else:
        dosage_vector = " / ".join(markers)
    block_labels = "/".join(str(index) for index in range(1, block_count + 1))
    return f"{base} (блоки {block_labels}: {dosage_vector})"


def _compact_repeated_practice_postfix(text: str) -> str:
    """Compact one unambiguous repeated postfix for DOCX display only."""

    original = _normalize_spaces(text).rstrip(" .")
    sentences = _split_sentences(original)
    if len(sentences) < 4:
        return original
    frames = [_display_postfix_frame(sentence) for sentence in sentences]
    candidates: list[tuple[int, int, int, int, int, tuple[int, ...], str]] = []
    for postfix_length in range(1, len(sentences) // 2 + 1):
        for first in range(1, len(sentences) - postfix_length):
            signature = tuple(frames[first : first + postfix_length])
            if any(frame is None for frame in signature):
                continue
            positions = [first]
            cursor = first + postfix_length + 1
            while cursor <= len(sentences) - postfix_length:
                if tuple(frames[cursor : cursor + postfix_length]) == signature:
                    positions.append(cursor)
                    cursor += postfix_length + 1
                else:
                    cursor += 1
            if len(positions) < 2:
                continue
            previous_end = 0
            main_blocks: list[tuple[str, ...]] = []
            valid = True
            for position in positions:
                main = sentences[previous_end:position]
                if not main or any(
                    not _display_punctuation_unambiguous(sentence) for sentence in main
                ):
                    valid = False
                    break
                main_blocks.append(main)
                previous_end = position + postfix_length
            if not valid or len({_join_sentences(block) for block in main_blocks}) < 2:
                continue
            postfix_items: list[str] = []
            for offset in range(postfix_length):
                item = _display_postfix_item(
                    [sentences[position + offset] for position in positions],
                    block_count=len(positions),
                )
                if item is None:
                    valid = False
                    break
                postfix_items.append(item)
            if not valid:
                continue
            block_text = "; ".join(
                f"{index}) {_join_sentences(block)}"
                for index, block in enumerate(main_blocks, start=1)
            )
            compacted = f"{block_text}. После каждого блока: {'; '.join(postfix_items)}"
            tail = sentences[previous_end:]
            if tail:
                compacted += ". " + _join_sentences(tail)
            compacted = _normalize_spaces(compacted).rstrip(" .")
            candidates.append(
                (
                    len(positions) * postfix_length,
                    len(positions),
                    -first,
                    len(original) - len(compacted),
                    postfix_length,
                    tuple(positions),
                    compacted,
                )
            )
    if not candidates:
        return original
    candidates.sort(reverse=True)
    best = candidates[0]
    tied = [candidate for candidate in candidates if candidate[:4] == best[:4]]
    if any((candidate[4], candidate[5]) != (best[4], best[5]) for candidate in tied[1:]):
        return original
    return best[6]


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
        body = _compact_repeated_practice_postfix(selected_clause)
        return f"{body} ({hours})"

    summary, practice_kind = brief_practice_summary(content)
    if summary:
        summary = _compact_repeated_practice_postfix(summary)
        if practice_kind == "split" and topic_title.casefold() not in summary.casefold():
            body = f"{display_number}. {topic_title}. {summary}"
        else:
            body = summary
        return f"{body} ({hours})"

    return f"{display_number}. {topic_title} ({hours})"
