import re

import pytest

from calendar_pedagoga.content_engine_v2 import (
    _continues_prepositional_group, _coordinated_inside_group,
    _drop_raw_list_tails, _fold_week_result,
    _result_grammar_issue, _derive_week_fields_v2, derive_fields_v2,
)


@pytest.mark.parametrize("text", [
    "Характеризует правилу безопасного поведения.",
    "Характеризует лучшие ученики школы.",
    "Характеризует деревью и кустарники.",
    "Характеризует роль башкирских путешественника.",
    "Характеризует значение волевых усилия.",
    "Строит на бумаге заданных азимутов.",
    "Изготавливает сувениры, масок, открытки.",
    "Оценивает заданных маршрутов.",
])
def test_unproven_case_is_rejected(text):
    assert _result_grammar_issue(text)


@pytest.mark.parametrize("text", [
    "Измеряет пульс.", "Выполняет разминку.",
    "Характеризует строение организма.",
    "Характеризует составляющие здорового образа жизни.",
    "Раскрывает значение водных процедур.",
    "Отрабатывает отдых на зацепах различной формы.",
])
def test_supported_case_not_rejected(text):
    assert not _result_grammar_issue(text)


def test_review_keeps_source_control_type_and_safe_independent_action():
    args = dict(topic_title="Учебная тема", theory_text="",
                practice_text="Построение на бумаге заданных азимутов. Измерение пульса.",
                practice_hours=2)
    before = _derive_week_fields_v2(**args)
    after = derive_fields_v2(**args)
    assert "Выполняет построение на бумаге заданных азимутов." in after.planned_result
    assert "Измеряет пульс" in after.planned_result
    assert after.assessment_method == before.assessment_method
    assert after.frame == before.frame
    assert dict(after.clause_coverage)["Построение на бумаге заданных азимутов"] == "COVERED"
    assert dict(after.clause_coverage)["Измерение пульса"] == "COVERED"
    assert not any("SOURCE: Построение" in w for w in after.warnings)


def test_abbreviation_does_not_leave_orphan_city_as_result():
    result = derive_fields_v2(topic_title="История", theory_text="История развития туризма в г. Салават.",
                              practice_text="", theory_hours=2)
    assert result.planned_result != "Салават."


@pytest.mark.parametrize("source, fragment", [
    ("Памятники прославленным людям.", "характеризует памятники прославленным людям"),
    ("День моей республики.", "характеризует день моей республики"),
    ("Одежда, зимний инвентарь.", "характеризует одежду, зимний инвентарь"),
])
def test_covered_knowledge_citation_survives_case_gate(source, fragment):
    result = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=2,
    )
    low = result.planned_result.casefold()
    assert fragment in low
    assert "по теме" not in low


@pytest.mark.parametrize("source, head", [
    ("Экскурсионные поездки: Шиханы, Капова пещера и другие.", "экскурсионные поездки"),
    ("Памятники: обелиски, мемориальные доски.", "памятники"),
])
def test_catalogue_heading_is_not_a_covered_citation(source, head):
    """Заголовок над перечнем не цитата клаузы: перечень нельзя терять молча."""
    result = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=2,
    )
    assert result.planned_result.strip() == ""
    assert f"характеризует {head}" not in result.planned_result.casefold()
    assert all(status == "NEEDS_REVIEW" for _clause, status in result.clause_coverage)
    assert any("NEEDS_REVIEW" in warning for warning in result.warnings)


def test_instrumental_government_is_not_an_admissible_knowledge_object():
    """Инструменталис — комплемент деятельности, а не поле знания."""
    source = "Перспективы занятий туристско-краеведческой деятельностью."
    result = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=2,
    )
    assert result.planned_result.strip() == ""
    assert "характеризует" not in result.planned_result.casefold()
    assert all(status == "NEEDS_REVIEW" for _clause, status in result.clause_coverage)


@pytest.mark.parametrize("text", [
    "Характеризует правилу безопасного поведения.",
    "Характеризует лучшие ученики школы.",
    "Характеризует деревью и кустарники.",
    "Характеризует городу Башкортостана.",
])
def test_unproven_knowledge_case_stays_rejected(text):
    assert _result_grammar_issue(text) == "unproven_knowledge_object_case"


def test_wrong_inflection_is_not_restored_from_near_source():
    result = derive_fields_v2(
        topic_title="Край",
        theory_text="Города Башкортостана.",
        practice_text="",
        program_content="Города Башкортостана.",
        theory_hours=2,
    )
    assert "городу" not in result.planned_result.casefold()


