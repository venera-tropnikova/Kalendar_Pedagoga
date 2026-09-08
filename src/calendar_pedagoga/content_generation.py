"""Сбор исходных данных для будущих строк календарного плана."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from calendar_pedagoga.match_review import (
    MISSING_PROGRAM_CONTENT_NOTICE,
    apply_match_reviews,
    topic_key,
)
from calendar_pedagoga.matching import MatchStatus, bound_program_item, match_utp_to_program
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.program_parsing import ProgramData, infer_study_year_number
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
    knowledge_outcomes = program.knowledge_outcomes if program else ()
    skill_outcomes = program.skill_outcomes if program else ()
    grouped: dict[tuple[int, str | None, str, str], dict[str, object]] = {}
    for element in schedule.elements:
        key = (element.week.number, element.topic_number, element.topic, element.section)
        if key not in grouped:
            grouped[key] = {"element": element, "theory": 0, "practice": 0}
        grouped[key][element.part_type] = int(grouped[key][element.part_type]) + element.hours

    topic_rows: list[tuple[int, WeekTopicPart, object]] = []
    for data in grouped.values():
        element = data["element"]
        row_topic_key = (element.topic_number, element.topic, element.section)
        match = match_by_topic.get(row_topic_key)
        program_item = bound_program_item(match)
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
        full_content = program_item.content if program_item else ""
        topic_rows.append(
            (
                element.week.number,
                WeekTopicPart(
                    topic_number=element.topic_number,
                    topic_title=element.topic,
                    section=element.section,
                    theory_hours=theory,
                    practice_hours=practice,
                    match_status=match.status if match else MatchStatus.NOT_MATCHED,
                    program_section=program_item.parent_section or "" if program_item else "",
                    program_topic=program_item.title if program_item else "",
                    program_content_full=full_content,
                    warnings=warnings,
                    knowledge_outcomes=knowledge_outcomes,
                    skill_outcomes=skill_outcomes,
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
