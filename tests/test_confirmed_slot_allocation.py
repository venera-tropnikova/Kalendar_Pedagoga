from __future__ import annotations

from datetime import date
from pathlib import Path

from calendar_pedagoga.confirmed_slot_allocation import (
    UNRESOLVED_MIXED_NO_MARKERS,
    allocate_contiguous,
    assignment_records,
    assign_confirmed_topic_slots,
    split_confirmed_source,
)
from calendar_pedagoga.content_engine_v2 import (
    PROVENANCE_GENERIC_ONLY,
    build_lesson_content_v2,
    is_sentence_frame_closed_row,
    is_utp_topic_derived_row,
)
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.parsing import Hours, Topic, UtpMetadata, UtpParseResult, parse_utp
from calendar_pedagoga.program_parsing import ProgramContentItem, ProgramData, parse_program
from calendar_pedagoga.resolve_utp import apply_workload_from_document
from calendar_pedagoga.scheduling import AcademicWeek, ScheduledElement, ScheduleResult, build_schedule


REFERENCES = Path(__file__).resolve().parents[1] / "references"


def _item(title: str, content: str, number: str | None = "1") -> ProgramContentItem:
    return ProgramContentItem(number, title, content, title, 1)


def _program(*items: ProgramContentItem) -> ProgramData:
    return ProgramData(
        title="Синтетика",
        duration=None,
        student_age=None,
        goal=None,
        tasks=(),
        lesson_forms=(),
        teaching_methods=(),
        expected_results=(),
        knowledge_outcomes=(),
        skill_outcomes=(),
        content_items=items,
    )


def _topic(number: str, title: str, theory: int, practice: int) -> Topic:
    return Topic(number, title, Hours(theory + practice, theory, practice), title)


def _week(number: int) -> AcademicWeek:
    start = date(2026, 9, 1)
    return AcademicWeek(
        number,
        start,
        start,
        "Сентябрь",
        "2026–2027",
    )


def _schedule(topics: tuple[Topic, ...], layout: tuple[tuple[int, str, str, object], ...]) -> tuple[UtpParseResult, ScheduleResult]:
    weeks = tuple(_week(number) for number in sorted({item[0] for item in layout}))
    by_number = {week.number: week for week in weeks}
    elements = []
    for week_number, topic_number, part_type, hours in layout:
        topic = next(item for item in topics if item.number == topic_number)
        elements.append(
            ScheduledElement(
                topic.parent_section or topic.title,
                topic.number,
                topic.title,
                part_type,
                hours,
                by_number[week_number],
            )
        )
    utp = UtpParseResult(
        metadata=UtpMetadata(
            hours_per_week=3,
            hours_per_year=sum(topic.hours.total for topic in topics),
            study_weeks=len(weeks),
            workload_provenance="document",
        ),
        sections=(),
        topics=topics,
        table_totals=Hours(
            sum(topic.hours.total for topic in topics),
            sum(topic.hours.theory for topic in topics),
            sum(topic.hours.practice for topic in topics),
        ),
    )
    return utp, ScheduleResult(weeks=weeks, elements=tuple(elements), warnings=())


def _reviews(topics: tuple[Topic, ...]) -> dict:
    return {
        (topic.number, topic.title, topic.parent_section): {
            "decision": "USER_CONFIRMED",
            "item_ref": {
                "number": topic.number,
                "title": topic.title,
                "section": topic.parent_section,
                "study_year": 1,
            },
        }
        for topic in topics
    }


