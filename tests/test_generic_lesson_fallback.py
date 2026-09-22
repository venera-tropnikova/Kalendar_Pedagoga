from __future__ import annotations

from dataclasses import replace

from calendar_pedagoga.content_engine_v2 import (
    PROVENANCE_GENERIC_ONLY,
    build_lesson_content_v2,
    derive_fields_v2,
    generic_lesson_fallback_frame,
    generic_lesson_fields_from_frame,
    is_generic_lesson_fallback_pair,
    is_generic_lesson_fallback_row,
)
from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.pipeline import (
    CalendarDocumentStatus,
    _build_pipeline_lesson_content_outcome,
)
from calendar_pedagoga.production_readiness import (
    EMPTY_CONTROL,
    EMPTY_RESULT,
    GENERIC_ONLY,
    SOURCE_NOT_MATCHED,
    production_readiness_codes,
)


def _row(**kwargs) -> CalendarContentRow:
    base = dict(
        week_number=1,
        date_range="01–07.09",
        month="Сентябрь",
        section="Самостоятельный раздел",
        topic_number="1",
        topic_title="Картография болот",
        source_topic_title="Картография болот",
        theory_hours=2,
        practice_hours=0,
        total_hours=2,
        match_status=MatchStatus.USER_CONFIRMED,
        program_section="Самостоятельный раздел",
        program_topic="Картография болот",
        program_content_full="Нельзя выполнять задание. Нельзя использовать материал.",
        program_content_preview="Нельзя выполнять задание. Нельзя использовать материал.",
        source_program_name="Программа неизвестного цикла",
        source_utp_name="synthetic.docx",
        warnings=(),
    )
    base.update(kwargs)
    return CalendarContentRow(**base)


def _build(row: CalendarContentRow):
    return build_lesson_content_v2((row,))[0]


def test_theory_fallback_comes_from_one_frame() -> None:
    topic = "Лаборатория керамики"
    frame = generic_lesson_fallback_frame(
        topic, theory_hours=2, practice_hours=0
    )
    result, control = generic_lesson_fields_from_frame(frame)
    assert frame.mode == "theory"
    assert result == "Характеризует содержание темы «Лаборатория керамики»."
    assert control == "Устный опрос по теме «Лаборатория керамики»."
    assert (result, control) == generic_lesson_fields_from_frame(frame)
    row = _build(
        _row(
            topic_title=topic,
            source_topic_title=topic,
            program_topic=topic,
            theory_hours=2,
            practice_hours=0,
            total_hours=2,
            program_content_full="Нельзя выполнять задание. Нельзя использовать материал.",
            program_content_preview="Нельзя выполнять задание. Нельзя использовать материал.",
        )
    )
    assert row.planned_result == result
    assert row.assessment_method == control
    assert row.action == frame.action
    assert row.object == frame.object
    assert PROVENANCE_GENERIC_ONLY in row.provenance_codes
    assert is_generic_lesson_fallback_row(row)


def test_practice_fallback_comes_from_one_frame() -> None:
    topic = "Орнитология двора"
    frame = generic_lesson_fallback_frame(
        topic, theory_hours=0, practice_hours=3
    )
    result, control = generic_lesson_fields_from_frame(frame)
    assert frame.mode == "practice"
    assert result == "Выполняет практическое задание по теме «Орнитология двора»."
    assert control == (
        "Педагогическое наблюдение за выполнением практического "
        "задания по теме «Орнитология двора»."
    )
    row = _build(
        _row(
            topic_title=topic,
            source_topic_title=topic,
            program_topic=topic,
            theory_hours=0,
            practice_hours=3,
            total_hours=3,
            program_content_full="Нельзя выполнять задание. Нельзя использовать материал.",
            program_content_preview="Нельзя выполнять задание. Нельзя использовать материал.",
        )
    )
    assert row.planned_result == result
    assert row.assessment_method == control
    assert is_generic_lesson_fallback_pair(
        topic,
        theory_hours=0,
        practice_hours=3,
        planned_result=row.planned_result,
        assessment_method=row.assessment_method,
    )


