import pytest
from calendar_pedagoga.content_engine_v2 import derive_fields_v2, _safe_operation_result, _derive_week_fields_v2


@pytest.mark.parametrize("source,expected", [
    ("Изготовление сувениров, масок, открыток к Новому году", "Выполняет изготовление сувениров, масок, открыток к Новому году."),
    ("Построение на бумаге заданных азимутов", "Выполняет построение на бумаге заданных азимутов."),
])
def test_approved_operation_keeps_all_source(source, expected):
    args = dict(topic_title="Учебная тема", theory_text="", practice_text=source, practice_hours=2)
    before = _derive_week_fields_v2(**args)
    result = derive_fields_v2(**args)
    assert result.planned_result == expected
    assert result.clause_coverage == ((source, "COVERED"),)
    assert not any("грамматическая безопасность" in w for w in result.warnings)
    assert result.assessment_method == before.assessment_method
    assert result.frame == before.frame


@pytest.mark.parametrize("source", [
    "Изготовление изделия запрещено", "Не выполнять построение маршрута",
    "Построение маршрута педагогом", "Изготовление модели учителем",
    "Изготовление модели: ребёнок наблюдает", "Изготовление модели может выполняться педагогом",
    "Изготовление", "Маска, открытка", "Воспитание волевых качеств",
    "Совершенствование функций организма", "Изготовление и развешивание кормушек",
    "Построение маршрута и измерение расстояния",
])
def test_unsupported_or_non_pupil_action_is_not_repaired(source):
    assert _safe_operation_result(source, practical=True) == ""


def test_theoretical_description_not_promoted_to_practical_action():
    assert not _safe_operation_result("Изготовление моделей", practical=False)
    result = derive_fields_v2(topic_title="Учебная тема", theory_text="Построение на бумаге заданных азимутов", practice_text="", theory_hours=2)
    assert "Выполняет построение" not in result.planned_result


def test_preserves_order_and_other_review():
    args = dict(topic_title="Учебная тема", theory_text="", practice_text="Построение на бумаге заданных азимутов. Измерение пульса. Альфа, бета, гамма.", practice_hours=2)
    before = _derive_week_fields_v2(**args)
    result = derive_fields_v2(**args)
    assert result.planned_result.startswith("Выполняет построение на бумаге заданных азимутов.")
    # This task does not repair the separate pre-existing pulse conversion.
    assert result.planned_result.split(". ", 1)[1] == before.planned_result.split(". ", 1)[1]
    assert dict(result.clause_coverage)["Альфа, бета, гамма"] == "NEEDS_REVIEW"
