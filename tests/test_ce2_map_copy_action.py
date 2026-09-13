# -*- coding: utf-8 -*-
"""Map-copying practice must stay a finite RESULT with an accusative patient."""

from calendar_pedagoga.content_engine_v2 import (
    _conjugate_verbal_noun,
    derive_fields_v2,
    transform_clause_to_result,
)


def test_copying_verbal_noun_conjugates_with_acc_patient() -> None:
    assert _conjugate_verbal_noun("копирование") == "копирует"
    phrase, _frame = transform_clause_to_result(
        "Копирование на кальку участка топографической карты",
        theory_only=False,
        full_source="Копирование на кальку участка топографической карты",
    )
    low = phrase.casefold()
    assert low.startswith("копирует")
    assert "на кальку" in low
    assert "участок" in low
    assert "участка" not in low
    assert "топографическ" in low and "карт" in low


def test_map_copy_kept_with_scale_exercises() -> None:
    """Positive: copying stays beside scale/distance exercises."""

    practice = (
        "Работа с картами различного масштаба. "
        "Упражнения по определению масштаба, измерению расстояния на карте. "
        "Копирование на кальку участка топографической карты."
    )
    derived = derive_fields_v2(
        topic_title="Топографическая карта",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "копирует" in result
    assert "кальку" in result
    assert "участок" in result
    assert "копир" in control or "кальк" in control or "участок" in control
    assert any(
        "копирование" in clause.casefold() and status == "COVERED"
        for clause, status in derived.clause_coverage
    )


def test_negative_kinds_copying_is_not_finite_drill() -> None:
    """Negative: a kinds label about copying is not a pupil drill."""

    phrase, _frame = transform_clause_to_result(
        "Виды копирования карт",
        theory_only=True,
        full_source="Виды копирования карт",
    )
    low = phrase.casefold()
    assert not low.startswith("копирует")
    assert "виды" in low or low.startswith("характеризует")
