"""Independent boundaries for explicit observer roles and ungoverned lists."""
import pytest

from calendar_pedagoga.content_engine_v2 import (
    derive_fields_v2, _pupil_observes_demonstration,
)


def derive(source):
    return derive_fields_v2(topic_title="Тема", theory_text="", practice_text=source,
                            program_content=source, practice_hours=2)


@pytest.mark.parametrize("source", [
    "Карта, компас, линейка.",
    "Перечень: бумага, кисть, краски.",
    "А/Б/В",
])
def test_unassigned_list_keeps_source_and_requests_review(source):
    result = derive(source)
    assert result.practice_text == source
    assert not result.planned_result and not result.assessment_method
    assert any("NEEDS_REVIEW" in w for w in result.warnings)


@pytest.mark.parametrize("source", [
    "Висы на зацепках, планках, турнике.",
    "Изготовление открытки, маски, сувенира.",
    "Отработка диагонального шага, наката, скрутки.",
    "Катание на санках, коньках.",
    "Экскурсии по улицам Строителей, Первомайской, Ленина.",
])
def test_objects_of_named_action_are_not_bare_list(source):
    result = derive(source)
    assert result.planned_result
    assert not any("NEEDS_REVIEW" in w for w in result.warnings)


@pytest.mark.parametrize("teacher,child,verb", [
    ("Педагог", "ребёнок", "наблюдает"),
    ("Учитель", "ученики", "наблюдают"),
    ("Инструктор", "дети", "наблюдают"),
])
def test_observer_retains_role_and_demonstrated_object(teacher, child, verb):
    source = f"{teacher} показывает упражнение; {child} {verb}."
    result = derive(source)
    assert result.planned_result.startswith("Наблюдает за показом")
    for text in (result.planned_result, result.assessment_method):
        assert "упражнение" in text
        assert "выполняет" not in text.casefold()
    assert result.practice_text == source


@pytest.mark.parametrize("source", [
    "Ребёнок показывает упражнение; педагог наблюдает.",
    "Педагог не показывает упражнение; ребёнок наблюдает.",
    "Педагог показывает упражнение; ребёнок не наблюдает.",
    "Педагог показывает упражнение; ребёнок наблюдает за птицами.",
    "Педагог показывает упражнение; ребёнок наблюдает. Измерение пульса.",
])
def test_do_not_guess_observer_relation_or_drop_other_action(source):
    assert _pupil_observes_demonstration(source) is None


@pytest.mark.parametrize("source", [
    "Мероприятие «Мама, папа, я – дружная семья».",
    "Компас, линейка, их устройство.",
])
def test_non_list_punctuation_and_anaphora_keep_existing_path(source):
    from calendar_pedagoga.content_engine_v2 import _bare_list_without_action
    assert not _bare_list_without_action(source)
