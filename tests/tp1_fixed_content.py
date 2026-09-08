"""Фикстура содержания TP1 по номеру темы, без live matching."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from calendar_pedagoga.content_generation import CalendarContentRow, WeekTopicPart
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.program_parsing import ProgramContentItem, parse_program
from calendar_pedagoga.resolve_utp import resolve_utp
from calendar_pedagoga.scheduling import build_schedule
from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload


REFERENCES = Path(__file__).resolve().parents[1] / "references"
_TP1_PROGRAM = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"


def _preview(text: str, limit: int = 320) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _items_by_number(items: tuple[ProgramContentItem, ...]) -> dict[str, ProgramContentItem]:
    found: dict[str, ProgramContentItem] = {}
    for item in items:
        if item.number and item.number not in found:
            found[item.number] = item
    return found


@lru_cache(maxsize=1)
def tp1_number_bound_content_rows() -> tuple[CalendarContentRow, ...]:
    """Строки CE2-oracle: текст программы по номеру темы, не через matching."""

    upload = validate_upload(
        UploadPurpose.PROGRAM,
        _TP1_PROGRAM.name,
        _TP1_PROGRAM.read_bytes(),
    )
    utp = resolve_utp(None, upload)
    program = parse_program(upload.content, upload.filename, study_year=1)
    schedule = build_schedule(utp, "2026–2027")
    by_number = _items_by_number(program.content_items)
    grouped: dict[tuple[int, str | None, str, str], dict[str, object]] = {}
    for element in schedule.elements:
        key = (element.week.number, element.topic_number, element.topic, element.section)
        if key not in grouped:
            grouped[key] = {"element": element, "theory": 0, "practice": 0}
        grouped[key][element.part_type] = int(grouped[key][element.part_type]) + element.hours

    topic_rows: list[tuple[int, WeekTopicPart, object]] = []
    for data in grouped.values():
        element = data["element"]
        item = by_number.get(element.topic_number or "")
        theory = int(data["theory"])
        practice = int(data["practice"])
        topic_rows.append(
            (
                element.week.number,
                WeekTopicPart(
                    topic_number=element.topic_number,
                    topic_title=element.topic,
                    section=element.section,
                    theory_hours=theory,
                    practice_hours=practice,
                    match_status=MatchStatus.EXACT if item else MatchStatus.NOT_MATCHED,
                    program_section=(item.parent_section or "") if item else "",
                    program_topic=item.title if item else "",
                    program_content_full=item.content if item else "",
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
        combined = "\n".join(
            part.program_content_full for part in parts if part.program_content_full
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
                program_content_full=combined,
                program_content_preview=_preview(combined),
                source_program_name=program.title or "",
                source_utp_name=_TP1_PROGRAM.name,
                week_parts=parts,
            )
        )
    return tuple(rows)