def test_each_unit_assigned_once_or_unresolved() -> None:
    source = (
        "Свойства глины. Цвет черепка. Обжиг.\n"
        "Практические работы:\n"
        "Темы: «Пластина», «Кулон», «Брошь», «Бусина»."
    )
    split = split_confirmed_source(source, topic_theory_hours=3, topic_practice_hours=6)
    assignments = assign_confirmed_topic_slots(
        source_content=source,
        match_statuses=(MatchStatus.USER_CONFIRMED,) * 3,
        already_assigned=(False, False, False),
        theory_hours=(3, 0, 0),
        practice_hours=(0, 3, 3),
    )
    assert assignments is not None
    records = assignment_records(assignments)
    units = [unit for unit, _channel, _index in records]
    assert len(units) == len(set(units))
    assert set(split.theory_units).issubset(set(units))
    assert set(split.practice_units).issubset(set(units))
    assigned_theory = [unit for unit, channel, _index in records if channel == "theory"]
    assigned_practice = [unit for unit, channel, _index in records if channel == "practice"]
    assert list(assigned_theory) == list(split.theory_units)
    assert list(assigned_practice) == list(split.practice_units)


def test_practice_catalog_never_enters_theory_slot() -> None:
    source = (
        "Фактура материала. Цветовой фон.\n"
        "Практические работы:\n"
        "Темы: «Изделие из семян», «Изделие из опила», «Изделие из ракушек»."
    )
    assignments = assign_confirmed_topic_slots(
        source_content=source,
        match_statuses=(MatchStatus.USER_CONFIRMED, MatchStatus.USER_CONFIRMED),
        already_assigned=(False, False),
        theory_hours=(2, 0),
        practice_hours=(0, 2),
    )
    assert assignments is not None
    theory = assignments[0].content.casefold()
    assert "изделие из семян" not in theory
    assert "изделие из опила" not in theory
    assert "изделие из ракушек" not in theory
    assert "«Изделие из семян»" in assignments[1].content


def test_multi_week_topic_does_not_broadcast_full_source() -> None:
    source = (
        "Первый теоретический тезис. Второй теоретический тезис. "
        "Третий теоретический тезис. Четвёртый теоретический тезис.\n"
        "Практика.\n"
        "Первое упражнение. Второе упражнение."
    )
    assignments = assign_confirmed_topic_slots(
        source_content=source,
        match_statuses=(MatchStatus.USER_CONFIRMED,) * 3,
        already_assigned=(False, False, False),
        theory_hours=(2, 2, 0),
        practice_hours=(0, 0, 2),
    )
    assert assignments is not None
    full = source.replace("\n", " ")
    for item in assignments:
        assert item.content
        assert item.content not in (source, full)
        assert source not in item.content


def test_mixed_week_keeps_independent_topic_segments() -> None:
    first = _topic("1", "Лаборатория керамики", 2, 0)
    second = _topic("2", "Картография болот", 0, 2)
    utp, schedule = _schedule(
        (first, second),
        (
            (1, "1", "theory", 2),
            (1, "2", "practice", 1),
            (2, "2", "practice", 1),
        ),
    )
    program = _program(
        _item(
            "Лаборатория керамики",
            "Состав шликера. Температура обжига.",
            "1",
        ),
        _item(
            "Картография болот",
            "Практические работы:\nТемы: «План участка», «Профиль болота».",
            "2",
        ),
    )
    rows = build_content_model(
        schedule,
        utp,
        program,
        "synthetic.docx",
        match_reviews=_reviews((first, second)),
    )
    week1 = next(row for row in rows if row.week_number == 1)
    assert len(week1.week_parts) == 2
    ceramic = next(part for part in week1.week_parts if part.topic_title == "Лаборатория керамики")
    swamp = next(part for part in week1.week_parts if part.topic_title == "Картография болот")
    assert "шликер" in ceramic.program_content_full.casefold()
    assert "план участка" not in ceramic.program_content_full.casefold()
    assert "профиль болота" not in ceramic.program_content_full.casefold()
    assert "план участка" in swamp.program_content_full.casefold()
    assert "шликер" not in swamp.program_content_full.casefold()
    assert "профиль болота" not in swamp.program_content_full.casefold()


