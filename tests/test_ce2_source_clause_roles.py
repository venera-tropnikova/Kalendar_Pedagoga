# -*- coding: utf-8 -*-
"""Cross-domain SOURCE-clause role classifier for the FINAL gate."""

from calendar_pedagoga.content_engine_v2 import (
    CATALOG,
    CONTEXT,
    EXAMPLE,
    METADATA,
    REQUIRED_ACTION,
    REQUIRED_CONDITION,
    REQUIRED_KNOWLEDGE,
    REQUIRED_OBJECT,
    _OPTIONAL_COVERAGE_STATUS,
    classify_source_clause,
    classify_source_clauses,
    derive_fields_v2,
    unresolved_mandatory_review_blocks,
    week_has_unresolved_mandatory_review,
)
from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.pipeline import PipelineError, _build_pipeline_lesson_content
import pytest


def test_standalone_action_is_required_action() -> None:
    role = classify_source_clause(
        "Выполнение разминки.",
        practice_hours=2,
    )
    assert role == REQUIRED_ACTION


def test_action_object_condition_all_required() -> None:
    clauses = (
        "Выполнение измерения длины.",
        "отрезка на чертеже",
        "при помощи линейки",
    )
    roles = dict(classify_source_clauses(clauses, practice_hours=2))
    assert roles[clauses[0]] == REQUIRED_ACTION
    assert roles[clauses[1]] == REQUIRED_OBJECT
    assert roles[clauses[2]] == REQUIRED_CONDITION
    derived = derive_fields_v2(
        topic_title="Черчение",
        theory_text="",
        practice_text="\n".join(clauses),
        program_content="\n".join(clauses),
        theory_hours=0,
        practice_hours=2,
    )
    statuses = dict(derived.clause_coverage)
    for clause in clauses:
        key = clause.rstrip(".")
        assert statuses.get(clause) in {"COVERED", "NEEDS_REVIEW"} or statuses.get(
            key
        ) in {"COVERED", "NEEDS_REVIEW"}


def test_sibling_does_not_demote_independent_pupil_action() -> None:
    clauses = (
        "Тренировка постановки ног при помощи игр на снаряде.",
        "Коллективные приседания (30 раз)",
    )
    roles = dict(classify_source_clauses(clauses, practice_hours=2))
    assert roles[clauses[0]] == REQUIRED_ACTION
    assert roles[clauses[1]] == REQUIRED_ACTION


def test_parenthetical_example_is_example_not_separate_result() -> None:
    clauses = (
        "Тренировка постановки ног при помощи игр на снаряде.",
        "«Альфа, бета, гамма»",
    )
    roles = dict(classify_source_clauses(clauses, practice_hours=2))
    assert roles[clauses[0]] == REQUIRED_ACTION
    assert roles[clauses[1]] == EXAMPLE
    derived = derive_fields_v2(
        topic_title="Подготовка",
        theory_text="",
        practice_text="\n".join(clauses),
        program_content="\n".join(clauses),
        theory_hours=0,
        practice_hours=2,
    )
    statuses = dict(derived.clause_coverage)
    assert statuses.get(clauses[1]) == _OPTIONAL_COVERAGE_STATUS
    # Example must not force an invented separate RESULT obligation.
    assert "изобретает" not in derived.planned_result.casefold()


def test_reference_catalog_is_catalog() -> None:
    clause = "Альфа, бета, гамма."
    assert classify_source_clause(clause, practice_hours=2) == CATALOG
    derived = derive_fields_v2(
        topic_title="Перечень материалов",
        theory_text="",
        practice_text=clause,
        program_content=clause,
        theory_hours=0,
        practice_hours=2,
    )
    assert dict(derived.clause_coverage).get(clause.rstrip(".")) == _OPTIONAL_COVERAGE_STATUS
    row = build_lesson_content_v2(
        (
            CalendarContentRow(
                week_number=1,
                date_range="01–07.09",
                month="Сентябрь",
                section="Раздел",
                topic_number="1",
                topic_title="Перечень",
                source_topic_title="Перечень",
                theory_hours=0,
                practice_hours=2,
                total_hours=2,
                match_status=MatchStatus.EXACT,
                program_section="Раздел",
                program_topic="Перечень",
                program_content_full="Практика.\n" + clause,
                program_content_preview=clause,
                source_program_name="Программа",
                source_utp_name="utp.docx",
            ),
        )
    )[0]
    assert not week_has_unresolved_mandatory_review(row)


