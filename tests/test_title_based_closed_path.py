from __future__ import annotations

from calendar_pedagoga.academic_year import APPROVED_ACADEMIC_YEAR
from calendar_pedagoga.content_engine_v2 import (
    PROVENANCE_GENERIC_ONLY,
    PROVENANCE_SENTENCE_FRAME_CLOSED,
    PROVENANCE_UNINFORMATIVE_TOPIC_TITLE,
    PROVENANCE_UTP_TOPIC_DERIVED,
    build_lesson_content_v2,
    is_sentence_frame_closed_row,
    is_utp_topic_derived_row,
)
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.docx_qa import (
    has_blocking_qa_issues,
    validate_calendar_docx,
    validate_calendar_docx_visual,
)
from calendar_pedagoga.generation_service import _decode_generation_payload
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.organization_template import select_calendar_template
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.pipeline import CalendarDocumentStatus, run_calendar_pipeline
from calendar_pedagoga.production_readiness import (
    EMPTY_CONTROL,
    EMPTY_RESULT,
    GENERIC_ONLY,
    SOURCE_NOT_MATCHED,
    UNINFORMATIVE_TOPIC_TITLE,
    production_readiness_codes,
)
from calendar_pedagoga.program_parsing import parse_program
from calendar_pedagoga.program_structure_confirmation import (
    confirm_embedded_utp_structure,
    confirm_program_structure,
    embedded_utp_candidates,
    needs_structure_confirmation,
    overlay_confirmed_program,
    select_embedded_utp,
)
from calendar_pedagoga.remote_generation import build_generation_payload
from calendar_pedagoga.resolve_utp import apply_workload_from_document
from calendar_pedagoga.scheduling import build_schedule
from calendar_pedagoga.sentence_frame import (
    LESSON_MODE_PRACTICE,
    LESSON_MODE_THEORY,
    TopicIntent,
    classify_topic_intent,
    render_sentence_frame,
    title_based_frame,
    title_is_informative,
)
from tests.docx_roundtrip import extract_logical_weeks
from tests.test_confirmed_program_overlay_wire import _holdout_confirmation, _roundtrip_payload
from tests.test_confirmed_slot_allocation import REFERENCES
from tests.test_remote_generation import REVISION
from tests.test_sentence_frame import _build, _part


def _title_row(*, topic: str, theory: int = 2, practice: int = 0):
    return {
        "schedule_id": "sch-0001",
        "number": "1",
        "topic": topic,
        "theory_hours": str(theory),
        "practice_hours": str(practice),
        "theory_content": "",
        "practice_content": "",
        "source_item_id": "",
        "excerpt": "",
        "content_origin": "",
        "topic_status": "UNRESOLVED",
        "source": "program",
        "match_status": MatchStatus.NOT_MATCHED.value,
    }


def test_informative_topic_without_source_is_utp_topic_derived() -> None:
    confirmation = confirm_program_structure(
        rows=(_title_row(topic="Лаборатория керамики", theory=2, practice=0),),
        study_year=1,
        study_weeks=1,
        hours_per_week="2",
        scope="title-derived",
    )
    overlaid = overlay_confirmed_program(None, confirmation.program_items)
    rows = build_content_model(
        build_schedule(confirmation.plan, APPROVED_ACADEMIC_YEAR),
        confirmation.plan,
        overlaid,
        "synthetic.docx",
        match_reviews=confirmation.match_reviews,
    )
    lessons = build_lesson_content_v2(rows)
    assert lessons
    row = lessons[0]
    assert is_sentence_frame_closed_row(row)
    assert is_utp_topic_derived_row(row)
    assert PROVENANCE_GENERIC_ONLY not in row.provenance_codes
    assert row.planned_result.strip()
    assert row.assessment_method.strip()
    assert "лаборатория керамики" in row.planned_result.casefold()
    assert not production_readiness_codes(row)


