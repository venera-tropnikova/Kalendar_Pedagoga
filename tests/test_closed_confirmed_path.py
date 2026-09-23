from __future__ import annotations

from dataclasses import replace

from calendar_pedagoga.academic_year import APPROVED_ACADEMIC_YEAR
from calendar_pedagoga.confirmed_slot_allocation import (
    _expand_channel_units,
    assignment_records,
    assign_confirmed_topic_slots,
    format_allocated_units,
)
from calendar_pedagoga.content_engine_v2 import (
    PROVENANCE_GENERIC_ONLY,
    PROVENANCE_SENTENCE_FRAME_CLOSED,
    build_lesson_content_v2,
    generic_fallback_fields_for_row,
    is_sentence_frame_closed_row,
)
from calendar_pedagoga.content_generation import CalendarContentRow, WeekTopicPart, build_content_model
from calendar_pedagoga.docx_qa import (
    has_blocking_qa_issues,
    validate_calendar_docx,
    validate_calendar_docx_visual,
)
from calendar_pedagoga.generation_service import _decode_generation_payload
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.organization_template import select_calendar_template
from calendar_pedagoga.pipeline import CalendarDocumentStatus, run_calendar_pipeline
from calendar_pedagoga.remote_generation import build_generation_payload
from calendar_pedagoga.scheduling import build_schedule
from calendar_pedagoga.semantic_review import (
    review_proposal_docx_issues,
    source_grounded_review_proposal,
)
from calendar_pedagoga.lesson_display import brief_allocated_work_labels
from calendar_pedagoga.sentence_frame import (
    allocate_repeated_title_slots,
    display_source_units_for_part,
    frames_by_confirmed_parts,
    frames_for_confirmed_part,
    render_confirmed_parts,
    weekly_source_topic,
)
from tests.docx_roundtrip import extract_logical_weeks
from tests.test_confirmed_program_overlay_wire import _holdout_confirmation, _roundtrip_payload
from tests.test_remote_generation import REVISION
from tests.test_semantic_review_core import _docx_rows_for, _review_row


def _part(
    *,
    topic_title: str,
    theory_hours: int = 0,
    practice_hours: int = 0,
    content: str = "",
    section: str = "Раздел",
    topic_number: str = "1",
    assigned: bool = True,
    status: MatchStatus = MatchStatus.USER_CONFIRMED,
    theory_units: tuple[str, ...] = (),
    practice_units: tuple[str, ...] = (),
) -> WeekTopicPart:
    return WeekTopicPart(
        topic_number=topic_number,
        topic_title=topic_title,
        section=section,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        match_status=status,
        program_section=section,
        program_topic=topic_title,
        program_content_full=content,
        weekly_content_assigned=assigned,
        theory_units=theory_units,
        practice_units=practice_units,
    )


def _row(*parts: WeekTopicPart) -> CalendarContentRow:
    theory_hours = sum(part.theory_hours for part in parts)
    practice_hours = sum(part.practice_hours for part in parts)
    content = "\n".join(
        part.program_content_full for part in parts if part.program_content_full
    )
    return CalendarContentRow(
        week_number=1,
        date_range="01–07.09",
        month="Сентябрь",
        section=parts[0].section,
        topic_number=parts[0].topic_number,
        topic_title=parts[0].topic_title,
        source_topic_title=parts[0].topic_title,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        total_hours=theory_hours + practice_hours,
        match_status=MatchStatus.USER_CONFIRMED,
        program_section=parts[0].program_section,
        program_topic=parts[0].topic_title,
        program_content_full=content,
        program_content_preview=content,
        source_program_name="Программа",
        source_utp_name="synthetic.docx",
        week_parts=parts,
    )


def test_closed_pair_unchanged_in_draft_resolved_rows(monkeypatch) -> None:
    planned = "Выполняет практическую работу по теме «Итоговое занятие»."
    control = (
        "Педагогическое наблюдение в ходе выполнения задания "
        "по теме «Итоговое занятие»."
    )
    row = replace(
        _review_row(),
        planned_result=planned,
        assessment_method=control,
        provenance_codes=(PROVENANCE_SENTENCE_FRAME_CLOSED, PROVENANCE_GENERIC_ONLY),
        warnings=("NEEDS_REVIEW: CONTROL не покрывает RESULT",),
    )
    assert is_sentence_frame_closed_row(row)

    def _blocked(*_args, **_kwargs):
        raise AssertionError("CLOSED pair must not call derive_fields_v2")

    monkeypatch.setattr(
        "calendar_pedagoga.semantic_review.derive_fields_v2", _blocked
    )
    monkeypatch.setattr(
        "calendar_pedagoga.content_engine_v2.generic_fallback_fields_for_row",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("CLOSED pair must not use generic fallback")
        ),
    )
    assert review_proposal_docx_issues(row) == ()
    assert source_grounded_review_proposal(row) is None
    assert generic_fallback_fields_for_row(row) is None
    marked, _cases = _docx_rows_for(row)
    assert marked[0].planned_result == planned
    assert marked[0].assessment_method == control


