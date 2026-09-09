"""T1/T2: specialized TYPE needs action evidence on theory-only parts."""

import pytest

from calendar_pedagoga.content_engine_v2 import (
    ActionFrame,
    derive_fields_v2,
    type_from_frame,
)
from calendar_pedagoga.lesson_content import finalize_lesson_type
from test_content_engine_v2 import APPROVED_TP_CONTROL_TYPE, _fill_tp_topic


def _type(source: str, *, theory: int, practice: int, result: str = "") -> str:
    frame = ActionFrame(source, "", "", "")
    planned = result or (
        "Выполняет практическое задание по теме «Раздел»."
        if practice
        else "Характеризует материал по теме «Тема»."
    )
    return type_from_frame(
        frame,
        planned_result=planned,
        theory_hours=theory,
        practice_hours=practice,
        theory_text=source if theory else "",
        practice_text=source if practice else "",
        program_content=source,
    )


def _derived_type(source: str, *, theory: int, practice: int) -> str:
    return derive_fields_v2(
        topic_title="Тема",
        theory_text=source if theory and not practice else "",
        practice_text=source if practice else "",
        program_content=source,
        theory_hours=theory,
        practice_hours=practice,
    ).lesson_type


def test_nominal_excursion_heading_is_theory_when_theory_only():
    source = "Экскурсионные поездки: Стерлитамакские шиханы, Торатау."
    assert _type(source, theory=2, practice=0) == "теоретическое занятие"
    assert _derived_type(source, theory=2, practice=0) == "теоретическое занятие"


def test_stories_about_excursions_stay_theory():
    source = "Рассказы об экскурсиях по родному краю."
    assert _type(source, theory=1, practice=0) == "теоретическое занятие"
    assert _derived_type(source, theory=1, practice=0) == "теоретическое занятие"
    assert _type(source, theory=0, practice=2) != "экскурсия"
    assert _derived_type(source, theory=0, practice=2) != "экскурсия"


def test_action_excursion_clause_is_excursion_on_practice():
    source = "Экскурсия по улицам города."
    assert _type(source, theory=0, practice=2) == "экскурсия"
    assert _derived_type(source, theory=0, practice=2) == "экскурсия"


def test_participation_in_competition_is_event_on_practice():
    source = "Участие в соревновании."
    assert _derived_type(source, theory=0, practice=2) == "соревнование"
    assert _type(source, theory=0, practice=2) == "соревнование"


def test_participation_in_holiday_or_mass_event_is_not_generic_practice():
    assert _derived_type("Участие в празднике курая.", theory=0, practice=2) == "праздник"
    assert (
        _derived_type("Участие в массовом мероприятии.", theory=0, practice=2)
        == "мероприятие"
    )
    assert (
        _derived_type(
            "Подготовка и участие в туристско-краеведческих массовых мероприятиях.",
            theory=0,
            practice=2,
        )
        == "мероприятие"
    )
    assert _type(
        "Подготовка и участие в празднике.",
        theory=0,
        practice=2,
        result="Участвует в празднике.",
    ) == "праздник"
    assert (
        finalize_lesson_type("мероприятие", theory_hours=0, practice_hours=2)
        == "мероприятие"
    )
    assert finalize_lesson_type("праздник", theory_hours=0, practice_hours=2) == "праздник"


def test_technique_drill_is_training_on_practice():
    source = "Отработка техники подъёма, спуска и торможения."
    assert _derived_type(source, theory=0, practice=2) == "учебно-тренировочное занятие"
    assert _type(source, theory=0, practice=2) == "учебно-тренировочное занятие"


def test_competition_heading_list_is_theory_when_theory_only():
    source = "Соревнования: дистанции, этапы, судейские должности."
    assert _type(source, theory=2, practice=0) == "теоретическое занятие"
    assert _derived_type(source, theory=2, practice=0) == "теоретическое занятие"


def test_explicit_action_clause_may_specialize_theory_only():
    assert _type("Экскурсия по улицам города.", theory=2, practice=0) == "экскурсия"
    assert _derived_type("Посещение музея края.", theory=1, practice=0) == "экскурсия"
    assert _derived_type("Участие в соревновании.", theory=1, practice=0) == "соревнование"


def test_tourists_approved_types_do_not_regress():
    for number, (_control, lesson_type) in APPROVED_TP_CONTROL_TYPE.items():
        derived = _fill_tp_topic(number)
        assert derived.lesson_type == lesson_type, (
            f"{number}: {derived.lesson_type!r} != {lesson_type!r}"
        )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Дидактические игры: «Загадки-задачки Рассеянного».", "дидактическое занятие"),
        ("Экскурсионные поездки: Стерлитамакские шиханы, Торатау.", "экскурсия"),
        (
            "Укладывает рюкзаки, подгоняет снаряжение, ухаживает за ним и ремонтирует его.",
            "практикум по работе со снаряжением",
        ),
        ("Ориентирует карту по компасу.", "практикум по ориентированию"),
        (
            "Первая доврачебная помощь при ушибах, потёртостях, ссадинах и ранах.",
            "практикум по оказанию первой помощи",
        ),
    ],
)
def test_key_like_special_forms_stay_on_practice(source, expected):
    if source.startswith("Укладывает") or source.startswith("Ориентирует"):
        lesson_type = type_from_frame(
            ActionFrame(source, "", "", ""),
            planned_result=source if source.endswith(".") else source + ".",
            theory_hours=0,
            practice_hours=1,
            theory_text="",
            practice_text=source,
            program_content=source,
        )
        assert lesson_type == expected
        return
    assert _derived_type(source, theory=0, practice=2) == expected


def test_named_training_competition_is_competition_not_generic_practice():
    source = "Учебное соревнование «Слалом» и «Эстафета»."
    assert _derived_type(source, theory=0, practice=2) == "соревнование"
    assert _type(source, theory=0, practice=2) == "соревнование"


def test_program_writing_practice_is_not_generic_workshop():
    source = "Составление программ движения по линии и остановки у стены."
    assert _derived_type(source, theory=0, practice=2) == (
        "практикум программирования"
    )
    algorithm = "Разработка алгоритма ветвления по датчику."
    assert _derived_type(algorithm, theory=0, practice=2) == (
        "практикум по разработке алгоритма"
    )


def test_underspecified_practice_stays_generic_workshop():
    source = "Практическая работа с чертежом детали."
    assert _derived_type(source, theory=0, practice=2) in {
        "практикум",
        "практическое занятие",
    }
