"""Oral CONTROL must inflect the whole NP, not only the first/last stem."""

from calendar_pedagoga.content_engine_v2 import (
    _noun_to_dative,
    _phrase_to_dative,
    derive_fields_v2,
)
from test_ce2_grounded_triad import _synthetic_week
from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2


def test_hyphenated_noun_noun_inflects_both_stems():
    assert _phrase_to_dative("улицы-границы микрорайона") == (
        "улицам-границам микрорайона"
    )
    assert _phrase_to_dative("реки-границы района") == "рекам-границам района"
    assert _noun_to_dative("стены-перегородки") == "стенам-перегородкам"


def test_substantivized_adjective_head_uses_adjective_dative():
    assert _phrase_to_dative("ответственные") == "ответственным"
    assert _phrase_to_dative("дежурные") == "дежурным"
    assert _phrase_to_dative("ответственные за питание") == (
        "ответственным за питание"
    )


def test_noun_plus_coordinated_adjectives_agree_in_dative():
    assert _phrase_to_dative("снаряжение личное и групповое") == (
        "снаряжению личному и групповому"
    )
    assert _phrase_to_dative("оборудование учебное и спортивное") == (
        "оборудованию учебному и спортивному"
    )


def test_ordinary_noun_control_object_stays_correct():
    assert _phrase_to_dative("история создания школы") == "истории создания школы"
    assert _phrase_to_dative("значение туризма").startswith("значению")
    assert _phrase_to_dative(
        "перспективы занятий туристско-краеведческой деятельностью"
    ) == "перспективам занятий туристско-краеведческой деятельностью"
    derived = derive_fields_v2(
        topic_title="История школы",
        theory_text="История создания школы, адрес.",
        practice_text="",
        program_content="История создания школы, адрес.",
        theory_hours=2,
        practice_hours=0,
    )
    assert derived.assessment_method.startswith("устный опрос по истории")
    assert derived.planned_result.casefold().startswith("характеризует")


def test_finite_verb_still_skips_noun_dative():
    assert _noun_to_dative("проводит") == "проводит"
    assert _noun_to_dative("анализирует") == "анализирует"
    assert "проводиту" not in _phrase_to_dative("проводит тестирование")
    assert "анализируету" not in _phrase_to_dative("анализирует ситуации")


def test_mixed_week_keeps_finite_control_and_inflects_knowledge_np():
    row = _synthetic_week(
        (
            "1.1",
            "Знакомство с деятельностью",
            "Перспективы занятий туристско-краеведческой деятельностью.",
            1,
            0,
        ),
        (
            "1.1а",
            "Знакомство с детьми",
            "Проведение тестирования «Какой Я».",
            0,
            1,
        ),
    )
    merged = build_lesson_content_v2((row,))[0]
    control = merged.assessment_method.casefold()
    assert "проводиту" not in control
    assert "устный опрос по перспективам" in control
    assert "наблюдение за проведением тестирования" in control
