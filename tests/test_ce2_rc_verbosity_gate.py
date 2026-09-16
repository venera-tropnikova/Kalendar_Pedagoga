# -*- coding: utf-8 -*-
"""Universal RESULT/CONTROL catalogue compression and FINAL verbosity gate."""

from __future__ import annotations

import re

import pytest

from calendar_pedagoga.content_engine_v2 import (
    LessonContentV2Row,
    _clause_meaning_preserved_in_result,
    _compress_exercise_catalogues_in_text,
    _derive_week_fields_v2,
    _fold_week_result,
    _quoted_actions_control,
    _rc_verbosity_block_reasons,
    derive_fields_v2,
    week_has_unresolved_mandatory_review,
)
from calendar_pedagoga.content_generation import CalendarContentRow, MatchStatus


def test_ofp_catalogue_compressed_in_result_keeps_dosage() -> None:
    source = (
        "Круговое ОФП: упражнение А, упражнение Б, упражнение В, "
        "упражнение Г (3 круга). Лазание по учебной трассе (2 раза)."
    )
    derived = derive_fields_v2(
        topic_title="Практика",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result
    control = derived.assessment_method
    low = result.casefold()
    assert "офп" in low
    assert "3 круга" in low or "(3 круга)" in low
    assert ":" not in result or not re.search(r"(?i)офп\s*:", result)
    assert "упражнение а" not in low
    assert "упражнение б" not in low
    assert "лазан" in low or "трасс" in low
    assert "проверяются действия" not in control.casefold()
    assert not _rc_verbosity_block_reasons(result, control)


def test_control_does_not_quote_full_result_sentences() -> None:
    result = (
        "Отрабатывает постановку ног. Выполняет круговое ОФП (2 круга). "
        "Проходит учебный траверс (2 раза)."
    )
    control = _quoted_actions_control(result)
    assert "проверяются действия" not in control.casefold()
    assert "проверяется действие" not in control.casefold()
    assert not re.search(r"«[^»]{72,}»", control)
    assert re.search(
        r"(?i)педагогическое наблюдение|практическ\w+ проверк|устный опрос|проверк\w+ выполнен",
        control,
    )
    assert "проверяются действия" not in control.casefold()


def test_homogeneous_actions_fold_without_losing_objects() -> None:
    source = (
        "Отработка постановки ног. Выполнение смены зацепов для рук. "
        "Прохождение траверса (2 раза)."
    )
    derived = derive_fields_v2(
        topic_title="Техника",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert "ног" in low
    assert "зацеп" in low or "рук" in low
    assert "траверс" in low
    assert not _rc_verbosity_block_reasons(
        derived.planned_result, derived.assessment_method
    )


def test_final_gate_blocks_control_that_duplicates_result() -> None:
    result = (
        "Выполняет круговое ОФП: планка, выпрыгивание, вис, отжимания (2 круга). "
        "Проходит учебную трассу."
    )
    control = (
        "Педагогическое наблюдение: проверяются действия "
        "«Выполняет круговое ОФП: планка, выпрыгивание, вис, отжимания (2 круга)», "
        "«Проходит учебную трассу»"
    )
    reasons = _rc_verbosity_block_reasons(result, control)
    assert reasons
    assert any(
        "дублир" in item.casefold()
        or "цитат" in item.casefold()
        or "офп" in item.casefold()
        for item in reasons
    )

    content = CalendarContentRow(
        week_number=1,
        date_range="01–07.01",
        month="Январь",
        section="Практика",
        topic_number="1",
        topic_title="Практика",
        source_topic_title="Практика",
        theory_hours=0,
        practice_hours=2,
        total_hours=2,
        match_status=MatchStatus.TEXT_MATCH,
        program_section="",
        program_topic="Практика",
        program_content_full=result,
        program_content_preview=result,
        source_program_name="p",
        source_utp_name="u",
        week_parts=(),
        warnings=(),
        knowledge_outcomes=(),
        skill_outcomes=(),
    )
    lesson = LessonContentV2Row(
        source=content,
        theory_text="",
        practice_text=result,
        lesson_type="практическое занятие",
        planned_result=result,
        assessment_method=control,
        action="",
        object="",
        conditions="",
        warnings=(),
        clause_coverage=(),
        clause_roles=(),
    )
    assert week_has_unresolved_mandatory_review(lesson)


def test_compress_helper_keeps_label_and_dosage() -> None:
    text = (
        "Выполняет круговое ОФП: планка, выпрыгивание, вис на турнике, "
        "коллективные приседания (2 круга)."
    )
    out = _compress_exercise_catalogues_in_text(text)
    low = out.casefold()
    assert "офп" in low
    assert "(2 круга)" in low or "2 круга" in low
    assert "планка" not in low
    assert "приседания" not in low


def _catalogue_meaning_preserved(source: str, result: str) -> bool:
    return _clause_meaning_preserved_in_result(
        source,
        result,
        topic_title="Практика",
        theory_hours=0,
        practice_hours=2,
    )


def test_homogeneous_catalogue_with_one_dosage_is_covered() -> None:
    source = "Круговое ОФП: планка, выпрыгивания, отжимания (2 круга)"
    result = "Выполняет круговое ОФП (2 круга)."

    assert _catalogue_meaning_preserved(source, result)


def test_homogeneous_catalogue_with_repeated_same_dosage_is_covered() -> None:
    source = (
        "Круговое ОФП: планка (2 круга), выпрыгивания (2 круга), "
        "отжимания (2 круга)"
    )
    result = "Выполняет круговое ОФП (2 круга)."

    assert _catalogue_meaning_preserved(source, result)


def test_catalogue_with_different_dosages_is_not_auto_covered() -> None:
    source = (
        "Круговое ОФП: планка (2 круга), выпрыгивания (10 раз), "
        "отжимания (2 круга)"
    )
    compact_result = "Выполняет круговое ОФП (2 круга)."

    assert not _catalogue_meaning_preserved(source, compact_result)
    assert _compress_exercise_catalogues_in_text(source) == source


def test_catalogue_without_dosage_is_not_auto_covered() -> None:
    source = "Круговое ОФП: планка, выпрыгивания, отжимания"
    compact_result = "Выполняет круговое ОФП."

    assert not _catalogue_meaning_preserved(source, compact_result)


def _early_catalogue_coverage(source: str) -> str:
    combined = source + ". Измерение пульса."
    result = _derive_week_fields_v2(
        topic_title="Практика",
        theory_text="",
        practice_text=combined,
        program_content=combined,
        theory_hours=0,
        practice_hours=2,
    )
    return dict(result.clause_coverage)[source]


def test_early_homogeneous_catalogue_with_one_dosage_is_covered() -> None:
    source = "Круговое ОФП: планка, выпрыгивания, отжимания (2круга)"

    assert _early_catalogue_coverage(source) == "COVERED"


def test_early_homogeneous_catalogue_with_repeated_dosage_is_covered() -> None:
    source = (
        "Круговое ОФП: планка (2 круга), выпрыгивания (2 круга), "
        "отжимания (2 круга)"
    )

    assert _early_catalogue_coverage(source) == "COVERED"


def test_early_catalogue_with_different_dosages_needs_review() -> None:
    source = (
        "Круговое ОФП: планка (2 круга), выпрыгивания (10 раз), "
        "отжимания (2 круга)"
    )

    assert _early_catalogue_coverage(source) == "NEEDS_REVIEW"


def test_early_catalogue_without_dosage_needs_review() -> None:
    source = "Круговое ОФП: планка, выпрыгивания, отжимания"

    assert _early_catalogue_coverage(source) == "NEEDS_REVIEW"


def test_early_ordinary_list_keeps_previous_coverage() -> None:
    source = "Отработка технических приёмов: диагональный шаг, накат, скрутка"

    assert _early_catalogue_coverage(source) == "COVERED"


def test_early_catalogue_does_not_mix_neighbor_action_dosage() -> None:
    source = (
        "Лазание трасс средней сложности на время (5 мин.) "
        "Круговое ОФП: планка, прыжки, подъём ног в висе (2 круга)"
    )

    assert _early_catalogue_coverage(source) == "COVERED"


def test_week_compaction_merges_same_action_object_and_keeps_three_dosages() -> None:
    source = (
        "Лазание лёгких трасс (по 3 раза). "
        "Лазание лёгких трасс (по 4 раза). "
        "Лазание лёгких трасс (по 5 раз)."
    )
    derived = derive_fields_v2(
        topic_title="Практика",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )

    low = derived.planned_result.casefold()
    assert low.count("лазан") == 1
    assert re.search(r"\(по 3, 4 и 5 раз\)", low)
    assert all(status == "COVERED" for _, status in derived.clause_coverage)
    assert derived.assessment_method.casefold().count("лазан") == 1
    assert ";" not in derived.assessment_method


def test_week_compaction_deduplicates_exact_dosage() -> None:
    result = _fold_week_result(
        "Выполняет лазанье лёгких трасс (по 3 раза), "
        "лазанье лёгких трасс (по 3 раза)."
    )

    assert result.casefold().count("лазанье лёгких трасс") == 1
    assert result.casefold().count("по 3 раза") == 1


def test_week_compaction_merges_equivalent_semantic_labels() -> None:
    result = _fold_week_result(
        "Выполняет лазание лёгких трасс (по 3 раза) и "
        "лазанье легких трасс (по 4 раза)."
    )

    assert result.casefold().count("трасс") == 1
    assert "(по 3 и 4 раза)" in result.casefold()


def test_week_compaction_does_not_merge_different_objects() -> None:
    result = _fold_week_result(
        "Выполняет лазанье лёгких трасс (по 3 раза) и "
        "прохождение лёгких трасс (по 4 раза)."
    )

    assert "лазанье лёгких трасс (по 3 раза)" in result.casefold()
    assert "прохождение лёгких трасс (по 4 раза)" in result.casefold()


def test_week_compaction_does_not_merge_different_conditions() -> None:
    result = _fold_week_result(
        "Выполняет лазанье лёгких трасс на время (по 3 раза) и "
        "лазанье лёгких трасс на скорость (по 4 раза)."
    )

    assert "на время (по 3 раза)" in result.casefold()
    assert "на скорость (по 4 раза)" in result.casefold()
    assert result.casefold().count("лазанье лёгких трасс") == 2


def test_week_compaction_does_not_merge_different_difficulty() -> None:
    result = _fold_week_result(
        "Выполняет лазанье лёгких трасс (по 3 раза) и "
        "лазанье сложных трасс (по 4 раза)."
    )

    assert "лёгких трасс (по 3 раза)" in result.casefold()
    assert "сложных трасс (по 4 раза)" in result.casefold()


def test_week_compaction_folds_three_characterize_objects() -> None:
    result = _fold_week_result(
        "Характеризует историю прибора. "
        "Характеризует назначение прибора. "
        "Характеризует устройство прибора."
    )

    assert result == (
        "Характеризует историю прибора, назначение прибора и устройство прибора."
    )


def test_week_compaction_folds_three_named_objects() -> None:
    result = _fold_week_result(
        "Называет виды узлов. Называет виды страховки. Называет виды снаряжения."
    )

    assert result == "Называет виды узлов, виды страховки и виды снаряжения."


def test_week_compaction_keeps_mixed_knowledge_predicates_in_two_groups() -> None:
    result = _fold_week_result(
        "Характеризует историю прибора. Характеризует устройство прибора. "
        "Называет виды приборов. Называет части приборов."
    )

    assert result == (
        "Характеризует историю прибора и устройство прибора. "
        "Называет виды приборов и части приборов."
    )


def test_week_compaction_deduplicates_knowledge_object() -> None:
    result = _fold_week_result(
        "Характеризует историю прибора. Характеризует историю прибора."
    )

    assert result == "Характеризует историю прибора."


def test_week_compaction_keeps_grammar_unsafe_knowledge_run() -> None:
    source = (
        "Характеризует историю прибора. "
        "Характеризует правилу безопасного поведения."
    )

    assert _fold_week_result(source) == source


def test_week_common_head_factors_three_ordered_objects() -> None:
    result = _fold_week_result(
        "Отрабатывает технику выполнения упражнений на развитие силовых качеств. "
        "Отрабатывает технику выполнения упражнений на развитие координации. "
        "Отрабатывает технику выполнения упражнений на развитие скоростных способностей."
    )

    assert result == (
        "Отрабатывает упражнения на развитие силовых качеств, координации "
        "и скоростных способностей."
    )


def test_week_common_head_keeps_ordered_dosage_vector() -> None:
    result = _fold_week_result(
        "Выполняет упражнения на равновесие (3 раза). "
        "Выполняет упражнения на равновесие (4 раза)."
    )

    assert result == "Выполняет упражнения на равновесие (3 и 4 раза)."
    assert "заданн" not in result.casefold()


def test_week_common_head_preserves_different_conditions() -> None:
    result = _fold_week_result(
        "Выполняет упражнения на равновесие с опорой. "
        "Выполняет упражнения на равновесие без опоры."
    )

    assert "с опорой" in result.casefold()
    assert "без опоры" in result.casefold()


def test_week_common_head_keeps_mixed_predicates_separate() -> None:
    result = _fold_week_result(
        "Выполняет упражнения на равновесие. "
        "Отрабатывает технику страховки."
    )

    assert result == (
        "Выполняет упражнения на равновесие. Отрабатывает технику страховки."
    )


def test_week_common_head_fails_closed_on_ambiguous_enumeration() -> None:
    source = (
        "Выполняет упражнения на пресс: подъём ног. "
        "Выполняет упражнения на равновесие."
    )

    assert _fold_week_result(source) == source


def test_appendix_reference_is_provenance_not_result() -> None:
    source = (
        "Выполнение упражнений на развитие мышц пресса (приложение №1). "
        "Выполнение упражнений на равновесие (приложение №1)."
    )
    derived = derive_fields_v2(
        topic_title="Практика",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )

    assert "приложение" not in derived.planned_result.casefold()
    assert "мышц пресса" in derived.planned_result.casefold()
    assert "равновес" in derived.planned_result.casefold()
    assert all(status == "COVERED" for _, status in derived.clause_coverage)


def test_common_head_control_uses_semantic_labels_without_result_quotes() -> None:
    source = (
        "Выполнение упражнений на развитие мышц пресса. "
        "Выполнение упражнений на равновесие."
    )
    derived = derive_fields_v2(
        topic_title="Практика",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )

    control = derived.assessment_method.casefold()
    assert control.startswith("педагогическое наблюдение:")
    assert "мышц пресса" in control
    assert "равновес" in control
    assert "выполнение —" not in control
    assert "«выполняет" not in control
    assert all(status == "COVERED" for _, status in derived.clause_coverage)


def test_control_compacts_dosage_items_to_ordered_skill_labels() -> None:
    source = (
        "Лазание лёгких трасс (по 3 раза). "
        "Круговое ОФП (2 круга). "
        "Коллективные приседания (40 раз)."
    )
    derived = derive_fields_v2(
        topic_title="Практика",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )

    control = derived.assessment_method.casefold()
    assert control.startswith("педагогическое наблюдение:")
    assert "лазан" in control
    assert "офп" in control
    assert "приседан" in control
    assert "3 раза" not in control
    assert "2 круга" not in control
    assert "40 раз" not in control
    assert all(status == "COVERED" for _, status in derived.clause_coverage)


def test_control_uses_game_skill_category_without_repeating_named_examples() -> None:
    source = (
        "Игры на развитие внимания: «Повторюшки», «Земля, вода, лава». "
        "Игры на быстроту реакции: «Выше ноги от земли», «Твистер»."
    )
    derived = derive_fields_v2(
        topic_title="Игры",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )

    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "повторюшки" in result and "твистер" in result
    assert "внимани" in control and "реакц" in control
    assert "повторюшки" not in control and "твистер" not in control
    assert all(status == "COVERED" for _, status in derived.clause_coverage)


@pytest.mark.parametrize(
    "source",
    (
        "Выполнение упражнения без страховки запрещено.",
        "Не выполнять упражнение без страховки.",
        "Нельзя трогать провода.",
        "Не допускается проводить опыт без защиты.",
    ),
)
def test_week_compaction_keeps_r13_fail_closed(source: str) -> None:
    derived = derive_fields_v2(
        topic_title="Техника безопасности",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=1,
    )

    assert derived.planned_result == ""
    assert derived.assessment_method == ""
    assert any("NEEDS_REVIEW" in warning for warning in derived.warnings)