def test_mixed_week_two_parts_keep_both_frames() -> None:
    theory = _part(
        topic_title="Порядок на столе",
        theory_hours=1,
        content="Поддержание порядка на рабочем месте.",
        topic_number="6",
    )
    practice = _part(
        topic_title="Декоративные работы",
        practice_hours=2,
        content="«Аппликация из семян».",
        topic_number="7",
        section="Другой раздел",
    )
    row = _row(theory, practice)
    groups = frames_by_confirmed_parts(row, (theory, practice))
    assert len(groups) == 2
    assert groups[0][0].topic == "Порядок на столе"
    assert groups[1][0].topic == "Декоративные работы"
    result, control, _fallback = render_confirmed_parts(groups)
    assert "Поддерживает порядок" in result
    assert "аппликацию из семян" in result.casefold()
    assert "Порядок на столе" not in groups[1][0].topic
    assert "за поддержанием порядка" in control.casefold()
    assert "аппликации из семян" in control.casefold()
    built = build_lesson_content_v2((row,))[0]
    assert "Поддерживает порядок" in built.planned_result
    assert "аппликацию из семян" in built.planned_result.casefold()
    assert PROVENANCE_SENTENCE_FRAME_CLOSED in built.provenance_codes


def test_display_source_units_match_sentence_frame_units() -> None:
    units = ("«Изделие из семян»", "«Изделие из опила»")
    part = _part(
        topic_title="Аппликация",
        practice_hours=3,
        content=format_allocated_units(units),
        practice_units=units,
    )
    display = display_source_units_for_part(part)
    frames = frames_for_confirmed_part(part, topic=weekly_source_topic(part))
    frame_units = tuple(frame.source_span for frame in frames if frame.source_span)
    assert display == units
    assert frame_units == units
    cell = format_allocated_units(display)
    assert "семян" in cell.casefold()
    assert "опил" in cell.casefold()


def test_six_catalog_units_in_five_slots_are_kept() -> None:
    source = (
        "Практические работы:\n"
        "Темы: «Альфа», «Бета», «Гамма», «Дельта», «Эпсилон», «Дзета»."
    )
    assignments = assign_confirmed_topic_slots(
        source_content=source,
        match_statuses=(MatchStatus.USER_CONFIRMED,) * 5,
        already_assigned=(False,) * 5,
        theory_hours=(0,) * 5,
        practice_hours=(1,) * 5,
    )
    assert assignments is not None
    records = assignment_records(assignments)
    assigned_units = [unit for unit, _channel, _index in records]
    assert len(assigned_units) == 6
    assert len(set(assigned_units)) == 6
    displayed: list[str] = []
    for item in assignments:
        cell = format_allocated_units(item.practice_units)
        for unit in item.practice_units:
            assert unit.rstrip(" .") in cell
            displayed.append(unit)
        assert not item.unresolved_reason
    assert displayed == assigned_units
    assert sum(1 for item in assignments if not item.practice_units) == 0


def test_quoted_title_with_period_is_not_split() -> None:
    units = _expand_channel_units("Продолжение. «Открытка. Зима».")
    assert units == ("«Открытка. Зима»",)
    whole = _expand_channel_units("«Продолжение. Открытка»")
    assert whole == ("«Продолжение. Открытка»",)
    assert all(unit.count("«") == unit.count("»") == 1 for unit in (*units, *whole))
    assert not any(unit.startswith("Открытка»") for unit in (*units, *whole))