def test_uninformative_topic_requires_title_review_not_result_control() -> None:
    assert not title_is_informative("Практика")
    assert not title_is_informative("Продолжение")
    assert not title_is_informative("")
    confirmation = confirm_program_structure(
        rows=(_title_row(topic="Практика", theory=0, practice=2),),
        study_year=1,
        study_weeks=1,
        hours_per_week="2",
        scope="uninformative",
    )
    overlaid = overlay_confirmed_program(None, confirmation.program_items)
    rows = build_content_model(
        build_schedule(confirmation.plan, APPROVED_ACADEMIC_YEAR),
        confirmation.plan,
        overlaid,
        "synthetic.docx",
        match_reviews=confirmation.match_reviews,
    )
    lessons = build_lesson_content_v2(rows)
    row = lessons[0]
    assert is_sentence_frame_closed_row(row)
    assert PROVENANCE_UNINFORMATIVE_TOPIC_TITLE in row.provenance_codes
    assert PROVENANCE_UTP_TOPIC_DERIVED not in row.provenance_codes
    codes = production_readiness_codes(row)
    assert UNINFORMATIVE_TOPIC_TITLE in codes
    assert GENERIC_ONLY not in codes
    assert SOURCE_NOT_MATCHED not in codes
    assert EMPTY_RESULT not in codes
    assert EMPTY_CONTROL not in codes
    assert row.planned_result.strip()
    assert row.assessment_method.strip()
    from calendar_pedagoga.pipeline import _build_pipeline_lesson_content_outcome

    outcome = _build_pipeline_lesson_content_outcome(
        rows,
        use_content_engine_v2=True,
        semantic_revision="uninformative",
    )
    assert outcome.status is CalendarDocumentStatus.DRAFT_READY
    assert any(
        UNINFORMATIVE_TOPIC_TITLE in case.reasons for case in outcome.review_cases
    )


def test_manual_override_is_optional() -> None:
    confirmation = confirm_program_structure(
        rows=(_title_row(topic="Картография болот", theory=2, practice=0),),
        study_year=1,
        study_weeks=1,
        hours_per_week="2",
        scope="optional-override",
    )
    overlaid = overlay_confirmed_program(None, confirmation.program_items)
    rows = build_content_model(
        build_schedule(confirmation.plan, APPROVED_ACADEMIC_YEAR),
        confirmation.plan,
        overlaid,
        "synthetic.docx",
        match_reviews=confirmation.match_reviews,
    )
    from calendar_pedagoga.pipeline import _build_pipeline_lesson_content_outcome

    outcome = _build_pipeline_lesson_content_outcome(
        rows,
        use_content_engine_v2=True,
        semantic_revision="rev",
    )
    assert outcome.status is CalendarDocumentStatus.FINAL_READY
    assert outcome.review_cases == ()


def test_unknown_program_with_confirmed_utp_generates_without_manual_text() -> None:
    payload, filename, program, confirmation, overlaid = _holdout_confirmation()
    assert all(
        review["decision"] == "USER_CONFIRMED"
        for review in confirmation.match_reviews.values()
    )
    embedded = embedded_utp_candidates(payload, filename)
    selected = select_embedded_utp(embedded, 1)
    assert selected is not None
    assert needs_structure_confirmation(
        plan=None,
        program=program,
        embedded_utp_count=len(embedded),
        has_external_utp=False,
        study_year=1,
        has_unique_embedded_utp=True,
    ) is False
    auto = confirm_embedded_utp_structure(
        program=program,
        embedded=embedded,
        study_year=1,
        study_weeks=32,
        hours_per_week="3",
        scope="auto",
        source_items=program.content_items,
    )
    assert auto.plan.total_hours == 96
    empty_items = [item for item in auto.program_items if not (item.content or "").strip()]
    assert empty_items
    assert all(item.title.strip() for item in empty_items)
    rows = build_content_model(
        build_schedule(confirmation.plan, APPROVED_ACADEMIC_YEAR),
        confirmation.plan,
        overlaid,
        filename,
        match_reviews=confirmation.match_reviews,
    )
    lessons = build_lesson_content_v2(rows)
    assert all(row.planned_result.strip() and row.assessment_method.strip() for row in lessons)


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
    lessons = build_lesson_content_v2(rows)
    assert all(PROVENANCE_UTP_TOPIC_DERIVED not in row.provenance_codes for row in lessons)
    assert all(PROVENANCE_SENTENCE_FRAME_CLOSED not in row.provenance_codes for row in lessons)


