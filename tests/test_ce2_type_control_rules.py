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
    previously_changed = {2, 3, 4, 6, 9, 11, 12, 13, 14, 16, 17, 23, 29, 30, 31, 36}
    r2_changed = {1}  # W01: mixed-week RESULT/CONTROL stay separate by topic
    changed = previously_changed | r2_changed
    w01_number, w01_type, w01_result, w01_control = CE2_TP1_WEEK_SNAPSHOT[0]
    assert w01_number == "1.1"
    assert w01_type == "теоретическое занятие"
    assert w01_result == (
        "Характеризует историю развития туризма в г. Салават. "
        "Раскрывает роль туризма в подготовке к защите Родины, "
        "в выборе профессии и подготовке к предстоящей трудовой деятельности."
    )
    assert w01_control == (
        "устный опрос по истории развития туризма в г. Салават; "
        "устный опрос по роли туризма в подготовке к защите Родины, "
        "в выборе профессии и подготовке к предстоящей трудовой деятельности"
    )
    assert _digest([row[2] for row in CE2_TP1_WEEK_SNAPSHOT]) == (
        "cec2a3089eee17d85e1a547d9eef6823ba3b8e25654b42dd513c8914fe8040c8"
    )
    # Live W01 RESULT/CONTROL are excluded from the untouched digest.
    # The historical oracle restores the pre-mixed-week W01 RESULT and the
    # pre-R2 W01 CONTROL so remaining weeks stay on the same freeze.
    pre_mixed_w01_result = (
        "Характеризует историю развития туризма в г. Салават и роль туризма "
        "в подготовке к защите Родины, в выборе профессии и подготовке к "
        "предстоящей трудовой деятельности."
    )
    pre_r2_w01_control = (
        "устный опрос по истории развития туризма в г. Салават и роли "
        "туризма в подготовке к защите Родины"
    )
    locked = []
    for week, row in enumerate(CE2_TP1_WEEK_SNAPSHOT, 1):
        if week in previously_changed:
            continue
        if week in r2_changed:
            locked.append([w01_type, pre_mixed_w01_result, pre_r2_w01_control])
            continue
        locked.append(list(row[1:]))
    assert _digest(locked) == "46338c7d732ed525b695d03de2bc3512e3de6282ec8264d66b12cf175e63c510"
    remaining_weeks = [
        week
        for week, _row in enumerate(CE2_TP1_WEEK_SNAPSHOT, 1)
        if week not in changed
    ]
    assert 1 not in remaining_weeks


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


def test_key_specialises_type_where_source_is_enough_and_keeps_generic_otherwise() -> None:
    from pathlib import Path

    from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2
    from calendar_pedagoga.content_generation import build_content_model
    from calendar_pedagoga.parsing import parse_utp
    from calendar_pedagoga.program_parsing import infer_study_year_number, parse_program
    from calendar_pedagoga.resolve_utp import apply_workload_from_document
    from calendar_pedagoga.scheduling import build_schedule

    root = Path(__file__).resolve().parents[1] / "references"
    utp = apply_workload_from_document(parse_utp(root / "УТП КЛЮЧ 2 г. 2ч.docx"))
    program = parse_program(
        (root / "Программа КЛЮЧ.DOC").read_bytes(),
        "Программа КЛЮЧ.DOC",
        study_year=infer_study_year_number(utp.metadata.study_year) or 2,
    )
    generated = build_lesson_content_v2(
        build_content_model(build_schedule(utp, "2026–2027"), utp, program, "УТП КЛЮЧ 2 г. 2ч.docx")
    )
    assert len(generated) == 36
    by_week = {lesson.source.week_number: lesson for lesson in generated}
    assert by_week[2].lesson_type == "экскурсия"
    assert by_week[4].lesson_type == "экскурсия"
    assert by_week[9].lesson_type == "дидактическое занятие"
    assert by_week[12].lesson_type == "учебно-тренировочное занятие"
    assert by_week[16].lesson_type == "дидактическое занятие"
    assert by_week[22].lesson_type == "учебно-тренировочное занятие"
    assert "бассейн" in by_week[22].planned_result.casefold()
    assert by_week[14].lesson_type == "практическое занятие"
    assert by_week[19].lesson_type == "практическое занятие"
    for lesson in generated:
        control = lesson.assessment_method.casefold()
        if not control:
            assert not lesson.planned_result.strip()
            assert any("NEEDS_REVIEW" in warning for warning in lesson.warnings)
            continue
        if control == "педагогическое наблюдение":
            raise AssertionError(f"W{lesson.source.week_number}: bare control")
        if lesson.lesson_type == "экскурсия":
            assert "наблюден" in control
            assert lesson.planned_result.casefold().startswith(("совершает", "посещает"))


