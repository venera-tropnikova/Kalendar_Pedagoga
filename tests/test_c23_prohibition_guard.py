"""C23 safety boundaries; existing semantic acceptance stays unchanged."""

import re

import pytest

from calendar_pedagoga.content_engine_v2 import derive_fields_v2, _prohibition_only_source


@pytest.mark.parametrize("source", [
    "Выполнение упражнения без страховки запрещено.",
    "Не выполнять упражнение без страховки.",
    "Запрещено использовать инструмент без разрешения.",
    "Запрещается открывать крышку при работе.",
    "Нельзя трогать провода.",
    "Не допускается проводить опыт без защиты.",
    "Использование инструмента без разрешения запрещено.",
    "Запрещено использование инструмента без разрешения.",
    "Не открывайте крышку при работе.",
    "Практика: Не выполнять упражнение без страховки.",
    "Не трогать провода. Не открывать крышку при работе.",
])
def test_prohibition_abstains_without_inventing_opposite(source):
    result = derive_fields_v2(
        topic_title="Безопасность", theory_text="", practice_text=source,
        program_content=source, practice_hours=2,
    )
    assert result.planned_result == ""
    assert result.assessment_method == ""
    assert result.practice_text == source
    assert any("NEEDS_REVIEW" in warning for warning in result.warnings)
    assert "без" not in result.planned_result  # no contrary positive activity either


@pytest.mark.parametrize("source", [
    "Выполнение упражнения со страховкой.",
    "Выполнение упражнения без ошибок.",
    "Выполнение несложного упражнения.",
    "Измерение пульса не менее двух раз.",
    "Не только выполнение упражнений, но и измерение пульса.",
    "Выполнение упражнения не запрещено.",
    "Обсуждение того, почему выполнение упражнения запрещено.",
    "Изучение запрещенных приемов.",
    "Не кровать, а стол.",
    "Не выполнять упражнение, а измерить пульс.",
    "Не выполнять упражнение, измеряет пульс.",
])
def test_guard_does_not_spread_to_positive_or_non_prohibitive_source(source):
    assert not _prohibition_only_source(source)
    result = derive_fields_v2(
        topic_title="Практика", theory_text="", practice_text=source,
        program_content=source, practice_hours=2,
    )
    assert not any("NEEDS_REVIEW" in warning for warning in result.warnings)


@pytest.mark.parametrize("source", [
    "Не выполнять упражнение без страховки. Измерение пульса.",
    "Измерение пульса. Не выполнять упражнение без страховки.",
])
def test_mixed_prohibition_keeps_positive_action(source):
    result = derive_fields_v2(
        topic_title="Практика", theory_text="", practice_text=source,
        program_content=source, practice_hours=2,
    )
    low = result.planned_result.casefold()
    assert "пульс" in low
    assert not re.search(r"\bвыполняет упражнение без страховки\b", low)
    if "не выполнять" not in low and "запрещ" not in low:
        assert any("NEEDS_REVIEW" in warning for warning in result.warnings)


def test_prohibition_is_not_borrowed_from_other_source():
    result = derive_fields_v2(
        topic_title="Практика", theory_text="",
        practice_text="Измерение пульса.",
        program_content="Не выполнять упражнение без страховки.", practice_hours=2,
    )
    assert result.planned_result
    assert not any("NEEDS_REVIEW" in warning for warning in result.warnings)