def test_json_remote_roundtrip_preserves_closed_path() -> None:
    payload_bytes, filename, _program, confirmation, overlaid = _holdout_confirmation()
    wire = build_generation_payload(
        confirmation.plan,
        academic_year=APPROVED_ACADEMIC_YEAR,
        source_plan_name="Подтверждённая структура программы",
        program_filename=filename,
        program_content=payload_bytes,
        match_reviews=confirmation.match_reviews,
        generator_revision=REVISION,
        program=overlaid,
    )
    decoded = _decode_generation_payload(_roundtrip_payload(wire))
    content = build_content_model(
        build_schedule(decoded.plan, APPROVED_ACADEMIC_YEAR),
        decoded.plan,
        decoded.program,
        decoded.source_plan_name,
        match_reviews=decoded.match_reviews,
    )
    v2 = build_lesson_content_v2(content)
    confirmed = [
        row
        for row in v2
        if any(part.weekly_content_assigned for part in row.source.week_parts)
    ]
    assert confirmed
    assert all(is_sentence_frame_closed_row(row) for row in confirmed)
    result = run_calendar_pipeline(
        decoded.plan,
        decoded.program,
        academic_year=APPROVED_ACADEMIC_YEAR,
        template=select_calendar_template(),
        source_utp_name=decoded.source_plan_name,
        use_ai=False,
        program_filename=decoded.program_filename,
        match_reviews=decoded.match_reviews,
        semantic_revision=REVISION,
    )
    weeks = extract_logical_weeks(result.content)
    by_week = {row.source.week_number: row for row in v2}
    for week in weeks:
        closed = by_week[week.week_number]
        if not is_sentence_frame_closed_row(closed):
            continue
        assert week.planned_result == closed.planned_result
        assert week.assessment == closed.assessment_method


