"""SOURCE allocation invariants; expectations are authored, not snapshots."""
import pytest

from calendar_pedagoga.content_generation import (
    _assign_section_blocks,
    _section_blocks_from_item,
)
from calendar_pedagoga.program_parsing import ProgramContentItem


def blocks(text):
    return _section_blocks_from_item(ProgramContentItem(None, "Раздел", text, "Раздел"))


@pytest.mark.parametrize("mode,field", [("Практика", "practice"), ("Теория", "theory")])
def test_complete_block_retains_sentences_and_conditions(mode, field):
    source = "Изучение правил. Выполнение упражнения без страховки запрещено."
    result = blocks(mode + "\n" + source)
    assert getattr(result[0], field) == source
    assigned = _assign_section_blocks(result, field, appearances=1)
    assert assigned[0].content.endswith(source)
    assert not assigned[0].warnings


def test_two_actions_are_not_reduced_to_first_sentence():
    source = "Выполнение разминки. Измерение пульса."
    assigned = _assign_section_blocks(blocks("Практика\n" + source), "practice", appearances=1)
    assert assigned[0].content == "Практика.\n" + source


def test_all_subtopics_assigned_in_order_without_invented_hours():
    names = ("Альфа", "Бета", "Гамма", "Дельта", "Эпсилон", "Дзета")
    source_blocks = tuple(
        block for name in names
        for block in blocks(f"Практика\nВыполнение упражнения «{name}».")
    )
    assigned = _assign_section_blocks(source_blocks, "practice", appearances=2)
    assert len(assigned) == 2
    text = "\n".join(item.content for item in assigned)
    assert [text.index(name) for name in names] == sorted(text.index(name) for name in names)
    assert all(text.count(name) == 1 for name in names)
    assert all(any("NEEDS_REVIEW" in warning for warning in item.warnings) for item in assigned)
    assert all(not hasattr(item, "hours") for item in assigned)


def test_game_does_not_replace_neighbor_operations():
    source_blocks = blocks(
        "Тема 1. Изделие\nПрактика\nИзготовление открытки.\n"
        "Тема 2. Игра\nПрактика\nРолевая игра.\n"
        "Тема 3. Измерение\nПрактика\nИзмерение длины."
    )
    assigned = _assign_section_blocks(source_blocks, "practice", appearances=1)
    assert assigned[0].content == (
        "Практика.\nИзготовление открытки.\nРолевая игра.\nИзмерение длины."
    )


def test_theory_practice_remain_separate():
    source_blocks = blocks(
        "Теория\nИзучение правил. Обсуждение запретов.\n"
        "Практика\nВыполнение разминки. Измерение пульса."
    )
    theory = _assign_section_blocks(source_blocks, "theory", appearances=1)[0].content
    practice = _assign_section_blocks(source_blocks, "practice", appearances=1)[0].content
    assert "Обсуждение запретов." in theory and "Измерение пульса." not in theory
    assert "Измерение пульса." in practice and "Изучение правил." not in practice


def test_continuation_preserves_whole_source_and_exposes_uncertainty():
    source = "Составление плана. Проверка плана."
    assigned = _assign_section_blocks(blocks("Практика\n" + source), "practice", appearances=3)
    assert len(assigned) == 3
    assert all(item.content.endswith(source) for item in assigned)
    assert all(item.warnings for item in assigned)
