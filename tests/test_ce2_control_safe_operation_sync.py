# -*- coding: utf-8 -*-
"""CONTROL quotes must follow gated safe-operation RESULT wording."""

from calendar_pedagoga.content_engine_v2 import derive_fields_v2


def test_positive_control_cites_safe_paper_azimuth_construction() -> None:
    """Positive: CONTROL quotes «выполняет построение…», not «строит … заданных»."""

    practice = (
        "Построение на бумаге заданных азимутов. "
        "Упражнения на глазомерную оценку азимутов. "
        "Построение тренировочных азимутальных треугольников."
    )
    derived = derive_fields_v2(
        topic_title="Азимут",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "выполняет построение на бумаге заданных азимутов" in result
    assert "выполняет построение на бумаге заданных азимутов" in control
    assert "строит на бумаге заданных" not in control
    assert "треугольн" in result and "треугольн" in control


def test_negative_safe_control_sync_does_not_invent_quote() -> None:
    """Negative: a clean RESULT keeps its CONTROL without injecting построение."""

    derived = derive_fields_v2(
        topic_title="Измерение",
        theory_text="",
        practice_text="Измерение пульса.",
        program_content="Измерение пульса.",
        theory_hours=0,
        practice_hours=2,
    )
    control = derived.assessment_method.casefold()
    assert "построение" not in control
    assert "измер" in control or "пульс" in control
