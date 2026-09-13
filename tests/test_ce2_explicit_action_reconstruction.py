# -*- coding: utf-8 -*-
"""Universal EXPLICIT ACTION RECONSTRUCTION + R13 pre-transform abstain."""

import re

from calendar_pedagoga.content_engine_v2 import (
    derive_fields_v2,
    transform_clause_to_result,
)


def test_r13_prohibition_never_becomes_positive_performance() -> None:
    source = "Выполнение упражнения без страховки запрещено"
    phrase, _frame = transform_clause_to_result(
        source, theory_only=False, full_source=source
    )
    assert phrase == ""
    assert not re.search(r"(?i)\bвыполняет\s+упражнение\s+без\s+страховки\b", phrase)
    derived = derive_fields_v2(
        topic_title="Безопасность",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    assert derived.planned_result == ""
    assert not re.search(
        r"(?i)\bвыполняет\s+упражнение\s+без\s+страховки\b",
        derived.planned_result,
    )
    assert any("NEEDS_REVIEW" in warning for warning in derived.warnings)


def test_verbal_noun_to_finite_action() -> None:
    source = "Огибание препятствий на траверсе."
    derived = derive_fields_v2(
        topic_title="Техника",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert "огиба" in low or "выполняет огибание" in low
    assert "препятств" in low
    assert any(status == "COVERED" for _, status in derived.clause_coverage)


def test_drill_np_with_quantity_is_finite() -> None:
    source = "Коллективные приседания (30 раз)"
    derived = derive_fields_v2(
        topic_title="ОФП",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    assert derived.planned_result.casefold().startswith("выполняет")
    assert "приседан" in derived.planned_result.casefold()
    assert "30" in derived.planned_result


def test_coordinated_actions_all_reach_finite() -> None:
    source = "Фасовка, упаковка и переноска продуктов в рюкзаках."
    derived = derive_fields_v2(
        topic_title="Питание",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert "фасует" in low
    assert "упаковывает" in low
    assert "переносит" in low
    assert "переносок" not in low


def test_shared_head_keeps_both_objects() -> None:
    source = (
        "Тренировка надевания страховочной системы и использование "
        "специального снаряжения для лазания."
    )
    derived = derive_fields_v2(
        topic_title="Страховка",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert "отрабатывает" in low or "надева" in low
    assert "страховочн" in low
    assert "снаряжен" in low


def test_action_object_list_reconstructs_head() -> None:
    source = "Преодоление препятствий: залесенная местность, крутые склоны."
    derived = derive_fields_v2(
        topic_title="Препятствия",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert "преодолева" in low or "выполняет преодоление" in low
    assert "препятств" in low


def test_ofp_catalogue_stays_one_finite_action() -> None:
    source = (
        "Круговое ОФП: планка, выпрыгивание, вис на турнике, "
        "«складочки», отжимания (2 круга)"
    )
    derived = derive_fields_v2(
        topic_title="ОФП",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert low.startswith("выполняет")
    assert "офп" in low
    assert "планка" in low
    assert "выпрыгиван" in low


def test_topic_noun_without_explicit_action_not_invented() -> None:
    source = "Вводный инструктаж."
    phrase, _frame = transform_clause_to_result(
        source, theory_only=False, full_source=source
    )
    assert not phrase.casefold().startswith("выполняет вводный инструктаж")


def test_alternative_or_abstains() -> None:
    source = "Выполнение разминки или отработка страховки."
    phrase, _frame = transform_clause_to_result(
        source, theory_only=False, full_source=source
    )
    assert phrase == ""
