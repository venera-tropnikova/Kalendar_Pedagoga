# -*- coding: utf-8 -*-
"""A standalone middle action between neighbors must stay in RESULT."""

from calendar_pedagoga.content_engine_v2 import (
    _conjugate_verbal_noun,
    derive_fields_v2,
    transform_clause_to_result,
)


def test_observance_verbal_noun_conjugates() -> None:
    assert _conjugate_verbal_noun("соблюдение") == "соблюдает"
    phrase, _frame = transform_clause_to_result(
        "Соблюдение режима движения",
        theory_only=False,
        full_source="Соблюдение режима движения",
    )
    low = phrase.casefold()
    assert low.startswith("соблюдает")
    assert "режим" in low
    assert "движен" in low


def test_middle_independent_action_between_neighbors_is_kept() -> None:
    """Positive: observance between two drill actions is not dropped."""

    practice = (
        "Отработка движения колонной. "
        "Соблюдение режима движения. "
        "Отработка техники движения по дорогам, тропам, по пересеченной местности."
    )
    derived = derive_fields_v2(
        topic_title="Туристский строй",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "отрабатывает движение колонной" in result
    assert "соблюдает режим движения" in result
    assert "техникой движения" in result or "технику движения" in result
    assert "соблюд" in control or "режим" in control
    assert all(status == "COVERED" for _clause, status in derived.clause_coverage)
    assert not any("Соблюдение режима" in warning for warning in derived.warnings)


def test_negative_kinds_catalogue_is_not_an_observance_result() -> None:
    """Negative: a kinds label is not conjugated as pupil observance."""

    phrase, _frame = transform_clause_to_result(
        "Виды соблюдения режима",
        theory_only=True,
        full_source="Виды соблюдения режима",
    )
    low = phrase.casefold()
    assert not low.startswith("соблюдает")
    assert "виды" in low or low.startswith("характеризует")


def test_negative_rules_observance_stays_knowledge_not_drill() -> None:
    """Negative: «соблюдение правил…» is not a movement-regime drill."""

    source = (
        "Соблюдение правил личной гигиены, утренний и вечерний туалет, "
        "уход за ногами, обувью, одеждой."
    )
    derived = derive_fields_v2(
        topic_title="Личная гигиена",
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=1,
        practice_hours=1,
    )
    result = derived.planned_result.casefold()
    assert "характеризует" in result
    assert not result.startswith("соблюдает")