def test_mixed_week_fallback_comes_from_one_frame() -> None:
    topic = "Фонетика жестов"
    frame = generic_lesson_fallback_frame(
        topic, theory_hours=1, practice_hours=2
    )
    result, control = generic_lesson_fields_from_frame(frame)
    assert frame.mode == "mixed"
    assert result == (
        "Характеризует содержание темы «Фонетика жестов» и выполняет "
        "практическое задание по этой теме."
    )
    assert control == (
        "Устный опрос и педагогическое наблюдение за выполнением "
        "практического задания по теме «Фонетика жестов»."
    )
    row = _build(
        _row(
            topic_title=topic,
            source_topic_title=topic,
            program_topic=topic,
            theory_hours=1,
            practice_hours=2,
            total_hours=3,
            program_content_full="Нельзя выполнять задание. Нельзя использовать материал.",
            program_content_preview="Нельзя выполнять задание. Нельзя использовать материал.",
        )
    )
    assert row.planned_result == result
    assert row.assessment_method == control
    assert GENERIC_ONLY in production_readiness_codes(row)
    outcome = _build_pipeline_lesson_content_outcome(
        (row.source,),
        use_content_engine_v2=True,
        semantic_revision="generic-fallback",
    )
    assert outcome.status is CalendarDocumentStatus.DRAFT_READY
    assert GENERIC_ONLY in outcome.review_cases[0].reasons
    assert EMPTY_RESULT not in outcome.review_cases[0].reasons
    assert EMPTY_CONTROL not in outcome.review_cases[0].reasons


def test_fallback_clears_leftover_covered_clauses() -> None:
    row = _build(_row())
    assert row.clause_coverage == ()
    assert GENERIC_ONLY in production_readiness_codes(row)
    leftover = replace(row, clause_coverage=(("Нельзя выполнять задание", "COVERED"),))
    assert GENERIC_ONLY not in production_readiness_codes(leftover)


def test_generic_only_blocks_final_ready() -> None:
    row = _build(_row())
    assert GENERIC_ONLY in row.provenance_codes
    assert production_readiness_codes(row) == (GENERIC_ONLY,)
    outcome = _build_pipeline_lesson_content_outcome(
        (row.source,),
        use_content_engine_v2=True,
        semantic_revision="generic-ready",
    )
    assert outcome.status is CalendarDocumentStatus.DRAFT_READY


def test_proven_ce2_result_is_not_replaced() -> None:
    derived = derive_fields_v2(
        topic_title="Укладка рюкзака",
        theory_text="",
        practice_text="Выполнение упражнения.",
        program_content="Выполнение упражнения.",
        theory_hours=0,
        practice_hours=2,
    )
    assert derived.planned_result == "Выполняет упражнение."
    assert derived.assessment_method
    assert PROVENANCE_GENERIC_ONLY not in derived.provenance_codes
    row = _build(
        _row(
            topic_title="Укладка рюкзака",
            source_topic_title="Укладка рюкзака",
            program_topic="Укладка рюкзака",
            theory_hours=0,
            practice_hours=2,
            total_hours=2,
            program_content_full="Выполнение упражнения.",
            program_content_preview="Выполнение упражнения.",
        )
    )
    assert row.planned_result == "Выполняет упражнение."
    assert PROVENANCE_GENERIC_ONLY not in row.provenance_codes
    assert not row.planned_result.startswith("Выполняет практическое задание по теме")
    assert not row.planned_result.startswith("Характеризует содержание темы")


def test_unconfirmed_source_is_not_masked() -> None:
    row = _build(
        _row(
            match_status=MatchStatus.NOT_MATCHED,
            program_content_full="",
            program_content_preview="",
            program_topic="",
        )
    )
    codes = production_readiness_codes(row)
    assert SOURCE_NOT_MATCHED in codes
    assert not is_generic_lesson_fallback_row(row)
    assert not is_generic_lesson_fallback_pair(
        row.source.topic_title,
        theory_hours=row.source.theory_hours,
        practice_hours=row.source.practice_hours,
        planned_result=row.planned_result,
        assessment_method=row.assessment_method,
    )


def test_no_thematic_dictionary_in_fallback_module() -> None:
    from pathlib import Path
    import calendar_pedagoga.content_engine_v2 as module

    text = Path(module.__file__).read_text(encoding="utf-8")
    forbidden = (
        "природн",
        "аппликац",
        "скульптур",
        "плетён",
        "плетен",
        "солён",
        "солен",
        "турист",
        "ключ",
        "лазан",
    )
    start = text.index("class GenericLessonFrame")
    end = text.index("def generic_fallback_fields_for_row")
    chunk = text[start:end].casefold()
    for needle in forbidden:
        assert needle not in chunk
