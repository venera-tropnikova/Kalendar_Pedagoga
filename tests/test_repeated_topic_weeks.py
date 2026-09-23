"""Repeated title-based weeks get a catalog slice or a neutral stage."""

from __future__ import annotations

from dataclasses import replace

from calendar_pedagoga.content_engine_v2 import (
    build_lesson_content_v2,
    is_sentence_frame_closed_row,
)
from calendar_pedagoga.lesson_display import format_schedule_topic_cell
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.sentence_frame import (
    allocate_repeated_title_slots,
    frames_by_confirmed_parts,
    proven_catalog_members,
    render_confirmed_parts,
    render_sentence_frame,
)
from tests.test_sentence_frame import _part, _row

_UNKNOWN = "Семейная керамика"
_CATALOG = "Фестивали, слёты, соревнования"
_INVENTED = ("глина", "изделие", "материал", "операц", "поделк")


def _practice_rows(topic: str, count: int, **part_kwargs):
    rows = []
    for number in range(1, count + 1):
        part = _part(topic_title=topic, practice_hours=3, **part_kwargs)
        rows.append(replace(_row(part), week_number=number))
    return tuple(rows)


def _rendered(rows):
    slots = allocate_repeated_title_slots(rows)
    lessons = build_lesson_content_v2(rows)
    return slots, lessons


def test_two_and_three_title_slots_get_different_frames() -> None:
    for count in (2, 3):
        rows = _practice_rows(_UNKNOWN, count)
        slots, lessons = _rendered(rows)
        results = [lesson.planned_result for lesson in lessons]
        controls = [lesson.assessment_method for lesson in lessons]
        assert len(set(results)) == count
        assert len(set(controls)) == count
        for index, lesson in enumerate(lessons, start=1):
            assert f"этап {index} из {count}" in lesson.planned_result
            assert f"этапа {index} из {count}" in lesson.assessment_method
            assert is_sentence_frame_closed_row(lesson)
            slot = slots[(lesson.source.week_number, 0)]
            cell = format_schedule_topic_cell("4", slot.schedule_title, 3)
            assert f"Этап {index} из {count}" in cell
            assert cell.endswith("(3)")


def test_catalog_of_three_items_splits_across_two_slots() -> None:
    assert proven_catalog_members(_CATALOG) == (
        "Фестивали",
        "слёты",
        "соревнования",
    )
    rows = _practice_rows(_CATALOG, 2)
    slots, lessons = _rendered(rows)
    assert slots[(1, 0)].schedule_title == "Фестивали, слёты"
    assert slots[(2, 0)].schedule_title == "Соревнования"
    assert "фестивалях" in lessons[0].planned_result
    assert "слётах" in lessons[0].planned_result
    assert "соревнован" not in lessons[0].planned_result.casefold()
    assert lessons[1].planned_result == "Участвует в соревнованиях."
    assert "фестивал" not in lessons[1].planned_result.casefold()
    assert "слёт" not in lessons[1].planned_result.casefold()


def test_catalog_unit_is_not_broadcast() -> None:
    rows = _practice_rows(_CATALOG, 2)
    slots, lessons = _rendered(rows)
    schedules = [slot.schedule_title.casefold() for slot in slots.values()]
    results = [lesson.planned_result.casefold() for lesson in lessons]
    for member in ("фестивал", "слёт", "соревнован"):
        assert sum(member in text for text in schedules) == 1
        assert sum(member in text for text in results) == 1


def test_unknown_topic_gets_stages_without_invented_content() -> None:
    rows = _practice_rows(_UNKNOWN, 2)
    _slots, lessons = _rendered(rows)
    for lesson in lessons:
        text = f"{lesson.planned_result} {lesson.assessment_method}".casefold()
        assert _UNKNOWN.casefold() in text
        assert "этап" in text
        for invented in _INVENTED:
            assert invented not in text
        groups = frames_by_confirmed_parts(
            lesson.source,
            lesson.source.week_parts,
            allocate_repeated_title_slots(rows),
        )
        rendered, control, fell_back = render_confirmed_parts(groups)
        assert not fell_back
        assert lesson.planned_result == rendered
        assert lesson.assessment_method == control
        frame = groups[0][0]
        direct, direct_control, direct_fallback = render_sentence_frame(frame)
        assert not direct_fallback
        assert direct == rendered
        assert direct_control == control


def test_source_backed_frames_stay_unchanged() -> None:
    rows = _practice_rows(
        _UNKNOWN,
        2,
        content="«Открытка». Изготовление открытки.",
    )
    slots, lessons = _rendered(rows)
    assert slots == {}
    assert all("этап" not in lesson.planned_result.casefold() for lesson in lessons)
    assert "открытк" in lessons[0].planned_result.casefold()


def test_auto_path_repeated_title_is_not_staged() -> None:
    rows = []
    for number in (1, 2):
        part = _part(
            topic_title="Плетение из проволоки",
            practice_hours=3,
            content="«Плетение из проволоки».",
            status=MatchStatus.EXACT,
            assigned=False,
        )
        rows.append(
            replace(_row(part, match_status=MatchStatus.EXACT), week_number=number)
        )
    packed = tuple(rows)
    assert allocate_repeated_title_slots(packed) == {}
    lessons = build_lesson_content_v2(packed)
    assert all("этап" not in lesson.planned_result.casefold() for lesson in lessons)
    assert all(
        lesson.source.practice_hours == 3 and lesson.source.theory_hours == 0
        for lesson in lessons
    )


def test_mixed_week_keeps_every_part_and_hours() -> None:
    summary = _part(
        topic_title="Итоговое занятие",
        theory_hours=1,
        topic_number="7",
    )
    practice = _part(
        topic_title=_UNKNOWN,
        practice_hours=2,
        topic_number="6",
    )
    earlier = _part(topic_title=_UNKNOWN, practice_hours=3, topic_number="6")
    rows = (
        replace(_row(earlier), week_number=1),
        replace(_row(summary, practice), week_number=2),
    )
    _slots, lessons = _rendered(rows)
    assert len(lessons) == 2
    assert lessons[0].source.practice_hours == 3
    assert lessons[0].source.theory_hours == 0
    assert lessons[1].source.theory_hours == 1
    assert lessons[1].source.practice_hours == 2
    mixed = lessons[1].planned_result.casefold()
    assert "этап" in mixed
    assert _UNKNOWN.casefold() in mixed
    assert "подводит итоги работы по программе" in mixed
    assert len(lessons[1].source.week_parts) == 2
