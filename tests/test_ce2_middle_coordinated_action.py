# -*- coding: utf-8 -*-
"""Middle coordinated action in a clause chain must not disappear."""

from calendar_pedagoga.content_engine_v2 import (
    _shared_object_after_paired_verbs,
    derive_fields_v2,
)


def test_shared_object_keeps_repair_and_handover() -> None:
    paired = _shared_object_after_paired_verbs("Ремонт и сдача инвентаря")
    assert paired is not None
    phrase, action, _obj = paired
    low = phrase.casefold()
    assert "ремонт" in low
    assert "сдач" in low
    assert "инвентар" in low
    assert "ремонт" in action.casefold() and "сдач" in action.casefold()


def test_middle_coordinated_action_in_clause_chain_is_kept() -> None:
    """A three-clause week must keep the middle coordinated pair."""

    source = (
        "Составление отчёта о походе. "
        "Ремонт и сдача инвентаря. "
        "Подготовка экспонатов для школьного музея."
    )
    derived = derive_fields_v2(
        topic_title="Подведение итогов туристского путешествия",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "отчёт" in result or "отчет" in result
    assert "ремонт" in result
    assert "сдач" in result
    assert "инвентар" in result
    assert "экспонат" in result
    assert "ремонт" in control or "сдач" in control
    assert "инвентар" in control
    assert all(status == "COVERED" for _clause, status in derived.clause_coverage)
    assert not any("Ремонт и сдача" in warning for warning in derived.warnings)
