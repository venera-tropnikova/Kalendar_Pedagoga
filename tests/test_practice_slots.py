"""Этап 1: слоты практики только для повторяющихся тем (W > 1)."""

from pathlib import Path

from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.practice_slots import (
    assign_practice_slots,
    format_slot_practice_text,
    practice_units_from_content,
    slot_is_continuation,
)
from calendar_pedagoga.program_parsing import infer_study_year_number, parse_program
from calendar_pedagoga.resolve_utp import apply_workload_from_document, resolve_utp
from calendar_pedagoga.scheduling import build_schedule
from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload
from test_ce2_grounded_triad import CE2_TP1_WEEK_SNAPSHOT


REFERENCES = Path(__file__).resolve().parents[1] / "references"


def test_assign_slots_covers_all_units_when_c_greater_than_w() -> None:
    units = [f"u{index}" for index in range(12)]
    slots = assign_practice_slots(units, 3)
    assert slots == (
        ("u0", "u1", "u2", "u3"),
        ("u4", "u5", "u6", "u7"),
        ("u8", "u9", "u10", "u11"),
    )
    assert [item for slot in slots for item in slot] == units


def test_assign_slots_one_unit_when_c_equals_w() -> None:
    units = ["a", "b", "c", "d"]
    slots = assign_practice_slots(units, 4)
    assert slots == (("a",), ("b",), ("c",), ("d",))
    assert not slot_is_continuation(slots, 3)


def test_assign_slots_continues_last_when_c_less_than_w() -> None:
    units = ["a", "b", "c", "d"]
    slots = assign_practice_slots(units, 5)
    assert slots == (("a",), ("b",), ("c",), ("d",), ("d",))
    assert slot_is_continuation(slots, 4)
    assert not slot_is_continuation(slots, 3)
    assert "Продолжение." in format_slot_practice_text(slots[4], continuation=True)
    assert "Продолжение." not in format_slot_practice_text(slots[3], continuation=False)


def test_tp1_repeated_topics_use_slots_not_modulo() -> None:
    source = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    upload = validate_upload(UploadPurpose.PROGRAM, source.name, source.read_bytes())
    utp = resolve_utp(None, upload)
    program = parse_program(upload.content, upload.filename, study_year=1)
    generated = build_lesson_content_v2(
        build_content_model(build_schedule(utp, "2026–2027"), utp, program, source.name)
    )
    ofp = [lesson for lesson in generated if lesson.source.topic_number == "5.3"]
    sfp = [lesson for lesson in generated if lesson.source.topic_number == "5.4"]
    excursion = [lesson for lesson in generated if lesson.source.topic_number == "3.2"]
    assert len(ofp) == 3
    assert len(sfp) == 5
    assert len(excursion) == 2
    joined = " ".join(item.planned_result.casefold() for item in ofp)
    for fragment in (
        "рук и плечевого пояса",
        "мышц шеи",
        "туловища и ног",
        "сопротивлением",
        "скакалкой",
        "акробатики",
        "эстафетах",
        "легкой атлетикой",
        "лыжным спортом",
        "гимнастические",
        "баскетбол",
        "плавания",
    ):
        assert fragment in joined
    assert sfp[4].planned_result == sfp[3].planned_result
    assert "выносливости" not in sfp[4].planned_result.casefold()
    assert SLOT_CONTINUE_IN(sfp[4].warnings)
    assert excursion[0].planned_result == excursion[1].planned_result
    assert excursion[0].planned_result == CE2_TP1_WEEK_SNAPSHOT[18][2]
    assert SLOT_CONTINUE_IN(excursion[1].warnings)
    week1 = generated[1]
    assert week1.source.topic_number == "1.3"
    assert week1.planned_result == CE2_TP1_WEEK_SNAPSHOT[1][2]


def SLOT_CONTINUE_IN(warnings: tuple[str, ...]) -> bool:
    return any("продолжение" in item.casefold() for item in warnings)


def _first_repeated_mismatch(generated, program):
    items = {item.number: item for item in program.content_items if item.number}
    counts: dict[str, int] = {}
    lessons_by: dict[str, list] = {}
    for lesson in generated:
        number = lesson.source.topic_number
        if not number or lesson.source.practice_hours <= 0:
            continue
        counts[number] = counts.get(number, 0) + 1
        lessons_by.setdefault(number, []).append(lesson)
    for number, count in counts.items():
        if count <= 1:
            continue
        item = items.get(number)
        if item is None:
            continue
        units = practice_units_from_content(
            item.content,
            theory_hours=0,
            practice_hours=2,
        )
        if len(units) != count and units:
            return number, count, units, lessons_by[number]
    return None


def test_key_has_repeated_topic_slot_coverage() -> None:
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    utp = apply_workload_from_document(parse_utp(utp_path))
    program = parse_program(
        program_path.read_bytes(),
        program_path.name,
        study_year=infer_study_year_number(utp.metadata.study_year) or 2,
    )
    generated = build_lesson_content_v2(
        build_content_model(build_schedule(utp, "2026–2027"), utp, program, utp_path.name)
    )
    found = _first_repeated_mismatch(generated, program)
    assert found is not None
    _number, w, units, lessons = found
    slots = assign_practice_slots(units, w)
    flattened = [item for slot in slots for item in slot]
    assert flattened[: len(units)] == units
    assert len(slots) == w
    if len(units) < w:
        assert lessons[-1].planned_result == lessons[len(units) - 1].planned_result
        assert any(
            "продолжение" in " ".join(lesson.warnings).casefold() for lesson in lessons
        )
    if len(units) > w:
        assert sum(len(slot) for slot in slots) == len(units)


def test_tp3_repeated_hours_keep_grid_without_inventing_clauses() -> None:
    utp_path = REFERENCES / "УТП ТП 3г. 2ч.docx"
    utp = apply_workload_from_document(parse_utp(utp_path))
    schedule = build_schedule(utp, "2026–2027")
    ofp = [element for element in schedule.elements if element.topic_number == "5.2"]
    assert sum(element.hours for element in ofp) == 10
    weeks = sorted({element.week.number for element in ofp})
    assert len(weeks) == 5
    generated = build_lesson_content_v2(
        build_content_model(schedule, utp, None, utp_path.name)
    )
    rows = [lesson for lesson in generated if lesson.source.topic_number == "5.2"]
    assert len(rows) == 5
    assert all(lesson.planned_result for lesson in rows)
    assert sum(lesson.source.practice_hours for lesson in rows) == 10
