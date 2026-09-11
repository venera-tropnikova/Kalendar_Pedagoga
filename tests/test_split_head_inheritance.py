"""Shared source heads survive weekly splitting without borrowing content."""

import pytest

from calendar_pedagoga.practice_slots import (
    practice_units_from_text, assign_distributed_practice_slots,
)
from calendar_pedagoga.content_engine_v2 import derive_fields_v2, _conducted_event_result


@pytest.mark.parametrize("head", [
    "Выполнение", "Проведение", "Изготовление", "Изучение", "Отработка",
    "Составление", "Подготовка", "Освоение", "Исследование", "Сравнение",
])
def test_shared_head_is_copied_verbatim_only(head):
    source = f"{head} первых объектов; вторых объектов; третьих объектов."
    assert practice_units_from_text(source) == [
        f"{head} первых объектов", f"{head} вторых объектов", f"{head} третьих объектов",
    ]


@pytest.mark.parametrize("source, expected", [
    ("Изучение предметов. Отдых; подвижных игр", "подвижных игр"),
    ("Изучение предметов; Рисунки", "Рисунки"),
    ("Изучение предметов; изготовление макетов; новых моделей", "изготовление новых моделей"),
    ("Предметы; новых моделей", "новых моделей"),
    ("Изучение предметов; обсуждение результатов", "обсуждение результатов"),
])
def test_no_inheritance_across_sentence_or_independent_action(source, expected):
    assert practice_units_from_text(source)[-1] == expected


SOURCE = (
    'Проведение дидактических и ролевых игр: «Разговор», «Пожелание», '
    'игра-фантазия «Если бы…»; подвижных игр, праздников с участием родителей. Рисунки.'
)


def test_weekly_slots_and_results_preserve_all_assigned_activities():
    units = practice_units_from_text(SOURCE)
    slots, _ = assign_distributed_practice_slots(units, 2)
    assert slots[1] == ('Проведение подвижных игр, праздников с участием родителей', 'Рисунки')
    results = [derive_fields_v2(
        topic_title="Произвольная тема", theory_text="", practice_text=SOURCE,
        program_content=SOURCE, practice_hours=2, occurrence_index=i,
        practice_appearance_count=2,
    ) for i in range(2)]
    first, second = results
    for text in (first.planned_result, first.assessment_method):
        assert 'дидактических и ролевых играх' in text
        assert '«Разговор», «Пожелание», игра-фантазия «Если бы…»' in text
        assert 'родителей' not in text
        assert 'по теме' not in text
    for text in (second.planned_result, second.assessment_method):
        assert 'подвижных играх' in text
        assert 'праздниках с участием родителей' in text
        assert 'рисунк' in text
        assert 'Разговор' not in text


def test_unknown_event_member_is_not_silently_dropped():
    assert _conducted_event_result('Проведение подвижных игр, неизвестного действия') is None


def test_quoted_semicolon_does_not_inherit_a_head():
    from calendar_pedagoga.practice_slots import _inherit_split_heads
    text = 'Проведение игр: «Первый; новых друзей». Рисунки.'
    assert _inherit_split_heads(text) == text
