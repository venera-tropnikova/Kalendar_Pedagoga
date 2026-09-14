# -*- coding: utf-8 -*-
"""Universal RESULT/CONTROL catalogue compression and FINAL verbosity gate."""

from __future__ import annotations

import re

import pytest

from calendar_pedagoga.content_engine_v2 import (
    LessonContentV2Row,
    _clause_meaning_preserved_in_result,
    _compress_exercise_catalogues_in_text,
    _derive_week_fields_v2,
    _quoted_actions_control,
    _rc_verbosity_block_reasons,
    derive_fields_v2,
    week_has_unresolved_mandatory_review,
)
from calendar_pedagoga.content_generation import CalendarContentRow, MatchStatus


def test_ofp_catalogue_compressed_in_result_keeps_dosage() -> None:
    source = (
        "Круговое ОФП: упражнение А, упражнение Б, упражнение В, "
        "упражнение Г (3 круга). Лазание по учебной трассе (2 раза)."
    )
    derived = derive_fields_v2(
        topic_title="Практика",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result
    control = derived.assessment_method
    low = result.casefold()
    assert "офп" in low
    assert "3 круга" in low or "(3 круга)" in low
    assert ":" not in result or not re.search(r"(?i)офп\s*:", result)
    assert "упражнение а" not in low
    assert "упражнение б" not in low
    assert "лазан" in low or "трасс" in low
    assert "проверяются действия" not in control.casefold()
    assert not _rc_verbosity_block_reasons(result, control)


def test_control_does_not_quote_full_result_sentences() -> None:
    result = (
        "Отрабатывает постановку ног. Выполняет круговое ОФП (2 круга). "
        "Проходит учебный траверс (2 раза)."
    )
    control = _quoted_actions_control(result)
    assert "проверяются действия" not in control.casefold()
    assert "проверяется действие" not in control.casefold()
    assert not re.search(r"«[^»]{72,}»", control)
    assert re.search(
        r"(?i)педагогическое наблюдение|практическ\w+ проверк|устный опрос|проверк\w+ выполнен",
        control,
    )
    assert "проверяются действия" not in control.casefold()


def test_homogeneous_actions_fold_without_losing_objects() -> None:
    source = (
        "Отработка постановки ног. Выполнение смены зацепов для рук. "
        "Прохождение траверса (2 раза)."
    )
    derived = derive_fields_v2(
        topic_title="Техника",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert "ног" in low
    assert "зацеп" in low or "рук" in low
    assert "траверс" in low
    assert not _rc_verbosity_block_reasons(
        derived.planned_result, derived.assessment_method
    )


def test_final_gate_blocks_control_that_duplicates_result() -> None:
    result = (
        "Выполняет круговое ОФП: планка, выпрыгивание, вис, отжимания (2 круга). "
        "Проходит учебную трассу."
    )
    control = (
        "Педагогическое наблюдение: проверяются действия "
        "«Выполняет круговое ОФП: планка, выпрыгивание, вис, отжимания (2 круга)», "
        "«Проходит учебную трассу»"
    )
    reasons = _rc_verbosity_block_reasons(result, control)
    assert reasons
    assert any(
        "дублир" in item.casefold()
        or "цитат" in item.casefold()
        or "офп" in item.casefold()
        for item in reasons
    )

    content = CalendarContentRow(
        week_number=1,
        date_range="01–07.01",
        month="Январь",
        section="Практика",
        topic_number="1",
        topic_title="Практика",
        source_topic_title="Практика",
        theory_hours=0,
        practice_hours=2,
        total_hours=2,
        match_status=MatchStatus.TEXT_MATCH,
        program_section="",
        program_topic="Практика",
        program_content_full=result,
        program_content_preview=result,
        source_program_name="p",
        source_utp_name="u",
        week_parts=(),
        warnings=(),
        knowledge_outcomes=(),
        skill_outcomes=(),
    )
    lesson = LessonContentV2Row(
        source=content,
        theory_text="",
        practice_text=result,
        lesson_type="практическое занятие",
        planned_result=result,
        assessment_method=control,
        action="",
        object="",
        conditions="",
        warnings=(),
        clause_coverage=(),
        clause_roles=(),
    )
    assert week_has_unresolved_mandatory_review(lesson)


def test_compress_helper_keeps_label_and_dosage() -> None:
    text = (
        "Выполняет круговое ОФП: планка, выпрыгивание, вис на турнике, "
        "коллективные приседания (2 круга)."
    )
    out = _compress_exercise_catalogues_in_text(text)
    low = out.casefold()
    assert "офп" in low
    assert "(2 круга)" in low or "2 круга" in low
    assert "планка" not in low
    assert "приседания" not in low


def _catalogue_meaning_preserved(source: str, result: str) -> bool:
    return _clause_meaning_preserved_in_result(
        source,
        result,
        topic_title="Практика",
        theory_hours=0,
        practice_hours=2,
    )


def test_homogeneous_catalogue_with_one_dosage_is_covered() -> None:
    source = "Круговое ОФП: планка, выпрыгивания, отжимания (2 круга)"
    result = "Выполняет круговое ОФП (2 круга)."

    assert _catalogue_meaning_preserved(source, result)


def test_homogeneous_catalogue_with_repeated_same_dosage_is_covered() -> None:
    source = (
        "Круговое ОФП: планка (2 круга), выпрыгивания (2 круга), "
        "отжимания (2 круга)"
    )
    result = "Выполняет круговое ОФП (2 круга)."

    assert _catalogue_meaning_preserved(source, result)


def test_catalogue_with_different_dosages_is_not_auto_covered() -> None:
    source = (
        "Круговое ОФП: планка (2 круга), выпрыгивания (10 раз), "
        "отжимания (2 круга)"
    )
    compact_result = "Выполняет круговое ОФП (2 круга)."

    assert not _catalogue_meaning_preserved(source, compact_result)
    assert _compress_exercise_catalogues_in_text(source) == source


def test_catalogue_without_dosage_is_not_auto_covered() -> None:
    source = "Круговое ОФП: планка, выпрыгивания, отжимания"
    compact_result = "Выполняет круговое ОФП."

    assert not _catalogue_meaning_preserved(source, compact_result)


def _early_catalogue_coverage(source: str) -> str:
    combined = source + ". Измерение пульса."
    result = _derive_week_fields_v2(
        topic_title="Практика",
        theory_text="",
        practice_text=combined,
        program_content=combined,
        theory_hours=0,
        practice_hours=2,
    )
    return dict(result.clause_coverage)[source]


def test_early_homogeneous_catalogue_with_one_dosage_is_covered() -> None:
    source = "Круговое ОФП: планка, выпрыгивания, отжимания (2круга)"

    assert _early_catalogue_coverage(source) == "COVERED"


def test_early_homogeneous_catalogue_with_repeated_dosage_is_covered() -> None:
    source = (
        "Круговое ОФП: планка (2 круга), выпрыгивания (2 круга), "
        "отжимания (2 круга)"
    )

    assert _early_catalogue_coverage(source) == "COVERED"


def test_early_catalogue_with_different_dosages_needs_review() -> None:
    source = (
        "Круговое ОФП: планка (2 круга), выпрыгивания (10 раз), "
        "отжимания (2 круга)"
    )

    assert _early_catalogue_coverage(source) == "NEEDS_REVIEW"


def test_early_catalogue_without_dosage_needs_review() -> None:
    source = "Круговое ОФП: планка, выпрыгивания, отжимания"

    assert _early_catalogue_coverage(source) == "NEEDS_REVIEW"


def test_early_ordinary_list_keeps_previous_coverage() -> None:
    source = "Отработка технических приёмов: диагональный шаг, накат, скрутка"

    assert _early_catalogue_coverage(source) == "COVERED"


def test_early_catalogue_does_not_mix_neighbor_action_dosage() -> None:
    source = (
        "Лазание трасс средней сложности на время (5 мин.) "
        "Круговое ОФП: планка, прыжки, подъём ног в висе (2 круга)"
    )

    assert _early_catalogue_coverage(source) == "COVERED"
