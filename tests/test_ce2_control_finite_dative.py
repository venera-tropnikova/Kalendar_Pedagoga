"""CONTROL must not dative a leftover finite verb as if it were a noun."""

from calendar_pedagoga.content_engine_v2 import (
    ActionFrame,
    _align_control_to_result,
    _control_from_proven_result,
    _merge_part_controls,
    _noun_to_dative,
    _oral_object_for_control,
    _phrase_to_dative,
    build_lesson_content_v2,
    control_from_frame,
    derive_fields_v2,
)
from test_ce2_grounded_triad import _synthetic_week


def test_action_finite_result_does_not_become_verb_plus_u():
    result = "Читает легенду края."
    control = control_from_frame(
        ActionFrame("Чтение легенды края.", "", "", ""),
        lesson_type="практическое занятие",
        theory_hours=0,
        practice_hours=2,
        planned_result=result,
    )
    assert "читаету" not in control.casefold()
    assert "устный опрос по читает" not in control.casefold()
    assert not control.startswith("устный опрос по " + "читает")
    assert _oral_object_for_control("читает легенду края") == ""
    assert "читаету" not in _phrase_to_dative("читает легенду края")


def test_another_action_finite_stays_off_oral_dative():
    derived = derive_fields_v2(
        topic_title="Макет местности",
        theory_text="",
        practice_text="Изготовление макета местности.",
        program_content="Изготовление макета местности.",
        theory_hours=0,
        practice_hours=2,
    )
    assert derived.planned_result.casefold().startswith("изготавливает")
    control = derived.assessment_method.casefold()
    assert "изготавливаету" not in control
    assert not control.startswith("устный опрос по изготавливает")


def test_knowledge_result_still_builds_oral_control():
    derived = derive_fields_v2(
        topic_title="История школы",
        theory_text="История создания школы, адрес.",
        practice_text="",
        program_content="История создания школы, адрес.",
        theory_hours=2,
        practice_hours=0,
    )
    assert derived.planned_result.casefold().startswith("характеризует")
    assert derived.assessment_method.startswith("устный опрос по ")
    assert "истории" in derived.assessment_method.casefold()
    assert derived.lesson_type == "теоретическое занятие"


def test_mixed_theory_practice_does_not_create_malformed_oral_tail():
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
    result = merged.planned_result.casefold()
    assert "проводиту" not in control
    assert "проводит тестирование" in result
    # Номинальный заголовок теории не доказывает действия ученика: SOURCE
    # сохраняется, клауза уходит в NEEDS_REVIEW, предикат не выдумывается.
    theory_clause = "Перспективы занятий туристско-краеведческой деятельностью"
    assert "характеризует" not in result
    assert "называет" not in result
    assert (theory_clause, "NEEDS_REVIEW") in merged.clause_coverage
    assert any(
        "NEEDS_REVIEW" in warning and theory_clause in warning
        for warning in merged.warnings
    )
    assert "устный опрос по перспективам занятий" in control
    assert "проводиту тестирование" not in control
    stitched = _merge_part_controls(
        [
            "устный опрос по перспективам занятий",
            "устный опрос по проводиту тестирование «Какой Я»",
        ]
    )
    assert "проводиту" not in stitched.casefold()


def test_analysis_action_result_does_not_dative_the_finite_verb():
    derived = derive_fields_v2(
        topic_title="Ситуации общения",
        theory_text="",
        practice_text="Анализ ситуаций, игры-фантазии.",
        program_content="Анализ ситуаций, игры-фантазии.",
        theory_hours=0,
        practice_hours=2,
        occurrence_index=0,
        practice_appearance_count=2,
    )
    assert derived.planned_result.casefold().startswith("анализирует")
    assert derived.lesson_type == "практическое занятие"
    control = derived.assessment_method.casefold()
    assert "анализируету" not in control
    assert "устный опрос по анализирует" not in control


def test_align_does_not_rebuild_oral_from_whole_action_result():
    aligned = _align_control_to_result(
        "устный опрос по теме",
        "Анализирует ситуации.",
    )
    assert "анализируету" not in aligned.casefold()
    assert not aligned.startswith("устный опрос по анализирует")
    proven = _control_from_proven_result(
        "Характеризует перспективы занятий. Проводит тестирование «Какой Я»."
    )
    assert "проводиту" not in proven.casefold()
    assert proven.startswith("устный опрос по ")


def test_nouns_with_verb_like_endings_still_take_dative():
    assert _noun_to_dative("пакет") == "пакету"
    assert _noun_to_dative("предмет") == "предмету"
    assert _noun_to_dative("ответ") == "ответу"
    assert _phrase_to_dative("значение туризма").startswith("значению")
    assert _noun_to_dative("читает") == "читает"
    assert _noun_to_dative("проводит") == "проводит"
    assert _noun_to_dative("анализирует") == "анализирует"
