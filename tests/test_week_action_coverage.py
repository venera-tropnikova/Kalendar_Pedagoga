import pytest

from calendar_pedagoga.content_engine_v2 import derive_fields_v2, _derive_selected_fields_v2


def derive(source, **kwargs):
    return derive_fields_v2(
        topic_title="Учебная тема", theory_text="", practice_text=source,
        practice_hours=2, **kwargs,
    )


def test_independent_operations_and_controls_are_retained():
    result = derive("Выполнение разминки. Измерение пульса.")
    for text in (result.planned_result, result.assessment_method):
        assert "разминк" in text.casefold()
        assert "пульс" in text.casefold()
    assert result.planned_result.index("разминк") < result.planned_result.index("пульс")


def test_failed_clause_does_not_erase_proven_action():
    result = derive("Измерение длины. Отработка действий участника.")
    assert "Измеряет длину" in result.planned_result
    assert "по теме" not in result.planned_result
    assert any("NEEDS_REVIEW" in w and "действий участника" in w for w in result.warnings)


def test_prohibition_does_not_become_action_in_mixed_source():
    result = derive("Измерение длины. Выполнение упражнения без страховки запрещено.")
    assert "Измеряет длину" in result.planned_result
    assert "Выполняет" not in result.planned_result
    assert any("запрещено" in w for w in result.warnings)


def test_unknown_list_does_not_invent_action():
    result = derive("Измерение длины. Альфа, бета, гамма.")
    assert "Измеряет длину" in result.planned_result
    assert "альфа" not in result.planned_result.casefold()
    assert any("Альфа" in w for w in result.warnings)


def test_other_actor_not_assigned_to_pupil():
    result = derive("Педагог демонстрирует упражнение. Измерение длины.")
    assert "демонстрирует" not in result.planned_result.casefold()
    assert "Измеряет длину" in result.planned_result
    assert any("Педагог" in w for w in result.warnings)


def test_only_current_slot_is_used():
    source = "Измерение длины. Измерение ширины. Изготовление открытки. Изготовление маски."
    first = derive(source, occurrence_index=0, practice_appearance_count=2)
    second = derive(source, occurrence_index=1, practice_appearance_count=2)
    assert "открытк" not in first.planned_result
    assert "маск" not in first.planned_result
    assert "длин" not in second.planned_result
    assert "ширин" not in second.planned_result


def test_path_complement_keeps_unconjugated_process():
    result = derive("Выполнение разминки. Движение по маршруту.")
    for text in (result.planned_result, result.assessment_method):
        assert "разминк" in text.casefold()
        assert "движен" in text.casefold()
        assert "маршрут" in text.casefold()
    assert dict(result.clause_coverage)["Движение по маршруту"] == "COVERED"


def test_document_noun_with_pp_is_not_performed():
    result = derive("Выполнение разминки. Введение в туризм.")
    assert "разминк" in result.planned_result.casefold()
    assert "выполняет введение" not in result.planned_result.casefold()
    assert any("Введение" in warning for warning in result.warnings)


@pytest.mark.parametrize("source", [
    "Выполнение разминки. Измерение пульса.",
    "Изготовление открытки. Ролевая игра.",
    "Ориентирование карты по компасу. Определение азимута на ориентир. Движение по азимуту.",
])
def test_type_and_source_do_not_change(source):
    args = dict(topic_title="Учебная тема", theory_text="", practice_text=source, practice_hours=2)
    before = _derive_selected_fields_v2(**args)
    after = derive_fields_v2(**args)
    assert after.lesson_type == before.lesson_type
    assert after.practice_text == before.practice_text
    assert after.theory_text == before.theory_text
