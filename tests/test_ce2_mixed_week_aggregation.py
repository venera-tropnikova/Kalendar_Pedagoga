"""Independent week_parts keep separate grounded triads."""

from calendar_pedagoga.content_engine_v2 import (
    _aggregate_week_lesson_type,
    _merge_part_results,
    build_lesson_content_v2,
)
from calendar_pedagoga.content_generation import WeekTopicPart
from calendar_pedagoga.matching import MatchStatus
from test_ce2_grounded_triad import _synthetic_week, _type_part, _type_result
from test_content_engine_v2 import _fill_tp_topic
from tp1_fixed_content import tp1_number_bound_content_rows


def test_two_theory_topics_keep_two_result_sentences_and_two_orals() -> None:
    row = _synthetic_week(
        ("A.1", "История прибора", "История прибора в городе.", 1, 0),
        ("A.2", "Роль прибора", "Роль прибора в обучении и выборе профессии.", 1, 0),
    )
    merged = build_lesson_content_v2((row,))[0]
    first, second = [
        item.strip()
        for item in merged.planned_result.replace(". ", ".\n").split("\n")
        if item.strip()
    ]
    assert first.startswith("Характеризует историю")
    assert second.startswith("Характеризует роль")
    assert merged.planned_result.count(".") == 2
    chunks = [item.strip() for item in merged.assessment_method.split(";")]
    assert len(chunks) == 2
    assert chunks[0].startswith("устный опрос по истории")
    assert chunks[1].startswith("устный опрос по роли")
    assert merged.lesson_type == "теоретическое занятие"


def test_theory_and_practice_topics_keep_both_triads() -> None:
    row = _synthetic_week(
        (
            "A.1",
            "Роль прибора",
            "Роль прибора в обучении, в выборе профессии и подготовке к труду.",
            1,
            0,
        ),
        (
            "A.2",
            "Сборка прибора",
            "Проведение тестирования учебной модели.",
            0,
            1,
        ),
    )
    merged = build_lesson_content_v2((row,))[0]
    low = merged.planned_result.casefold()
    assert "характеризует" in low
    assert "роль прибора" in low
    assert "тестирован" in low or "выполняет" in low
    assert merged.planned_result.casefold().count("характеризует") == 1
    chunks = [item.strip() for item in merged.assessment_method.split(";")]
    assert any(item.startswith("устный опрос по") for item in chunks)
    assert any(
        item.startswith("педагогическое наблюдение") or item.startswith("проверка")
        for item in chunks
    )
    oral = next(item for item in chunks if item.startswith("устный опрос по"))
    assert "рол" in oral.casefold()
    assert "сборк" not in oral.casefold()


def test_differing_part_types_use_compound_week_type() -> None:
    actual = _aggregate_week_lesson_type(
        (
            WeekTopicPart(
                topic_number="A.1",
                topic_title="Теория",
                section="Раздел",
                theory_hours=1,
                practice_hours=0,
                match_status=MatchStatus.EXACT,
                program_section="Раздел",
                program_topic="Теория",
                program_content_full="Основные понятия.",
            ),
            WeekTopicPart(
                topic_number="A.2",
                topic_title="Практика",
                section="Раздел",
                theory_hours=0,
                practice_hours=1,
                match_status=MatchStatus.EXACT,
                program_section="Раздел",
                program_topic="Практика",
                program_content_full="Выполнение упражнения.",
            ),
        ),
        [_type_result("теоретическое занятие"), _type_result("практикум")],
        theory_text="Основные понятия.",
        practice_text="Выполнение упражнения.",
    )
    assert actual == "теоретико-практическое занятие"


def test_same_topic_confirmed_practice_type_still_wins() -> None:
    actual = _aggregate_week_lesson_type(
        (_type_part(1, 0), _type_part(0, 1)),
        [_type_result("теоретическое занятие"), _type_result("практикум")],
        theory_text="Основные понятия.",
        practice_text="Выполнение задания.",
    )
    assert actual == "практикум"


def test_same_topic_objects_still_join_with_and() -> None:
    derived = _fill_tp_topic("1.2")
    assert derived.planned_result.startswith("Характеризует роль туризма")
    assert " и " in derived.planned_result
    assert derived.planned_result.casefold().count("характеризует") == 1
    assert derived.assessment_method.startswith("устный опрос по роли туризма")
    assert derived.assessment_method.count("устный опрос") == 1
    folded = _merge_part_results(
        [
            "Характеризует историю прибора в городе.",
            "Характеризует роль прибора в обучении.",
        ]
    )
    assert folded == (
        "Характеризует историю прибора в городе и роль прибора в обучении."
    )


def test_tp_w01_keeps_two_topic_triads() -> None:
    lesson = build_lesson_content_v2(tp1_number_bound_content_rows())[0]
    assert lesson.source.week_number == 1
    assert lesson.lesson_type == "теоретическое занятие"
    assert lesson.planned_result == (
        "Характеризует историю развития туризма в г. Салават. "
        "Характеризует роль туризма в подготовке к защите Родины, "
        "в выборе профессии и подготовке к предстоящей трудовой деятельности."
    )
    assert lesson.assessment_method == (
        "устный опрос по истории развития туризма в г. Салават; "
        "устный опрос по роли туризма в подготовке к защите Родины, "
        "в выборе профессии и подготовке к предстоящей трудовой деятельности"
    )
    assert " и роль " not in lesson.planned_result
    assert " и роли " not in lesson.assessment_method
