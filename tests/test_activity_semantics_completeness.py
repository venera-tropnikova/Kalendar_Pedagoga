import pytest

from calendar_pedagoga.content_engine_v2 import derive_fields_v2, _split_action_segments
from calendar_pedagoga.lesson_content import refine_selected_activity_type


def derive(source):
    return derive_fields_v2(topic_title="Практика", theory_text="", practice_text=source,
                            program_content="Практика. " + source, practice_hours=2)


@pytest.mark.parametrize("members", [
    "диагональный шаг, накат, скрутка",
    "скрутка, накат, диагональный шаг",
    "сгибание, разгибание, вращение",
    "штриховка, растушевка, заливка",
])
def test_catalogue_members_remain_in_result_and_control(members):
    source = "Изучение и отработка основных технических приемов: " + members
    result = derive(source)
    for member in members.split(", "):
        assert member in result.planned_result
        assert member in result.assessment_method
    assert _split_action_segments(source) == [source]


def test_independent_action_not_absorbed_as_catalogue():
    source = "Изучение карты, выполнение упражнений"
    assert len(_split_action_segments(source)) == 2


@pytest.mark.parametrize("modifiers,expected", [
    ("массовых туристско-краеведческих", "туристско-краеведческое мероприятие"),
    ("туристско-краеведческих массовых", "туристско-краеведческое мероприятие"),
    ("музыкальных", "музыкальное мероприятие"),
    ("экологических", "экологическое мероприятие"),
    ("массовых", "мероприятие"),
])
def test_event_subject_comes_from_selected_source(modifiers, expected):
    assert derive(f"Подготовка и участие в {modifiers} мероприятиях.").lesson_type == expected


def test_leisure_does_not_claim_training():
    assert refine_selected_activity_type("практикум", "Любимые зимние развлечения — катание на санках, на коньках", "Выполняет катание.") == "спортивно-игровое занятие"
    assert refine_selected_activity_type("практикум", "Обучение технике катания на коньках", "Отрабатывает технику.") == "учебно-тренировочное занятие"
    assert refine_selected_activity_type("практикум", "Катание на коньках", "Выполняет катание.") == "практикум"


def test_equipment_type_does_not_replace_general_training():
    source = "Тренировка надевания страховочной системы и использование специального снаряжения для лазания"
    assert refine_selected_activity_type("учебно-тренировочное занятие", source, "Отрабатывает надевание.") == "практикум по работе со страховочным снаряжением"
    for source in ("Тренировка техники лазания", "Упражнения на развитие силы", "Отработка страховки партнера"):
        assert refine_selected_activity_type("учебно-тренировочное занятие", source, "Отрабатывает технику.") == "учебно-тренировочное занятие"