def test_topic_fallback_is_not_restored_by_citation_gate():
    result = derive_fields_v2(
        topic_title="Аптечка",
        theory_text="",
        practice_text="",
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    assert result.planned_result.strip() == ""


@pytest.mark.parametrize(
    "source, fragment",
    [
        ("Торможение.", "выполняет торможение"),
        ("Подъем «лесенкой».", "выполняет подъем «лесенкой»"),
        ("Спуск с холма.", "выполняет спуск с холма"),
        ("Преодоление препятствий на коньках.", "выполняет преодоление препятствий"),
    ],
)
def test_practice_process_actions_become_finite_result(source, fragment):
    result = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text="",
        practice_text=source,
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    low = result.planned_result.casefold().replace("ё", "е")
    assert fragment.replace("ё", "е") in low
    assert "по теме" not in low
    assert "тормозит" not in low
    assert "преодолевает" not in low
    control = result.assessment_method.casefold()
    assert control
    assert "по теме" not in control
    assert control.startswith("педагогическое наблюдение") or control.startswith(
        "проверка выполнения"
    )


@pytest.mark.parametrize(
    "source, kept",
    [
        ("Способы передвижения на коньках.", ""),
        ("Спуск с холма, способы поворота.", "выполняет спуск с холма"),
        ("Подъем «лесенкой», способы поворота.", "выполняет подъем «лесенкой»"),
    ],
)
def test_ways_catalogue_is_not_a_performed_object(source, kept):
    result = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text="",
        practice_text=source,
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    low = result.planned_result.casefold()
    control = result.assessment_method.casefold()
    assert "выполняет способ" not in low
    assert "выполнением способ" not in control
    if kept:
        assert kept in low
        assert "способ" not in low
        assert "способ" not in control
    else:
        assert "по теме" in low or not low.strip()


def test_practice_process_list_keeps_control_from_same_result():
    result = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text="",
        practice_text=(
            "Способы передвижения на лыжах. Подъем «лесенкой», «ёлочкой». "
            "Спуск с горы, способы поворота. Торможение."
        ),
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    low = result.planned_result.casefold()
    assert "выполняет" in low
    assert "торможение" in low
    assert "по теме" not in low
    control = result.assessment_method
    assert "по теме" not in control.casefold()
    assert control.startswith("Педагогическое наблюдение за выполнением ")
    assert "торможения" in control
    # One performance is observed once, so the finite verb is not repeated and
    # the named ways of the movement keep their own quotes.
    assert control.casefold().count("выполнен") == 1
    assert "выполняет" not in control.casefold()
    assert "»»" not in control


def test_theory_process_noun_is_not_wrapped_as_performance():
    result = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text="Торможение.",
        practice_text="",
        program_content="Торможение.",
        theory_hours=2,
        practice_hours=0,
    )
    low = result.planned_result.casefold()
    assert not low.startswith("выполняет")


def test_knowledge_or_ordinary_np_is_not_invented_practice_action():
    result = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text="",
        practice_text="Значение правил. Вязка.",
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    low = result.planned_result.casefold()
    assert "выполняет значение" not in low
    assert "вяжет" not in low
    assert not re.search(r"выполняет вязк", low)


def test_colon_catalogue_after_process_is_not_performed_activity():
    result = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text="",
        practice_text="Преодоление препятствий: крутые склоны.",
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    low = result.planned_result.casefold()
    assert "выполняет преодоление" not in low
    assert "преодолевает" not in low


@pytest.mark.parametrize("text, expected", [
    # A further member of an open prepositional group keeps the same case and
    # is coordinated with the member before it.
    (
        "оказывает первую доврачебную помощь при ожогах, обморожениях",
        "оказывает первую доврачебную помощь при ожогах и обморожениях",
    ),
    (
        "выполняет обязанности по должностям в период подготовки, "
        "проведения похода и подведения итогов",
        "выполняет обязанности по должностям в период подготовки, "
        "проведения похода и подведения итогов",
    ),
    # A nominative names a new activity, so it stays a separate list item.
    (
        "изучает на местности изображения местных предметов, "
        "знакомство с различными формами рельефа",
        "изучает на местности изображения местных предметов",
    ),
    # No group is open, so there is nothing the tail could continue.
    ("закупает продукты, фасовка и упаковка продуктов", "закупает продукты"),
    # An unproven form proves no agreement with the member before it.
    (
        "разрабатывает маршрут с описанием ориентиров, составлением графика",
        "разрабатывает маршрут с описанием ориентиров",
    ),
])
def test_only_proven_group_continuation_survives_a_comma(text, expected):
    assert _drop_raw_list_tails(text) == expected


