# -*- coding: utf-8 -*-
"""Tourists W24/W27: medicinal plants + health/capacity without week hardcode."""

from calendar_pedagoga.content_engine_v2 import (
    _bare_list_without_action,
    derive_fields_v2,
)


def test_positive_title_action_tail_reaches_result_and_control() -> None:
    """Coordinated title action not present in body still reaches RESULT/CONTROL."""

    topic = "Походная медицинская аптечка, использование лекарственных растений"
    theory = (
        "Составление медицинской аптечки. Хранение и транспортировка аптечки. "
        "Назначение и дозировка препаратов."
    )
    practice = "Формирование походной медицинской аптечки."
    derived = derive_fields_v2(
        topic_title=topic,
        theory_text=theory,
        practice_text=practice,
        program_content=f"{theory}\n{practice}",
        theory_hours=1,
        practice_hours=1,
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "формирует" in result and "аптечк" in result
    assert "использует" in result and "лекарственн" in result and "растен" in result
    assert "аптечк" in control
    assert "лекарственн" in control or "растен" in control


def test_negative_bare_title_noun_tail_is_not_invented() -> None:
    """A non-action title tail must not invent a pupil RESULT."""

    derived = derive_fields_v2(
        topic_title="Походная медицинская аптечка, компас",
        theory_text="Составление медицинской аптечки.",
        practice_text="Формирование походной медицинской аптечки.",
        program_content="Формирование походной медицинской аптечки.",
        theory_hours=1,
        practice_hours=1,
    )
    assert "компас" not in derived.planned_result.casefold()
    assert "использует" not in derived.planned_result.casefold()


def test_positive_exercise_influence_keeps_health_and_capacity() -> None:
    """Influence PP with coordinated targets is not a bare list and reaches RESULT."""

    clause = (
        "Влияние различных физических упражнений на укрепление здоровья, "
        "работоспособность"
    )
    assert not _bare_list_without_action(clause)
    theory = (
        "Краткие сведения о строении человеческого организма (органы и системы). "
        "Костно-связочный аппарат. Мышцы, их строение и взаимодействие. "
        "Основные сведения о строении внутренних органов. "
        f"{clause}. "
        "Совершенствование функций органов дыхания и кровообращения "
        "под воздействием занятий спортом."
    )
    derived = derive_fields_v2(
        topic_title=(
            "Краткие сведения о строении и функциях организма человека "
            "и влиянии физических упражнений"
        ),
        theory_text=theory,
        practice_text="",
        program_content=theory,
        theory_hours=2,
        practice_hours=0,
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "укреплен" in result and "здоров" in result
    assert "работоспособ" in result
    assert "укреплен" in control and "здоров" in control
    assert "работоспособ" in control
    # Existing sport-effect RESULT must stay; do not replace it with influence alone.
    assert "совершенствован" in result or "кровообращен" in result
    coverage = dict(derived.clause_coverage)
    assert coverage.get(clause) == "COVERED"


def test_negative_uncoupled_title_verbal_without_finite_map_is_skipped() -> None:
    """Title tails without a proven verbal→finite map must not invent RESULT."""

    derived = derive_fields_v2(
        topic_title="Правила движения в походе, преодоление препятствий",
        theory_text="Порядок движения группы на маршруте.",
        practice_text=(
            "Отработка движения колонной. Соблюдение режима движения. "
            "Отработка техники движения по дорогам, тропам, по пересеченной местности."
        ),
        program_content="Отработка движения колонной. Соблюдение режима движения.",
        theory_hours=1,
        practice_hours=1,
    )
    assert "преодолен" not in derived.planned_result.casefold()

    assert _bare_list_without_action("Альфа, бета, гамма.")
    derived = derive_fields_v2(
        topic_title="Перечень",
        theory_text="",
        practice_text="Альфа, бета, гамма.",
        program_content="Альфа, бета, гамма.",
        theory_hours=0,
        practice_hours=2,
    )
    assert not derived.planned_result.strip() or all(
        status == "NEEDS_REVIEW" for _clause, status in derived.clause_coverage
    )