def test_source_backed_sentence_frame_still_used_when_source_exists() -> None:
    row = _build(
        _part(
            topic_title="Аппликация",
            practice_hours=3,
            content="«Аппликация из семян».",
        )
    )
    assert PROVENANCE_SENTENCE_FRAME_CLOSED in row.provenance_codes
    assert "семян" in row.planned_result.casefold()
    assert PROVENANCE_UTP_TOPIC_DERIVED not in row.provenance_codes


def test_holdout_without_manual_topics_7_8_word_qa() -> None:
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
    docx_by_week = {week.week_number: week for week in weeks}
    week30 = docx_by_week[30]
    assert "подводит итоги работы по программе" in week30.planned_result.casefold()
    assert "выполняет" in week30.planned_result.casefold()
    assert "солёное тесто" in week30.planned_result.casefold()
    assert is_sentence_frame_closed_row(v2_by_week[30])
    for number in (31, 32):
        lesson = v2_by_week[number]
        docx_row = docx_by_week[number]
        assert lesson.planned_result == "Участвует в конкурсах, выставках и экскурсиях."
        assert lesson.assessment_method == (
            "Педагогическое наблюдение за участием в конкурсах, "
            "выставках и экскурсиях."
        )
        assert docx_row.planned_result == lesson.planned_result
        assert docx_row.assessment == lesson.assessment_method
        assert is_sentence_frame_closed_row(lesson)
        assert is_utp_topic_derived_row(lesson)
        assert PROVENANCE_GENERIC_ONLY not in lesson.provenance_codes
    assert result.status in {
        CalendarDocumentStatus.DRAFT_READY,
        CalendarDocumentStatus.FINAL_READY,
    }
    print(
        f"W30 v2={v2_by_week[30].planned_result!r} "
        f"docx={week30.planned_result!r} closed={is_sentence_frame_closed_row(v2_by_week[30])}"
    )
    print(
        f"W31 derived={is_utp_topic_derived_row(v2_by_week[31])} "
        f"result={v2_by_week[31].planned_result!r}"
    )
    print(
        f"W32 derived={is_utp_topic_derived_row(v2_by_week[32])} "
        f"result={v2_by_week[32].planned_result!r}"
    )


def test_participation_catalog_uses_proven_locative() -> None:
    topic = "Конкурсы, выставки, экскурсии"
    assert classify_topic_intent(topic, lesson_mode=LESSON_MODE_PRACTICE) is (
        TopicIntent.PARTICIPATION
    )
    frame = title_based_frame(LESSON_MODE_PRACTICE, topic)
    assert frame.intent is TopicIntent.PARTICIPATION
    result, control, fell_back = render_sentence_frame(frame)
    assert not fell_back
    assert result == "Участвует в конкурсах, выставках и экскурсиях."
    assert control == (
        "Педагогическое наблюдение за участием в конкурсах, "
        "выставках и экскурсиях."
    )


def test_unknown_catalog_uses_safe_participation_fallback() -> None:
    topic = "Конкурсы, хакатоны, экскурсии"
    assert classify_topic_intent(topic, lesson_mode=LESSON_MODE_PRACTICE) is (
        TopicIntent.PARTICIPATION
    )
    frame = title_based_frame(LESSON_MODE_PRACTICE, topic)
    result, control, _fell_back = render_sentence_frame(frame)
    quoted = "«Конкурсы, хакатоны, экскурсии»"
    assert result == f"Участвует в мероприятиях по теме {quoted}."
    assert control == (
        f"Педагогическое наблюдение за участием в мероприятиях по теме {quoted}."
    )
    assert "хакатон" not in result.casefold() or "мероприятиях" in result.casefold()


