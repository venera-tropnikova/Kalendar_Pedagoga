"""Accepted TYPE/CONTROL audit: frozen good rows and local evidence only."""

import hashlib
import json

import pytest

from calendar_pedagoga.content_engine_v2 import ActionFrame, control_from_frame, type_from_frame
from test_ce2_grounded_triad import CE2_TP1_WEEK_SNAPSHOT


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    ).encode()).hexdigest()


def test_audit_snapshot_preserves_results_and_untouched_rows():
    # RESULT digest captured before this task. TYPE/CONTROL freeze excludes
    # the agreed change weeks so neighbouring rows cannot drift silently.
    changed = {3, 6, 9, 11, 12, 13, 14, 16, 17}
    assert _digest([row[2] for row in CE2_TP1_WEEK_SNAPSHOT]) == "710d813ff5ddad138ec1a2808176ed73de886f52039074909223996a462debce"
    assert _digest([list(row[1:]) for week, row in enumerate(
        CE2_TP1_WEEK_SNAPSHOT, 1
    ) if week not in changed]) == "de2eb3c655a678b5bfd1ac94bfc089037dc432b70c108f8190c85acbf8919619"


@pytest.mark.parametrize(("result", "clause", "expected"), [
    ("Выступает в туристских соревнованиях в качестве участника.",
     "Выступление в туристских соревнованиях в качестве участников.",
     "туристские соревнования"),
    ("Выступает в музыкальном конкурсе.", "Выступление в музыкальном конкурсе.", "конкурс"),
    ("Выполняет обязанности дежурного.", "Выполнение обязанностей дежурного.", "практикум"),
    ("Распознаёт знаки.", "Упражнения на запоминание знаков.", "практикум по работе со знаками"),
    ("Распознаёт знаки.", "Топографические диктанты, упражнения на запоминание знаков.",
     "практикум по работе с топографическими знаками"),
    ("Ведёт дневник наблюдений.", "Ведение дневника наблюдений.", "практикум"),
    ("Ведёт дневник самоконтроля.", "Ведение дневника самоконтроля.", "практикум по самоконтролю"),
    ("Определяет стороны горизонта по Солнцу.", "Определение сторон горизонта по Солнцу.",
     "практикум по ориентированию"),
])
def test_specialisation_uses_only_selected_activity(result, clause, expected):
    # Tempting but UNSELECTED neighbouring material must not supply a form.
    irrelevant = " Соревнование. Топографический диктант. Имитация ситуации и действия. Защита."
    assert type_from_frame(
        ActionFrame(clause, "", "", ""), planned_result=result,
        theory_hours=0, practice_hours=1, theory_text=irrelevant,
        practice_text=clause + irrelevant, program_content=clause + irrelevant,
    ) == expected


@pytest.mark.parametrize(("result", "expected"), [
    ("Составляет меню.", "проверка меню"),
    ("Составляет меню и список продуктов.", "проверка меню и списка продуктов"),
    ("Составляет меню, готовит пищу на кухне.",
     "проверка меню; педагогическое наблюдение за приготовлением пищи на кухне"),
    ("Ориентирует карту по компасу.",
     "практическое задание по ориентированию карты по компасу"),
    ("Отбирает ориентиры на карте по заданному маршруту.",
     "практическое задание по отбору ориентиров на карте по заданному маршруту"),
    ("Проводит наблюдения за ростом растений.",
     "педагогическое наблюдение за проведением наблюдений за ростом растений"),
    ("Выполняет обязанности по должностям в период подготовки.",
     "педагогическое наблюдение за выполнением обязанностей по должностям в период подготовки"),
    ("Составляет отчёт о наблюдениях.", "проверка отчёта о наблюдениях"),
    ("Выступает в туристских соревнованиях в качестве участника.",
     "выступление в туристских соревнованиях"),
])
def test_control_checks_result_without_inventing_products_or_conditions(result, expected):
    actual = control_from_frame(
        ActionFrame(result, "", "", ""), planned_result=result,
        lesson_type="практикум", theory_hours=0, practice_hours=1,
    )
    assert actual == expected
    assert not any(word in actual for word in (
        "чек-лист", "норматив", "протокол", "эксперт", "самооцен", "зачёт", "балл", "защита",
    ))


def test_explicit_selected_control_has_priority_over_product():
    assert control_from_frame(
        ActionFrame("Топографический диктант.", "", "", ""),
        planned_result="Составляет план.", lesson_type="топографический практикум",
        theory_hours=0, practice_hours=1,
    ) == "топографический диктант"


def test_dictation_stays_control_and_does_not_become_type():
    clause = "Топографические диктанты, упражнения на запоминание знаков."
    result = "Распознаёт знаки."
    lesson_type = type_from_frame(
        ActionFrame(clause, "", "", ""), planned_result=result,
        theory_hours=0, practice_hours=1, theory_text="",
        practice_text=clause, program_content=clause,
    )
    control = control_from_frame(
        ActionFrame(clause, "", "", ""), planned_result=result,
        lesson_type=lesson_type, theory_hours=0, practice_hours=1,
    )
    assert lesson_type == "практикум по работе с топографическими знаками"
    assert control == "топографический диктант"
    assert "диктант" not in lesson_type
    assert lesson_type != control

