"""Fail-closed reconstruction of theory questions and nominal knowledge clauses."""

from calendar_pedagoga.content_engine_v2 import (
    _theory_knowledge_reconstruction,
    derive_fields_v2,
)


def _derive(theory: str):
    return derive_fields_v2(
        topic_title="Теоретическая тема",
        theory_text=theory,
        practice_text="",
        program_content=theory,
        theory_hours=1,
        practice_hours=0,
    )


def test_what_is_question_becomes_finite_explanation() -> None:
    result = _derive("Что такое краеведение?")

    assert result.planned_result == "Объясняет, что такое краеведение."
    assert result.assessment_method.casefold().startswith("устный опрос")
    assert "краеведение" in result.assessment_method.casefold()
    assert result.clause_coverage == (("Что такое краеведение?", "COVERED"),)


def test_modified_knowledge_head_keeps_every_ordered_modifier() -> None:
    source = (
        "Познавательное, оздоровительное и воспитательное значение "
        "туризма и краеведения."
    )
    result = _derive(source)

    assert result.planned_result == (
        "Характеризует познавательное, оздоровительное и воспитательное "
        "значение туризма и краеведения."
    )
    assert "краеведениям" not in result.assessment_method.casefold()
    assert "туризма и краеведения" in result.assessment_method.casefold()
    assert result.clause_coverage == ((source.rstrip("."), "COVERED"),)


def test_plural_nominal_knowledge_subject_keeps_source_government() -> None:
    source = "Перспективы занятий туристско-краеведческой деятельностью."
    result = _derive(source)

    assert result.planned_result == (
        "Объясняет, в чём состоят перспективы занятий "
        "туристско-краеведческой деятельностью."
    )
    assert result.clause_coverage == ((source.rstrip("."), "COVERED"),)


def test_key_week_one_theory_clauses_are_all_covered() -> None:
    theory = (
        "Что такое краеведение? "
        "Познавательное, оздоровительное и воспитательное значение туризма "
        "и краеведения. "
        "Перспективы занятий туристско-краеведческой деятельностью."
    )
    result = _derive(theory)

    assert len(result.clause_coverage) == 3
    assert all(status == "COVERED" for _clause, status in result.clause_coverage)
    assert result.planned_result.count("Объясняет") == 2
    assert result.planned_result.count("Характеризует") == 1
    assert "устный опрос" in result.assessment_method.casefold()


def test_unsafe_nominal_fragment_stays_needs_review() -> None:
    assert _theory_knowledge_reconstruction(
        "Материалы занятий: возможные варианты."
    ) is None
