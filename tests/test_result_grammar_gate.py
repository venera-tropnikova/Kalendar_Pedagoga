import pytest

from calendar_pedagoga.content_engine_v2 import (
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
    (
        "Экскурсионные поездки: Шиханы, Капова пещера и другие.",
        "характеризует экскурсионные поездки",
    ),
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


def test_unproven_practice_list_stays_empty():
    result = derive_fields_v2(
        topic_title="Лыжный туризм",
        theory_text="",
        practice_text=(
            "Способы передвижения на лыжах. Подъем «лесенкой», «ёлочкой». "
            "Спуск с горы, способы поворота. Торможение."
        ),
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    assert result.planned_result.strip() == ""
