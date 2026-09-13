# -*- coding: utf-8 -*-
"""Final Tourists batch: purchase chain, travel/quiz, azimuth glue, camp paren, control."""

from calendar_pedagoga.content_engine_v2 import (
    _fold_week_result,
    _inflect_object_phrase,
    derive_fields_v2,
    transform_clause_to_result,
)
from calendar_pedagoga.practice_slots import (
    assign_distributed_practice_slots,
    practice_units_from_text,
)


def test_positive_purchase_chain_reaches_result_and_control() -> None:
    practice = (
        "Составление меню и списка продуктов для похода. "
        "Закупка, фасовка и упаковка продуктов. "
        "Приготовление пищи на костре."
    )
    derived = derive_fields_v2(
        topic_title="Питание в туристском походе",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "закупает" in result and "фасует" in result and "упаковывает" in result
    assert "закупает" in control and "продукт" in control
    assert all(status == "COVERED" for _clause, status in derived.clause_coverage)


def test_negative_purchase_chain_not_required_for_unrelated_cooking() -> None:
    practice = "Приготовление пищи на костре."
    derived = derive_fields_v2(
        topic_title="Питание",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    assert "закупает" not in derived.planned_result.casefold()
    assert "фасов" not in derived.planned_result.casefold()


def test_positive_map_travel_and_local_quiz_reach_result_control() -> None:
    theory = "Климат, растительность и животный мир Башкортостана."
    practice = (
        "Знакомство с картой своего района. "
        "«Путешествия» по карте. "
        "Проведение краеведческих викторин."
    )
    derived = derive_fields_v2(
        topic_title="Родной край, его природные особенности, история, известные земляки",
        theory_text=theory,
        practice_text=practice,
        program_content=f"{theory}\n{practice}",
        theory_hours=1,
        practice_hours=1,
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "путешеств" in result and "по карте" in result
    assert "викторин" in result
    assert "путешеств" in control and "викторин" in control
    assert all(status == "COVERED" for _clause, status in derived.clause_coverage)


def test_negative_bare_travel_without_path_pp_stays_non_trip() -> None:
    phrase, _frame = transform_clause_to_result(
        "Туристские путешествия",
        theory_only=True,
        full_source="Туристские путешествия.",
    )
    assert not phrase.casefold().startswith("совершает")


def test_positive_azimuth_motion_stays_own_sentence_after_fold() -> None:
    result = (
        "Ориентирует карту по компасу. "
        "Выполняет упражнения на засечки: определение азимута на заданный предмет "
        "(обратная засечка) и нахождение ориентиров по заданному азимуту "
        "(прямая засечка). "
        "Выполняет движение по азимуту, проходит азимутальные отрезки, "
        "азимутальные построения (треугольники, «бабочки» и т.п.)."
    )
    folded = _fold_week_result(result).casefold()
    assert "выполняет движение по азимуту" in folded
    assert "проходит" in folded and "азимутальн" in folded
    assert "засечки:" in folded
    assert "засечки: определение" in folded
    # Colon-list of засечки must not absorb the later azimuth motion.
    assert "засечка), движение по азимуту" not in folded


def test_positive_azimuth_week_keeps_motion_in_result_and_control() -> None:
    practice = (
        "Ориентирование карты по компасу. "
        "Упражнения на засечки: определение азимута на заданный предмет "
        "(обратная засечка) и нахождение ориентиров по заданному азимуту "
        "(прямая засечка). "
        "Движение по азимуту, прохождение азимутальных отрезков, "
        "азимутальных построений (треугольники, «бабочки» и т.п.)."
    )
    derived = derive_fields_v2(
        topic_title="Компас. Работа с компасом",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "движен" in result and "азимут" in result
    assert "проходит" in result or "азимутальн" in result
    assert "движен" in control and "азимут" in control


def test_positive_camp_parenthetical_agrees_with_accusative_head() -> None:
    phrase, _frame = transform_clause_to_result(
        "Развертывание и свертывание лагеря (бивака)",
        theory_only=False,
        full_source="Развертывание и свертывание лагеря (бивака).",
    )
    assert "лагерь (бивак)" in phrase.casefold()
    assert "бивака" not in phrase.casefold()
    assert _inflect_object_phrase("лагеря (бивака)", case="acc") == "лагерь (бивак)"


def test_positive_direct_case_paren_exemplars_stay_nominative() -> None:
    """Paren examples like «треугольники» must not become «треугольнику»."""

    source = (
        "Движение по азимуту, прохождение азимутальных отрезков, "
        "азимутальных построений (треугольники, «бабочки» и т.п.)."
    )
    phrase, _frame = transform_clause_to_result(
        source, theory_only=False, full_source=source
    )
    assert "(треугольники, «бабочки» и т.п.)" in phrase
    assert "треугольнику" not in phrase.casefold()
    derived = derive_fields_v2(
        topic_title="Компас. Работа с компасом",
        theory_text="",
        practice_text=(
            "Ориентирование карты по компасу. "
            "Упражнения на засечки: определение азимута на заданный предмет "
            "(обратная засечка) и нахождение ориентиров по заданному азимуту "
            "(прямая засечка). "
            + source
        ),
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    assert "(треугольники, «бабочки» и т.п.)" in derived.planned_result
    assert "треугольнику" not in derived.planned_result.casefold()
    assert "треугольники" in derived.assessment_method.casefold()
    assert "треугольнику" not in derived.assessment_method.casefold()


def test_negative_already_accusative_parenthetical_is_not_rewritten() -> None:
    # Direct-case exemplars stay; only genitive appositions convert with the head.
    assert "бивак)" in _inflect_object_phrase("лагеря (бивака)", case="acc")
    assert _inflect_object_phrase("лагеря (бивака)", case="acc") != "лагеря (бивака)"
    kept = _inflect_object_phrase(
        "азимутальных построений (треугольники, «бабочки» и т.п.)", case="acc"
    )
    assert "(треугольники, «бабочки» и т.п.)" in kept
    assert "треугольнику" not in kept
    # Feminine gen.sg apposition still follows the head (пары → пару).
    assert "(пару шагов)" in _inflect_object_phrase("шага (пары шагов)", case="acc")



def test_positive_technique_control_covers_result_without_orphan_tail() -> None:
    practice = (
        "Отработка движения колонной. "
        "Соблюдение режима движения. "
        "Отработка техники движения по дорогам, тропам, по пересеченной местности "
        "(лес, заросли кустарников, завалы, заболоченная местность)."
    )
    derived = derive_fields_v2(
        topic_title="Правила движения в походе, преодоление препятствий",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=1,
    )
    control = derived.assessment_method
    result = derived.planned_result.casefold()
    assert "отрабатывает" in result and "соблюдает" in result
    assert "; за " not in control
    assert not control.rstrip().endswith("за техникой движения")
    assert "техник" in control.casefold()
    assert "режим" in control.casefold()


def test_identical_sfp_source_slots_may_share_result() -> None:
    """When SOURCE text is the same block, last slots may repeat the same unit."""

    practice = (
        "Упражнение на развитие выносливости. "
        "Упражнения на развитие быстроты. "
        "Упражнения на развитие силы. "
        "Упражнения на развитие гибкости, на растягивание и расслабление мышц."
    )
    units = practice_units_from_text(practice)
    slots, _flags = assign_distributed_practice_slots(units, 5)
    assert len(slots) == 5
    assert len(units) == 4
    # Extra calendar week continues the last grounded unit; do not invent a fifth.
    assert slots[3] == slots[4]
    last = derive_fields_v2(
        topic_title="Специальная физическая подготовка",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
        occurrence_index=4,
        practice_appearance_count=5,
    )
    prev = derive_fields_v2(
        topic_title="Специальная физическая подготовка",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
        occurrence_index=3,
        practice_appearance_count=5,
    )
    assert last.planned_result == prev.planned_result
    assert "гибк" in last.planned_result.casefold()