def test_summary_topic_uses_program_totals() -> None:
    topic = "Итоговое занятие"
    assert classify_topic_intent(topic, lesson_mode=LESSON_MODE_THEORY) is (
        TopicIntent.SUMMARY
    )
    frame = title_based_frame(LESSON_MODE_THEORY, topic)
    result, control, fell_back = render_sentence_frame(frame)
    assert not fell_back
    assert result == "Подводит итоги работы по программе."
    assert control == "Проверка итоговых работ."


def test_mixed_summary_and_practice_keeps_both_actions() -> None:
    row = _build(
        _part(
            topic_title="Солёное тесто",
            practice_hours=2,
            content="«Солёное тесто».",
        ),
        _part(
            topic_title="Итоговое занятие",
            theory_hours=1,
            content="",
        ),
    )
    assert "солёное тесто" in row.planned_result.casefold()
    assert "подводит итоги работы по программе" in row.planned_result.casefold()
    assert "выполняет" in row.planned_result.casefold()
    assert "характеризует" not in row.planned_result.casefold()
    assert PROVENANCE_SENTENCE_FRAME_CLOSED in row.provenance_codes


def test_practical_work_is_not_participation() -> None:
    topic = "Практическая работа"
    assert classify_topic_intent(topic, lesson_mode=LESSON_MODE_PRACTICE) is (
        TopicIntent.PRACTICAL_CREATION
    )
    making = "Плетение из проволоки"
    assert classify_topic_intent(making, lesson_mode=LESSON_MODE_PRACTICE) is (
        TopicIntent.PRACTICAL_CREATION
    )
    frame = title_based_frame(LESSON_MODE_PRACTICE, making)
    result, _control, _fell_back = render_sentence_frame(frame)
    assert result.startswith("Выполняет практическую работу по теме")
    assert "участвует" not in result.casefold()
    contest_making = "Изготовление конкурсных работ"
    assert classify_topic_intent(
        contest_making, lesson_mode=LESSON_MODE_PRACTICE
    ) is TopicIntent.PRACTICAL_CREATION


def test_theory_topic_is_knowledge_or_unknown_safe() -> None:
    knowledge = "Понятие цвета"
    assert classify_topic_intent(knowledge, lesson_mode=LESSON_MODE_THEORY) is (
        TopicIntent.KNOWLEDGE
    )
    frame = title_based_frame(LESSON_MODE_THEORY, knowledge)
    result, control, fell_back = render_sentence_frame(frame)
    assert not fell_back
    assert result == "Характеризует содержание темы «Понятие цвета»."
    assert control == "Устный опрос по теме «Понятие цвета»."
    unknown = "Лаборатория керамики"
    assert classify_topic_intent(unknown, lesson_mode=LESSON_MODE_THEORY) is (
        TopicIntent.UNKNOWN
    )
    unknown_frame = title_based_frame(LESSON_MODE_THEORY, unknown)
    unknown_result, unknown_control, _fell_back = render_sentence_frame(unknown_frame)
    assert unknown_result == "Характеризует содержание темы «Лаборатория керамики»."
    assert unknown_control == "Устный опрос по теме «Лаборатория керамики»."


def test_cross_domain_intents_without_program_hardcode() -> None:
    festival = "Фестиваль"
    assert classify_topic_intent(festival, lesson_mode=LESSON_MODE_PRACTICE) is (
        TopicIntent.PARTICIPATION
    )
    result, _control, _fell_back = render_sentence_frame(
        title_based_frame(LESSON_MODE_PRACTICE, festival)
    )
    assert result == "Участвует в фестивалях."
    history = "История книгопечатания"
    assert classify_topic_intent(history, lesson_mode=LESSON_MODE_THEORY) is (
        TopicIntent.KNOWLEDGE
    )
    optics = "Квантовая оптика"
    assert classify_topic_intent(optics, lesson_mode=LESSON_MODE_THEORY) is (
        TopicIntent.UNKNOWN
    )
    weaving = "Плетение"
    assert classify_topic_intent(weaving, lesson_mode=LESSON_MODE_PRACTICE) is (
        TopicIntent.PRACTICAL_CREATION
    )
