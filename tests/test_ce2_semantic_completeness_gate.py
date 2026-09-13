# -*- coding: utf-8 -*-
"""Universal semantic completeness gate — cross-domain, no program hardcode."""

from dataclasses import replace

from calendar_pedagoga.content_engine_v2 import (
    ContentEngineV2Result,
    ActionFrame,
    _apply_semantic_completeness_gate,
    _bare_list_without_action,
    _control_covers_all_result_items,
    derive_fields_v2,
)


def test_two_independent_knowledge_clauses_both_covered() -> None:
    theory = (
        "Строение клетки. "
        "Функции клеточной мембраны."
    )
    derived = derive_fields_v2(
        topic_title="Биология клетки",
        theory_text=theory,
        practice_text="",
        program_content=theory,
        theory_hours=2,
        practice_hours=0,
    )
    statuses = dict(derived.clause_coverage)
    assert statuses.get("Строение клетки") == "COVERED"
    assert statuses.get("Функции клеточной мембраны") == "COVERED"
    result = derived.planned_result.casefold()
    assert "клетк" in result
    assert "мембран" in result
    assert _control_covers_all_result_items(
        derived.planned_result, derived.assessment_method
    )


def test_theory_and_practice_both_covered() -> None:
    theory = "Строение измерительного инструмента."
    practice = "Выполнение разметки заготовки."
    derived = derive_fields_v2(
        topic_title="Технология обработки",
        theory_text=theory,
        practice_text=practice,
        program_content=f"{theory}\n{practice}",
        theory_hours=1,
        practice_hours=1,
    )
    statuses = dict(derived.clause_coverage)
    assert statuses.get("Строение измерительного инструмента") == "COVERED"
    assert statuses.get("Выполнение разметки заготовки") == "COVERED"
    low = derived.planned_result.casefold()
    assert "измерительн" in low or "инструмент" in low or "строен" in low
    assert "разметк" in low or "заготовк" in low
    assert _control_covers_all_result_items(
        derived.planned_result, derived.assessment_method
    )


def test_action_object_condition_all_preserved() -> None:
    practice = "Измерение длины отрезка на чертеже при помощи линейки."
    derived = derive_fields_v2(
        topic_title="Черчение",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result.casefold()
    assert "измеря" in result or "измерен" in result
    assert "длин" in result or "отрезок" in result or "отрезк" in result
    assert "линейк" in result or "чертеж" in result
    assert dict(derived.clause_coverage).get(practice.rstrip(".")) == "COVERED" or any(
        status == "COVERED" for _, status in derived.clause_coverage
    )
    assert _control_covers_all_result_items(
        derived.planned_result, derived.assessment_method
    )


def test_untranslatable_clause_needs_review_others_covered() -> None:
    practice = "Выполнение разминки. Квантовая запутанность нейтрино без наблюдения."
    derived = derive_fields_v2(
        topic_title="Общая подготовка",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    statuses = dict(derived.clause_coverage)
    assert statuses.get("Выполнение разминки") == "COVERED"
    hard = next(
        clause
        for clause in statuses
        if "запутанность" in clause.casefold() or "нейтрино" in clause.casefold()
    )
    assert statuses[hard] == "NEEDS_REVIEW"
    assert "разминк" in derived.planned_result.casefold()


def test_grammar_gate_drop_demotes_source_coverage() -> None:
    """If an accepted RESULT sentence is removed, COVERED must not remain."""

    candidate = ContentEngineV2Result(
        frame=ActionFrame("Строение клетки", "характеризует", "строение клетки", ""),
        lesson_type="теоретическое занятие",
        planned_result="Характеризует строение клетки. Характеризует функции мембраны.",
        assessment_method=(
            "устный опрос по строению клетки; устный опрос по функциям мембраны"
        ),
        theory_text="Строение клетки. Функции мембраны.",
        practice_text="",
        warnings=(),
        clause_coverage=(
            ("Строение клетки", "COVERED"),
            ("Функции мембраны", "COVERED"),
        ),
    )
    # Simulate grammar gate removing the second RESULT sentence.
    stripped = replace(
        candidate,
        planned_result="Характеризует строение клетки.",
        assessment_method="устный опрос по строению клетки",
    )
    gated = _apply_semantic_completeness_gate(
        stripped,
        topic_title="Биология клетки",
        theory_text="Строение клетки. Функции мембраны.",
        practice_text="",
        program_content="Строение клетки. Функции мембраны.",
        theory_hours=2,
        practice_hours=0,
    )
    statuses = dict(gated.clause_coverage)
    assert statuses["Строение клетки"] == "COVERED"
    assert statuses["Функции мембраны"] == "NEEDS_REVIEW"


def test_bare_list_has_no_invented_action() -> None:
    assert _bare_list_without_action("Альфа, бета, гамма.")
    derived = derive_fields_v2(
        topic_title="Перечень материалов",
        theory_text="",
        practice_text="Альфа, бета, гамма.",
        program_content="Альфа, бета, гамма.",
        theory_hours=0,
        practice_hours=2,
    )
    assert not derived.planned_result.strip() or "изобретает" not in derived.planned_result.casefold()
    assert all(
        status in {"NEEDS_REVIEW", "OPTIONAL"}
        for _, status in derived.clause_coverage
    )


def test_control_must_cover_every_result_item() -> None:
    derived = derive_fields_v2(
        topic_title="Лабораторная работа",
        theory_text="",
        practice_text="Выполнение разминки. Измерение пульса.",
        program_content="Выполнение разминки. Измерение пульса.",
        theory_hours=0,
        practice_hours=2,
    )
    assert derived.planned_result.strip()
    assert _control_covers_all_result_items(
        derived.planned_result, derived.assessment_method
    )
    # Incomplete CONTROL is a failing completeness condition.
    assert not _control_covers_all_result_items(
        derived.planned_result, "проверка дневника"
    )
