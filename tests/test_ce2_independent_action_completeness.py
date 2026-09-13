# -*- coding: utf-8 -*-
"""Independent coordinated SOURCE actions must all reach RESULT/CONTROL."""

from calendar_pedagoga.content_engine_v2 import (
    _conjugate_verbal_noun,
    derive_fields_v2,
    transform_clause_to_result,
)


def test_collection_and_passage_verbal_nouns_conjugate() -> None:
    assert _conjugate_verbal_noun("сбор") == "собирает"
    assert _conjugate_verbal_noun("прохождение") == "проходит"


def test_positive_relief_acquaintance_and_forms_are_kept() -> None:
    practice = (
        "Изучение на местности изображения местных предметов, "
        "знакомство с различными формами рельефа. "
        "Топографические диктанты, упражнения на запоминание знаков, "
        "игры, мини соревнования."
    )
    derived = derive_fields_v2(
        topic_title="Условные знаки",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "знакомится" in result and "рельеф" in result
    assert "диктант" in result
    assert "играх" in result
    assert "соревновани" in result
    assert "знакомится" in control or "рельеф" in control
    assert "играх" in control or "соревновани" in control
    assert all(status == "COVERED" for _clause, status in derived.clause_coverage)


def test_positive_azimuth_passage_and_legend_motion_are_kept() -> None:
    practice = (
        "Движение по азимуту, прохождение азимутальных отрезков. "
        "Занятия по практическому прохождению мини маршрута, движение по легенде. "
        "Разработка маршрута с подробным описанием ориентиров, составлением графика."
    )
    derived = derive_fields_v2(
        topic_title="Ориентирование",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result.casefold()
    assert "движен" in result and "азимут" in result
    assert "проходит" in result and "отрезк" in result
    assert "легенд" in result
    assert "составлени" in result and "график" in result
    assert all(status == "COVERED" for _clause, status in derived.clause_coverage)


def test_positive_measurement_assessment_and_collection_are_kept() -> None:
    practice = (
        "Измерение кривых линий курвиметром или ниткой. "
        "Оценка пройденных расстояний по затраченному времени. "
        "Сбор материалов для школьного музея. "
        "Способы обеззараживания питьевой воды."
    )
    derived = derive_fields_v2(
        topic_title="Практика",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "кривые линии" in result
    assert "пройденные расстояния" in result
    assert "собирает" in result
    assert "характеризует" in result and "обеззараж" in result
    assert "собирает" in control or "материал" in control
    assert all(status == "COVERED" for _clause, status in derived.clause_coverage)


def test_negative_bare_kinds_catalogue_is_not_a_pupil_drill() -> None:
    phrase, _frame = transform_clause_to_result(
        "Виды способов обеззараживания воды",
        theory_only=True,
        full_source="Виды способов обеззараживания воды",
    )
    low = phrase.casefold()
    assert not low.startswith("собирает")
    assert not low.startswith("проходит")
    assert "виды" in low or low.startswith("характеризует")


def test_negative_locomotion_ways_label_stays_unconverted() -> None:
    phrase, _frame = transform_clause_to_result(
        "Способы передвижения на лыжах",
        theory_only=False,
        full_source="Способы передвижения на лыжах",
    )
    low = (phrase or "").casefold()
    assert not low.startswith("характеризует")
    assert low.startswith("способы")
