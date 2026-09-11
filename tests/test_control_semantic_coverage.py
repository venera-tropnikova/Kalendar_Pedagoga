import pytest
from calendar_pedagoga.content_engine_v2 import ActionFrame, control_from_frame, _control_result_obligations


def control(result):
    return control_from_frame(ActionFrame("", "", "", ""), planned_result=result,
                              lesson_type="практикум", theory_hours=0, practice_hours=2)


@pytest.mark.parametrize("result", [
    "Изготавливает открытку или маску по выбору.",
    "Изготавливает макет или модель по выбору.",
])
def test_choice_is_not_reduced_to_first_product(result):
    actual = control(result)
    assert result.rstrip(".") in actual
    assert "выбранного" in actual


@pytest.mark.parametrize("obj", ["кормушку", "плакат"])
def test_common_product_does_not_cover_placing_it(obj):
    actual = control(f"Изготавливает {obj} и развешивает {obj}.")
    assert f"развешивает {obj}" in actual
    assert "изготов" in actual


def test_shared_object_is_inherited_by_coordinated_verbs():
    assert _control_result_obligations("Изучает и отрабатывает приёмы.") == [
        ("Изучает", "приёмы"), ("отрабатывает", "приёмы"),
    ]
    assert "«Изучает и»" not in control("Изучает и отрабатывает приёмы.")


def test_three_operations_with_same_object():
    actual = control("Изготавливает плакат, развешивает плакат и складывает плакат.")
    assert "развешивает плакат" in actual
    assert "складывает плакат" in actual


def test_empty_result_does_not_invent_operations():
    assert "проверка действия" not in control("")
