"""Сбор исходных данных для будущих строк календарного плана."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re

from calendar_pedagoga.match_review import (
    MISSING_PROGRAM_CONTENT_NOTICE,
    apply_match_reviews,
    topic_key,
)
from calendar_pedagoga.matching import (
    MatchStatus,
    bound_program_item,
    match_utp_to_program,
    normalize_title,
)
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.program_parsing import (
    ProgramContentItem,
    ProgramData,
    infer_study_year_number,
)
from calendar_pedagoga.scheduling import ScheduleResult


@dataclass(frozen=True)
class WeekTopicPart:
    topic_number: str | None
    topic_title: str
    section: str
    theory_hours: int
    practice_hours: int
    match_status: MatchStatus
    program_section: str
    program_topic: str
    program_content_full: str
    warnings: tuple[str, ...] = ()
    knowledge_outcomes: tuple[str, ...] = ()
    skill_outcomes: tuple[str, ...] = ()
    weekly_content_assigned: bool = False


@dataclass(frozen=True)
class CalendarContentRow:
    week_number: int
    date_range: str
    month: str
    section: str
    topic_number: str | None
    topic_title: str
    source_topic_title: str
    theory_hours: int
    practice_hours: int
    total_hours: int
    match_status: MatchStatus
    program_section: str
    program_topic: str
    program_content_full: str
    program_content_preview: str
    source_program_name: str
    source_utp_name: str
    week_parts: tuple[WeekTopicPart, ...] = ()
    warnings: tuple[str, ...] = ()
    knowledge_outcomes: tuple[str, ...] = ()
    skill_outcomes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _SectionContentBlock:
    title: str
    theory: str = ""
    practice: str = ""
    untyped: str = ""


@dataclass(frozen=True)
class _AssignedSectionContent:
    title: str
    content: str


_SECTION_MODE_MARKERS = {"теория": "theory", "практика": "practice"}
_NESTED_TOPIC_RE = re.compile(
    r"(?i)^тема\s+\d+(?:\.\d+)*\.?\s*(?P<title>.*)$"
)
_EXPLICIT_ACTIVITY_FORM_RE = re.compile(
    r"(?i)\b(?:игр(?:а|ы|овой|овая|овое)|викторин\w*|"
    r"соревнован\w*|тестирован\w*|диагностик\w*)\b"
)


def _first_source_sentence(lines: list[str]) -> str:
    text = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if not text:
        return ""
    match = re.match(r"^.+?(?:[.!?](?=\s|$)|$)", text)
    sentence = (match.group(0) if match else text).strip()
    return sentence if sentence.endswith((".", "!", "?")) else sentence + "."


def _section_blocks_from_item(
    item: ProgramContentItem,
) -> tuple[_SectionContentBlock, ...]:
    """Extract explicitly delimited section activities without inventing hours."""

    lines = [re.sub(r"\s+", " ", line).strip() for line in item.content.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ()

    blocks: list[_SectionContentBlock] = []
    title = item.title
    mode: str | None = None
    theory: list[str] = []
    practice: list[str] = []
    untyped: list[str] = []

    def flush() -> None:
        nonlocal theory, practice, untyped
        theory_text = _first_source_sentence(theory)
        practice_text = _first_source_sentence(practice)
        untyped_text = _first_source_sentence(untyped)
        if theory_text or practice_text or untyped_text:
            blocks.append(
                _SectionContentBlock(
                    title=title,
                    theory=theory_text,
                    practice=practice_text,
                    untyped=untyped_text,
                )
            )
        theory, practice, untyped = [], [], []

    for index, line in enumerate(lines):
        low = line.casefold().rstrip(".:")
        marker = _SECTION_MODE_MARKERS.get(low)
        next_marker = (
            _SECTION_MODE_MARKERS.get(lines[index + 1].casefold().rstrip(".:"))
            if index + 1 < len(lines)
            else None
        )
        nested = _NESTED_TOPIC_RE.match(line)
        if nested:
            inherited_mode = mode
            flush()
            title = (nested.group("title") or item.title).strip().rstrip(".")
            mode = inherited_mode
            continue
        if marker:
            mode = marker
            continue
        # A source title immediately before its first marker starts a new block.
        # The single theory sentence immediately before «Практика» belongs to
        # the current block and therefore is not reclassified as a title.
        if next_marker and not (mode == "theory" and next_marker == "practice"):
            flush()
            title = line.rstrip(".")
            mode = None
            continue
        if mode == "theory":
            theory.append(line)
        elif mode == "practice":
            practice.append(line)
        else:
            untyped.append(line)
    flush()
    return tuple(blocks)


def _assign_section_blocks(
    blocks: tuple[_SectionContentBlock, ...],
    part_type: str,
    *,
    appearances: int,
    allow_untyped: bool = False,
) -> tuple[_AssignedSectionContent, ...]:
    """Assign source-ordered semantic blocks once across section appearances."""

    eligible = tuple(
        block
        for block in blocks
        if getattr(block, part_type)
        or (allow_untyped and block.untyped)
    )
    if appearances <= 0 or not eligible:
        return ()
    # Local import avoids the existing lesson_content -> content_generation
    # dependency cycle while reusing the one established slot allocator.
    from calendar_pedagoga.practice_slots import assign_practice_slots

    slots = assign_practice_slots(list(eligible), appearances)
    marker = "Теория." if part_type == "theory" else "Практика."
    assigned: list[_AssignedSectionContent] = []
    for slot in slots:
        titles = tuple(dict.fromkeys(block.title for block in slot))
        representative = slot[0]
        if part_type == "practice":
            representative = next(
                (
                    block
                    for block in slot
                    if _EXPLICIT_ACTIVITY_FORM_RE.search(
                        getattr(block, part_type) or block.untyped
                    )
                ),
                slot[(len(slot) - 1) // 2],
            )
        representative_clause = (
            getattr(representative, part_type) or representative.untyped
        )
        representative_title = representative.title
        if part_type == "practice" and ":" in representative_clause:
            prefix, _separator, _tail = representative_clause.partition(":")
            if _EXPLICIT_ACTIVITY_FORM_RE.search(prefix):
                representative_title = prefix.strip()
        assigned.append(
            _AssignedSectionContent(
                title=representative_title if representative_title else titles[0],
                content="\n".join((marker, representative_clause)),
            )
        )
    return tuple(assigned)


def _section_program_items(
    program: ProgramData,
    heading: ProgramContentItem,
) -> tuple[ProgramContentItem, ...]:
    section_key = normalize_title(heading.title)
    return tuple(
        item
        for item in program.content_items
        if item is heading
        or normalize_title(item.parent_section or "") == section_key
    )


def _ordered_compact_bindings(
    utp: UtpParseResult,
    program: ProgramData,
    matches_by_topic: Mapping,
    reviews: Mapping | None,
) -> dict[tuple[str | None, str, str | None], ProgramContentItem]:
    """Fill an unmatched compact section only when source order is anchored."""

    topics = tuple(utp.topics)
    if not _is_compact_section_plan(utp):
        return {}
    roots = tuple(
        item
        for item in program.content_items
        if item.parent_section is None
        or normalize_title(item.parent_section) == normalize_title(item.title)
    )
    if len(roots) != len(topics):
        return {}
    anchored = 0
    for index, topic in enumerate(topics):
        key = (topic.number, topic.title, topic.parent_section)
        bound = bound_program_item(matches_by_topic.get(key))
        if bound is None:
            continue
        if bound is not roots[index]:
            return {}
        anchored += 1
    if anchored < 2:
        return {}
    resolved: dict[tuple[str | None, str, str | None], ProgramContentItem] = {}
    for index, topic in enumerate(topics):
        key = (topic.number, topic.title, topic.parent_section)
        match = matches_by_topic.get(key)
        if bound_program_item(match) is not None:
            continue
        review = (reviews or {}).get(topic_key(topic))
        if review is not None:
            continue
        if (
            match is not None
            and match.status is MatchStatus.NOT_MATCHED
            and not match.ambiguous_candidates
        ):
            resolved[key] = roots[index]
    return resolved


def _is_compact_section_plan(utp: UtpParseResult) -> bool:
    topics = tuple(utp.topics)
    weeks = utp.metadata.study_weeks
    if weeks is None and utp.table_totals and utp.metadata.hours_per_week:
        weeks = utp.table_totals.total // utp.metadata.hours_per_week
    return bool(
        topics
        and weeks
        and all(topic.is_standalone_section for topic in topics)
        and len(topics) * 3 <= weeks
    )


def _preview(text: str, limit: int = 320) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def study_year_for_matching(utp: UtpParseResult) -> int | None:
    """Тот же год обучения, что analysis и pipeline передают в matching."""

    return infer_study_year_number(utp.metadata.study_year)


def build_content_model(
    schedule: ScheduleResult,
    utp: UtpParseResult,
    program: ProgramData | None,
    source_utp_name: str,
    match_reviews: Mapping | None = None,
) -> tuple[CalendarContentRow, ...]:
    """Связать календарные строки только с фактическими источниками."""
    matches = (
        match_utp_to_program(
            utp.topics,
            program.content_items,
            study_year=study_year_for_matching(utp),
        )
        if program
        else ()
    )
    if program is not None:
        matches = apply_match_reviews(matches, program.content_items, match_reviews)
    match_by_topic = {
        (match.utp_position.number, match.utp_position.title, match.utp_position.parent_section): match
        for match in matches
    }
    ordered_bindings = (
        _ordered_compact_bindings(utp, program, match_by_topic, match_reviews)
        if program is not None
        else {}
    )
    knowledge_outcomes = program.knowledge_outcomes if program else ()
    skill_outcomes = program.skill_outcomes if program else ()
    grouped: dict[tuple[int, str | None, str, str], dict[str, object]] = {}
    for element in schedule.elements:
        key = (element.week.number, element.topic_number, element.topic, element.section)
        if key not in grouped:
            grouped[key] = {"element": element, "theory": 0, "practice": 0}
        grouped[key][element.part_type] = int(grouped[key][element.part_type]) + element.hours

    section_content: dict[
        tuple[tuple[int, str | None, str, str], str],
        _AssignedSectionContent,
    ] = {}
    if program is not None and _is_compact_section_plan(utp):
        grouped_entries = list(grouped.items())
        for topic in utp.topics:
            if not topic.is_standalone_section:
                continue
            topic_key_value = (topic.number, topic.title, topic.parent_section)
            heading = (
                bound_program_item(match_by_topic.get(topic_key_value))
                or ordered_bindings.get(topic_key_value)
            )
            if heading is None:
                continue
            blocks = tuple(
                block
                for item in _section_program_items(program, heading)
                for block in _section_blocks_from_item(item)
            )
            for part_type in ("theory", "practice"):
                occurrences = [
                    key
                    for key, data in grouped_entries
                    if (
                        data["element"].topic_number,
                        data["element"].topic,
                        data["element"].section,
                    )
                    == topic_key_value
                    and int(data[part_type]) > 0
                ]
                assigned = _assign_section_blocks(
                    blocks,
                    part_type,
                    appearances=len(occurrences),
                    allow_untyped=(
                        topic.hours.theory == 0
                        if part_type == "practice"
                        else topic.hours.practice == 0
                    ),
                )
                for key, content in zip(occurrences, assigned):
                    section_content[(key, part_type)] = content

    topic_rows: list[tuple[int, WeekTopicPart, object]] = []
    for group_key, data in grouped.items():
        element = data["element"]
        row_topic_key = (element.topic_number, element.topic, element.section)
        match = match_by_topic.get(row_topic_key)
        ordered_program_item = ordered_bindings.get(row_topic_key)
        program_item = bound_program_item(match) or ordered_program_item
        match_status = (
            MatchStatus.TEXT_MATCH
            if ordered_program_item is not None
            else (match.status if match else MatchStatus.NOT_MATCHED)
        )
        if program is None:
            warnings = ("Образовательная программа не загружена; содержание отсутствует.",)
        elif program_item is None:
            review = (
                (match_reviews or {}).get(topic_key(match.utp_position))
                if match is not None
                else None
            )
            if isinstance(review, Mapping) and review.get("decision") == "USER_REJECTED":
                warnings = (
                    f"Тема УТП «{element.topic}» не связана с содержанием программы "
                    "(решение педагога).",
                )
            elif match is not None and match.status is MatchStatus.UNCONFIRMED:
                warnings = (
                    f"Тема УТП «{element.topic}» не сопоставлена с программой: "
                    "номер без подтверждения названия или раздела недостаточен.",
                )
            elif (
                match is not None
                and match.status is MatchStatus.NOT_MATCHED
                and not match.ambiguous_candidates
            ):
                warnings = (MISSING_PROGRAM_CONTENT_NOTICE,)
            else:
                warnings = (f"Тема УТП «{element.topic}» не сопоставлена с программой.",)
        else:
            warnings = ()
        theory = int(data["theory"])
        practice = int(data["practice"])
        assigned_parts = [
            section_content[(group_key, part_type)]
            for part_type in ("theory", "practice")
            if (group_key, part_type) in section_content
        ]
        full_content = (
            "\n".join(item.content for item in assigned_parts)
            if assigned_parts
            else (program_item.content if program_item else "")
        )
        program_topic = (
            (
                assigned_parts[-1].title
                if practice and assigned_parts[-1].title
                else assigned_parts[0].title
            )
            if assigned_parts
            else (program_item.title if program_item else "")
        )
        topic_rows.append(
            (
                element.week.number,
                WeekTopicPart(
                    topic_number=element.topic_number,
                    topic_title=element.topic,
                    section=element.section,
                    theory_hours=theory,
                    practice_hours=practice,
                    match_status=match_status,
                    program_section=program_item.parent_section or "" if program_item else "",
                    program_topic=program_topic,
                    program_content_full=full_content,
                    warnings=warnings,
                    knowledge_outcomes=knowledge_outcomes,
                    skill_outcomes=skill_outcomes,
                    weekly_content_assigned=bool(assigned_parts),
                ),
                element,
            )
        )

    by_week: dict[int, list[tuple[WeekTopicPart, object]]] = {}
    for week_number, part, element in topic_rows:
        by_week.setdefault(week_number, []).append((part, element))

    rows: list[CalendarContentRow] = []
    for week_number in sorted(by_week):
        parts_with_elements = by_week[week_number]
        parts = tuple(part for part, _ in parts_with_elements)
        element = parts_with_elements[0][1]
        theory = sum(part.theory_hours for part in parts)
        practice = sum(part.practice_hours for part in parts)
        primary = parts[0]
        combined_content = "\n".join(
            part.program_content_full
            for part in parts
            if part.program_content_full
        )
        combined_warnings = tuple(
            dict.fromkeys(warning for part in parts for warning in part.warnings)
        )
        rows.append(
            CalendarContentRow(
                week_number=week_number,
                date_range=element.week.date_range,
                month=element.week.month,
                section=primary.section,
                topic_number=primary.topic_number,
                topic_title=primary.topic_title,
                source_topic_title=primary.topic_title,
                theory_hours=theory,
                practice_hours=practice,
                total_hours=theory + practice,
                match_status=primary.match_status,
                program_section=primary.program_section,
                program_topic=primary.program_topic,
                program_content_full=combined_content,
                program_content_preview=_preview(combined_content),
                source_program_name=(program.title or "") if program else "",
                source_utp_name=source_utp_name,
                week_parts=parts,
                warnings=combined_warnings,
                knowledge_outcomes=knowledge_outcomes,
                skill_outcomes=skill_outcomes,
            )
        )
    return tuple(rows)