def test_gloss_of_covered_knowledge_is_context() -> None:
    clauses = (
        "Государственные символы: герб, флаг, гимн.",
        "их смысл",
    )
    roles = dict(classify_source_clauses(clauses, theory_hours=2))
    assert roles[clauses[1]] == CONTEXT
    derived = derive_fields_v2(
        topic_title="Символы",
        theory_text="\n".join(clauses),
        practice_text="",
        program_content="\n".join(clauses),
        theory_hours=2,
        practice_hours=0,
    )
    assert dict(derived.clause_coverage).get(clauses[1]) == _OPTIONAL_COVERAGE_STATUS


def test_second_independent_knowledge_is_required() -> None:
    clauses = (
        "Строение клетки.",
        "Функции клеточной мембраны.",
    )
    roles = dict(classify_source_clauses(clauses, theory_hours=2))
    assert roles[clauses[0]] == REQUIRED_KNOWLEDGE
    assert roles[clauses[1]] == REQUIRED_KNOWLEDGE
    derived = derive_fields_v2(
        topic_title="Биология клетки",
        theory_text="\n".join(clauses),
        practice_text="",
        program_content="\n".join(clauses),
        theory_hours=2,
        practice_hours=0,
    )
    statuses = dict(derived.clause_coverage)
    assert statuses.get("Строение клетки") == "COVERED"
    assert statuses.get("Функции клеточной мембраны") == "COVERED"


def test_unknown_required_clause_blocks() -> None:
    practice = "Квантовая запутанность нейтрино без наблюдения."
    content = (
        CalendarContentRow(
            week_number=3,
            date_range="01–07.09",
            month="Сентябрь",
            section="Раздел",
            topic_number="1",
            topic_title="Неизвестное",
            source_topic_title="Неизвестное",
            theory_hours=0,
            practice_hours=2,
            total_hours=2,
            match_status=MatchStatus.EXACT,
            program_section="Раздел",
            program_topic="Неизвестное",
            program_content_full="Практика.\n" + practice,
            program_content_preview=practice,
            source_program_name="Программа",
            source_utp_name="utp.docx",
        ),
    )
    with pytest.raises(PipelineError, match="не готов"):
        _build_pipeline_lesson_content(content, use_content_engine_v2=True)


def test_unknown_optional_context_does_not_block() -> None:
    theory = "Строение измерительного инструмента.\nих назначение"
    content = (
        CalendarContentRow(
            week_number=4,
            date_range="01–07.09",
            month="Сентябрь",
            section="Раздел",
            topic_number="1",
            topic_title="Инструмент",
            source_topic_title="Инструмент",
            theory_hours=2,
            practice_hours=0,
            total_hours=2,
            match_status=MatchStatus.EXACT,
            program_section="Раздел",
            program_topic="Инструмент",
            program_content_full=theory,
            program_content_preview=theory,
            source_program_name="Программа",
            source_utp_name="utp.docx",
        ),
    )
    rows = build_lesson_content_v2(content)
    roles = dict(rows[0].clause_roles)
    assert any(role == CONTEXT for role in roles.values())
    assert unresolved_mandatory_review_blocks(rows) == ()
    # Ready path must accept the week when only CONTEXT is unresolved-as-optional.
    lesson_rows = _build_pipeline_lesson_content(content, use_content_engine_v2=True)
    assert lesson_rows[0].planned_result.strip()