def test_holdout_live_equivalent_closed_path_word_qa() -> None:
    payload_bytes, filename, _program, confirmation, overlaid = _holdout_confirmation()
    wire = build_generation_payload(
        confirmation.plan,
        academic_year=APPROVED_ACADEMIC_YEAR,
        source_plan_name="Подтверждённая структура программы",
        program_filename=filename,
        program_content=payload_bytes,
        match_reviews=confirmation.match_reviews,
        generator_revision=REVISION,
        program=overlaid,
    )
    decoded = _decode_generation_payload(_roundtrip_payload(wire))
    content = build_content_model(
        build_schedule(decoded.plan, APPROVED_ACADEMIC_YEAR),
        decoded.plan,
        decoded.program,
        decoded.source_plan_name,
        match_reviews=decoded.match_reviews,
    )
    v2 = build_lesson_content_v2(content)
    result = run_calendar_pipeline(
        decoded.plan,
        decoded.program,
        academic_year=APPROVED_ACADEMIC_YEAR,
        template=select_calendar_template(),
        source_utp_name=decoded.source_plan_name,
        use_ai=False,
        program_filename=decoded.program_filename,
        match_reviews=decoded.match_reviews,
        semantic_revision=REVISION,
    )
    weeks = extract_logical_weeks(result.content)
    empty_result = [week.week_number for week in weeks if not week.planned_result.strip()]
    empty_control = [week.week_number for week in weeks if not week.assessment.strip()]
    assert decoded.plan.total_hours == 96
    assert len(weeks) == 32
    assert empty_result == []
    assert empty_control == []
    qa_issues = validate_calendar_docx(result.content, expected_weeks=32)
    visual_issues = validate_calendar_docx_visual(result.content)
    assert not has_blocking_qa_issues((*qa_issues, *visual_issues))

    v2_by_week = {row.source.week_number: row for row in v2}
    content_by_week = {row.week_number: row for row in content}
    docx_by_week = {week.week_number: week for week in weeks}
    title_slots = allocate_repeated_title_slots(content)
    for number in (3, 23, 24, 30, 31, 32):
        source_row = content_by_week[number]
        groups = frames_by_confirmed_parts(
            source_row, source_row.week_parts, title_slots
        )
        rendered, control, _fallback = render_confirmed_parts(groups)
        v2_row = v2_by_week[number]
        docx_row = docx_by_week[number]
        units = tuple(
            unit
            for part in source_row.week_parts
            for unit in display_source_units_for_part(part)
        )
        print(
            f"W{number} allocated={units!r} "
            f"frames={[tuple(frame.source_span for frame in group) for group in groups]!r} "
            f"v2_result={v2_row.planned_result!r} v2_control={v2_row.assessment_method!r} "
            f"docx_theory={docx_row.theory!r} docx_practice={docx_row.practice!r} "
            f"docx_result={docx_row.planned_result!r} docx_control={docx_row.assessment!r} "
            f"closed={is_sentence_frame_closed_row(v2_row)}"
        )
        assert v2_row.planned_result == rendered
        assert v2_row.assessment_method == control
        assert docx_row.planned_result == v2_row.planned_result
        assert docx_row.assessment == v2_row.assessment_method
        assert is_sentence_frame_closed_row(v2_row)

    def _units_for_week(number: int) -> tuple[str, ...]:
        return tuple(
            unit
            for part in content_by_week[number].week_parts
            for unit in display_source_units_for_part(part)
        )

    def _assert_no_descriptive_source_dump(week, units: tuple[str, ...]) -> None:
        blob = f"{week.theory}\n{week.practice}".casefold()
        for unit in units:
            text = unit.strip()
            if not text:
                continue
            if brief_allocated_work_labels((text,)):
                continue
            if text.startswith(("«", "„", '"')) and text.endswith(("»", "“", '"')):
                continue
            if len(text.split()) < 8:
                continue
            assert text.casefold() not in blob

    week3 = docx_by_week[3]
    week3_units = _units_for_week(3)
    assert len([unit for unit in week3_units if unit]) >= 2
    assert brief_allocated_work_labels(week3_units) == (
        "«Аппликация из семян»",
        "«Аппликация из опила»",
    )
    assert week3.practice == "«Аппликация из семян». «Аппликация из опила» (3)"
    _assert_no_descriptive_source_dump(week3, week3_units)
    for unit in week3_units:
        token = unit.strip(" «».").split()[-1].casefold()
        if token:
            assert token[:4] in week3.planned_result.casefold() or token in week3.planned_result.casefold()

    week23 = docx_by_week[23]
    week23_units = _units_for_week(23)
    assert any("Открытка" in unit for unit in week23_units)
    assert all(unit.count("«") == unit.count("»") for unit in week23_units)
    assert not any(
        "Открытка" in unit and ("«" not in unit or unit.strip().startswith("Открытка»"))
        for unit in week23_units
    )
    assert week23.practice == "«Изонить. Открытка» (3)"
    assert "Открытка" in week23.practice or "Открытка" in week23.planned_result
    _assert_no_descriptive_source_dump(week23, week23_units)

    week15 = docx_by_week[15]
    week15_row = v2_by_week[15]
    assert week15.practice == "«Украшения в технике папье-маше» (3)"
    assert (
        week15_row.planned_result
        == "Выполняет практическую работу «Украшения в технике папье-маше»."
    )
    assert (
        week15_row.assessment_method
        == "Педагогическое наблюдение за выполнением практической работы "
        "«Украшения в технике папье-маше»."
    )
    assert week15.planned_result == week15_row.planned_result
    assert week15.assessment == week15_row.assessment_method
    assert PROVENANCE_GENERIC_ONLY not in week15_row.provenance_codes
    assert result.status is CalendarDocumentStatus.FINAL_READY
    assert [case.week_number for case in result.review_cases if case.blocks_delivery] == []
    assert sorted(
        case.week_number for case in result.review_cases if not case.blocks_delivery
    ) == [1, 2, 8, 23]

    week24 = docx_by_week[24]
    assert week24.practice.startswith("5. Плетение.")
    assert week24.practice.endswith("(3)")
    assert "Этап" in week24.practice
    assert "изонить" not in week24.practice.casefold()
    _assert_no_descriptive_source_dump(week24, _units_for_week(24))

    week30 = v2_by_week[30]
    assert docx_by_week[30].theory == "7. Итоговое занятие. (2)"
    assert docx_by_week[30].practice.startswith("6. Солёное тесто")
    assert docx_by_week[30].practice.endswith("(1)")
    assert "выполняет" in week30.planned_result.casefold()
    assert "солёное тесто" in week30.planned_result.casefold()
    assert "подводит итоги работы по программе" in week30.planned_result.casefold()
    assert week30.assessment_method == docx_by_week[30].assessment
    assert "анализирует выполненные работы" not in week30.assessment_method.casefold()
    assert is_sentence_frame_closed_row(week30)
    _assert_no_descriptive_source_dump(docx_by_week[30], _units_for_week(30))

    catalog_weeks = []
    for number in (31, 32):
        week = docx_by_week[number]
        assert week.theory == ""
        assert week.practice.startswith("8. ")
        assert week.practice.endswith("(3)")
        catalog_weeks.append(week.practice.casefold())
        _assert_no_descriptive_source_dump(week, _units_for_week(number))
    assert catalog_weeks[0] != catalog_weeks[1]
    assert "конкурс" in catalog_weeks[0] and "выставк" in catalog_weeks[0]
    assert "экскурси" not in catalog_weeks[0]
    assert "экскурси" in catalog_weeks[1]
    assert "конкурс" not in catalog_weeks[1]

    assert result.status in {
        CalendarDocumentStatus.DRAFT_READY,
        CalendarDocumentStatus.FINAL_READY,
    }