@pytest.mark.parametrize("kept, tail", [
    # Opens a group of its own instead of continuing the one before it.
    ("выполняет обязанности в период подготовки", "при проведении похода"),
    # Carries its own finite verb, so it is a clause, not a member.
    ("оказывает помощь при ожогах", "обрабатывает обморожения"),
    # Nominative singular cannot be a member of an oblique group.
    ("изучает изображения на местности предметов", "знакомство"),
    # No preposition in the kept part, so no group is open.
    ("закупает продукты", "фасовка"),
])
def test_unproven_continuations_are_rejected(kept, tail):
    assert not _continues_prepositional_group(kept, tail)


def test_group_continuation_reaches_the_result():
    result = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text="",
        practice_text=(
            "Основные приёмы оказания первой доврачебной помощи при ожогах, "
            "обморожениях. Первая помощь утопающему."
        ),
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    low = result.planned_result.casefold()
    assert "при ожогах" in low
    assert "обморожениях" in low
    assert all(status == "COVERED" for _clause, status in result.clause_coverage)


def test_group_coordination_keeps_objects_in_their_own_sentences():
    week = _derive_week_fields_v2(
        topic_title="Учебная тема",
        theory_text="",
        practice_text=(
            "Основные приёмы оказания первой доврачебной помощи при ожогах, "
            "обморожениях. Первая помощь утопающему."
        ),
        practice_hours=2,
    )
    assert _fold_week_result(week.planned_result) == (
        "Оказывает первую доврачебную помощь при ожогах и обморожениях. "
        "Оказывает первую помощь утопающему."
    )


@pytest.mark.parametrize("result, expected", [
    # Coordination of the last object ends the enumeration, so it still folds.
    (
        "Характеризует значение карт. Характеризует устройство и назначение компаса.",
        "Характеризует значение карт, устройство и назначение компаса.",
    ),
    # An «и» before any preposition joins dependents of the head, not members
    # of a group, so the objects stay in one enumeration.
    (
        "Характеризует роль государства и органов образования в развитии туризма. "
        "Характеризует краеведение.",
        "Характеризует роль государства и органов образования в развитии туризма, "
        "краеведение.",
    ),
    # Parentheses delimit their own coordination.
    (
        "Характеризует строение организма (органы и системы). "
        "Характеризует строение внутренних органов.",
        "Характеризует строение организма (органы и системы), "
        "строение внутренних органов.",
    ),
    # A comma enumeration inside an object is not coordination by «и».
    (
        "Выполняет подъем «лесенкой», «ёлочкой». Выполняет спуск с горы. "
        "Выполняет торможение.",
        "Выполняет подъем «лесенкой», «ёлочкой», спуск с горы, торможение.",
    ),
])
def test_objects_without_group_coordination_still_fold(result, expected):
    assert _fold_week_result(result) == expected


@pytest.mark.parametrize("obj, coordinated", [
    ("первую доврачебную помощь при ожогах и обморожениях", True),
    ("значение волевых усилий в походах и тренировках", True),
    ("роль государства и органов образования в развитии туризма", False),
    ("строение организма (органы и системы)", False),
    ("духовные и физические возможности среды в развитии личности", False),
    ("устройство и назначение компаса", False),
])
def test_group_coordination_is_recognised_only_inside_a_group(obj, coordinated):
    assert _coordinated_inside_group(obj) is coordinated


def test_dropped_list_member_is_reported_instead_of_covered():
    result = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text="",
        practice_text=(
            "Изучение на местности изображения местных предметов, "
            "знакомство с различными формами рельефа. Топографические диктанты."
        ),
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    low = result.planned_result.casefold()
    assert "знакомств" not in low
    assert "знакомится" not in low
    coverage = dict(result.clause_coverage)
    clause = (
        "Изучение на местности изображения местных предметов, "
        "знакомство с различными формами рельефа"
    )
    assert coverage[clause] == "NEEDS_REVIEW"
    assert any(clause in warning for warning in result.warnings)


def test_named_techniques_without_process_head_stay_unconverted():
    result = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text="",
        practice_text="Крутые склоны, залесенная местность.",
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    low = result.planned_result.casefold()
    assert "тормозит" not in low
    assert "выполняет крутые" not in low
    assert "по теме" in low or not low.strip()
