"""Regression: object agreement without word-level hardcode."""

from calendar_pedagoga.content_engine_v2 import (
    _adj_to_acc,
    _inflect_object_phrase,
    _phrase_to_dative,
    derive_fields_v2,
)


def test_velar_genitive_adjective_takes_ij_not_y() -> None:
    assert _adj_to_acc("краткого", plural=False, gender="m") == "краткий"
    assert _adj_to_acc("русского", plural=False, gender="m") == "русский"
    assert _adj_to_acc("нового", plural=False, gender="m") == "новый"
    assert _adj_to_acc("рабочего", plural=False, gender="n") == "рабочее"


def test_neuter_genitive_np_becomes_neuter_accusative() -> None:
    assert _inflect_object_phrase("рабочего места", case="acc") == "рабочее место"
    assert _inflect_object_phrase("учебных моделей: макет-мельница", case="acc") == (
        "учебные модели: макет-мельница"
    )


def test_listed_neuter_plural_knowledge_head_stays_accusative_equal() -> None:
    derived = derive_fields_v2(
        topic_title="Техника безопасности",
        theory_text="Правила работы в лаборатории.",
        practice_text="",
        program_content="Правила работы в лаборатории.",
        theory_hours=2,
        practice_hours=0,
    )
    low_result = derived.planned_result.casefold()
    low_control = derived.assessment_method.casefold()
    assert "правила работы" in low_result
    assert "правилу" not in low_result
    assert low_control.startswith("устный опрос по правилам работы")
    assert "правиле работы" not in low_control
    assert _phrase_to_dative("правила работы") == "правилам работы"


def test_verbal_noun_object_agrees_in_gender_and_spelling() -> None:
    workplace = derive_fields_v2(
        topic_title="Подготовка кабинета",
        theory_text="",
        practice_text="Организация рабочего места.",
        program_content="Организация рабочего места.",
        theory_hours=0,
        practice_hours=2,
    )
    assert "рабочее место" in workplace.planned_result.casefold()
    assert "рабочий места" not in workplace.planned_result.casefold()
    assert "рабочий места" not in workplace.assessment_method.casefold()
    assert "организацией рабочего места" in workplace.assessment_method.casefold()

    report = derive_fields_v2(
        topic_title="Итоги работы",
        theory_text="",
        practice_text="Подготовка краткого отчёта.",
        program_content="Подготовка краткого отчёта.",
        theory_hours=0,
        practice_hours=2,
    )
    assert "краткий отчёт" in report.planned_result.casefold()
    assert "краткый" not in report.planned_result.casefold()
    assert "краткый" not in report.assessment_method.casefold()