def test_synthetic_unknown_program_unresolved_uses_generic_slot() -> None:
    topic = _topic("1", "Орнитология двора", 2, 2)
    utp, schedule = _schedule(
        (topic,),
        (
            (1, "1", "theory", 2),
            (2, "1", "practice", 2),
        ),
    )
    program = _program(
        _item(
            "Орнитология двора",
            "Наблюдение за птицами во дворе и ведение дневника.",
        )
    )
    rows = build_content_model(
        schedule,
        utp,
        program,
        "synthetic.docx",
        match_reviews=_reviews((topic,)),
    )
    assert all(part.weekly_content_assigned for row in rows for part in row.week_parts)
    assert any(
        UNRESOLVED_MIXED_NO_MARKERS in part.warnings
        for row in rows
        for part in row.week_parts
    )
    lessons = build_lesson_content_v2(rows)
    assert all(row.planned_result.strip() and row.assessment_method.strip() for row in lessons)
    assert all(is_sentence_frame_closed_row(row) for row in lessons)
    assert all(is_utp_topic_derived_row(row) for row in lessons)
    assert all(PROVENANCE_GENERIC_ONLY not in row.provenance_codes for row in lessons)


def test_known_program_auto_path_is_unchanged() -> None:
    utp_path = REFERENCES / "УТП ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    program_path = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    if not utp_path.exists() or not program_path.exists():
        utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
        program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    utp = apply_workload_from_document(parse_utp(utp_path))
    program = parse_program(
        program_path.read_bytes(),
        program_path.name,
        study_year=2,
    )
    rows = build_content_model(
        build_schedule(utp, "2026–2027"),
        utp,
        program,
        utp_path.name,
    )
    assert rows
    assert all(row.match_status is not MatchStatus.USER_CONFIRMED for row in rows)
    first_matched = next(
        row for row in rows if row.program_content_full.strip() and row.week_parts
    )
    assert first_matched.week_parts
    # Auto-path may still slice compact sections, but never via USER_CONFIRMED overlay.
    assert all(part.match_status is not MatchStatus.USER_CONFIRMED for part in first_matched.week_parts)


def test_contiguous_allocation_is_proportional_and_unique() -> None:
    units = tuple(f"u{index}" for index in range(9))
    slots = allocate_contiguous(units, (1000, 2000, 3000))
    flat = [item for slot in slots for item in slot]
    assert flat == list(units)
    assert [len(slot) for slot in slots] == [2, 3, 4]


def test_empty_theory_slot_stays_unresolved_not_broadcast() -> None:
    source = "Единственный тезис."
    assignments = assign_confirmed_topic_slots(
        source_content=source,
        match_statuses=(MatchStatus.USER_CONFIRMED, MatchStatus.USER_CONFIRMED),
        already_assigned=(False, False),
        theory_hours=(1, 2),
        practice_hours=(0, 0),
    )
    assert assignments is not None
    contents = [item.content for item in assignments]
    assert contents.count("Единственный тезис.") == 1
    assert any(not item.content for item in assignments)
    assert any(item.unresolved_reason for item in assignments)


def test_holdout_theory_weeks_do_not_receive_practice_catalog() -> None:
    from calendar_pedagoga.academic_year import APPROVED_ACADEMIC_YEAR
    from tests.test_confirmed_program_overlay_wire import _holdout_confirmation

    _payload, _name, _program, confirmation, overlaid = _holdout_confirmation()
    rows = build_content_model(
        build_schedule(confirmation.plan, APPROVED_ACADEMIC_YEAR),
        confirmation.plan,
        overlaid,
        "Подтверждённая структура программы",
        match_reviews=confirmation.match_reviews,
    )
    assert len(rows) == 32
    week1 = next(row for row in rows if row.week_number == 1)
    blob = "\n".join(part.program_content_full for part in week1.week_parts).casefold()
    assert "аппликация из макарон" not in blob
    assert "аппликацию из макарон" not in blob
    assert "аппликация из ракушек" not in blob
    by_topic: dict[tuple[str | None, str], list[str]] = {}
    for row in rows:
        for part in row.week_parts:
            by_topic.setdefault((part.topic_number, part.topic_title), []).append(
                part.program_content_full
            )
    for contents in by_topic.values():
        nonempty = [item for item in contents if item.strip()]
        if len(nonempty) < 2:
            continue
        assert len(set(nonempty)) > 1
