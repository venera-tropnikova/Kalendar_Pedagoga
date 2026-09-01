"""Сбор исходных данных для будущих строк календарного плана."""

from __future__ import annotations

from dataclasses import dataclass

from calendar_pedagoga.matching import MatchStatus, match_utp_to_program
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.program_parsing import ProgramData
from calendar_pedagoga.scheduling import ScheduleResult


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
    warnings: tuple[str, ...] = ()


def _preview(text: str, limit: int = 320) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_content_model(
    schedule: ScheduleResult,
    utp: UtpParseResult,
    program: ProgramData | None,
    source_utp_name: str,
) -> tuple[CalendarContentRow, ...]:
    """Связать календарные строки только с фактическими источниками."""
    matches = match_utp_to_program(utp.topics, program.content_items) if program else ()
    match_by_topic = {
        (match.utp_position.number, match.utp_position.title, match.utp_position.parent_section): match
        for match in matches
    }
    grouped: dict[tuple[int, str | None, str, str], dict[str, object]] = {}
    for element in schedule.elements:
        key = (element.week.number, element.topic_number, element.topic, element.section)
        if key not in grouped:
            grouped[key] = {"element": element, "theory": 0, "practice": 0}
        grouped[key][element.part_type] = int(grouped[key][element.part_type]) + element.hours

    rows: list[CalendarContentRow] = []
    for data in grouped.values():
        element = data["element"]
        topic_key = (element.topic_number, element.topic, element.section)
        match = match_by_topic.get(topic_key)
        program_item = match.program_item if match else None
        if program is None:
            warnings = ("Образовательная программа не загружена; содержание отсутствует.",)
        elif program_item is None:
            warnings = (f"Тема УТП «{element.topic}» не сопоставлена с программой.",)
        else:
            warnings = ()
        theory = int(data["theory"])
        practice = int(data["practice"])
        full_content = program_item.content if program_item else ""
        rows.append(
            CalendarContentRow(
                week_number=element.week.number,
                date_range=element.week.date_range,
                month=element.week.month,
                section=element.section,
                topic_number=element.topic_number,
                topic_title=element.topic,
                source_topic_title=element.topic,
                theory_hours=theory,
                practice_hours=practice,
                total_hours=theory + practice,
                match_status=match.status if match else MatchStatus.NOT_MATCHED,
                program_section=program_item.parent_section or "" if program_item else "",
                program_topic=program_item.title if program_item else "",
                program_content_full=full_content,
                program_content_preview=_preview(full_content),
                source_program_name=(program.title or "") if program else "",
                source_utp_name=source_utp_name,
                warnings=warnings,
            )
        )
    return tuple(rows)
