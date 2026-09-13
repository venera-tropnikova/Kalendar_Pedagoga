# -*- coding: utf-8 -*-
"""Using equipment named in SOURCE must stay a finite RESULT action."""

from calendar_pedagoga.content_engine_v2 import (
    _conjugate_verbal_noun,
    derive_fields_v2,
    transform_clause_to_result,
)


def test_equipment_use_verbal_noun_conjugates() -> None:
    assert _conjugate_verbal_noun("использование") == "использует"
    phrase, _frame = transform_clause_to_result(
        "Использование альпенштока на склонах",
        theory_only=False,
        full_source="Использование альпенштока на склонах",
    )
    low = phrase.casefold()
    assert low.startswith("использует")
    assert "альпеншток" in low
    assert "склон" in low


def test_alpenstock_use_between_obstacle_actions_is_kept() -> None:
    """Positive: equipment use after neighbouring drills is not dropped."""

    practice = (
        "Отработка техники преодоления естественных препятствий: склонов, подъёмов. "
        "Организация переправы по бревну с самостраховкой. "
        "Использование альпенштока на склонах."
    )
    derived = derive_fields_v2(
        topic_title="Преодоление препятствий",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "отрабатывает" in result and "препятств" in result
    assert "организует переправу" in result or "переправ" in result
    assert "использует" in result and "альпеншток" in result
    assert "альпеншток" in control or "использует" in control
    assert all(status == "COVERED" for _clause, status in derived.clause_coverage)
    assert not any("альпеншток" in warning.casefold() for warning in derived.warnings)


def test_negative_prohibition_use_is_not_pupil_result() -> None:
    """Negative: a prohibition about use is not conjugated as pupil action."""

    phrase, _frame = transform_clause_to_result(
        "Запрещено использование инструмента без разрешения",
        theory_only=False,
        full_source="Запрещено использование инструмента без разрешения",
    )
    low = (phrase or "").casefold()
    assert not low.startswith("использует")
