"""R1: knowledge-head needs a same-clause owner; tautology salvage keeps the NP tail."""

import re

from calendar_pedagoga.content_engine_v2 import (
    _quality_issue,
    derive_fields_v2,
    fill_from_source,
)
from test_content_engine_v2 import _fill_tp_topic


def _theory(topic: str, source: str):
    return derive_fields_v2(
        topic_title=topic,
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=1,
        practice_hours=0,
    )


def _is_bare_head_result(result: str, head: str) -> bool:
    return bool(re.match(rf"(?i)^характеризует {re.escape(head)}\.?$", result.strip()))


def test_names_possessive_keeps_in_clause_owner() -> None:
    derived = _theory("Имена", "Имена и фамилии учеников, их значение.")
    low = derived.planned_result.casefold()
    assert not _is_bare_head_result(derived.planned_result, "значение")
    assert "ученик" in low
    assert derived.assessment_method != "устный опрос по значению"


def test_semicolon_anaphor_does_not_emit_bare_meaning() -> None:
    derived = _theory(
        "Животные",
        "Домашние животные; их значение в жизни человека.",
    )
    assert not _is_bare_head_result(derived.planned_result, "значение")
    assert derived.assessment_method != "устный опрос по значению"


def test_owners_from_different_clauses_are_not_mixed() -> None:
    derived = _theory(
        "Знакомство",
        "Имена учеников, их значение. Домашние животные, их роль.",
    )
    low = derived.planned_result.casefold()
    has_students = any(stem in low for stem in ("ученик", "имён", "имена"))
    has_animals = "животн" in low
    assert not (has_students and has_animals)


def test_role_keeps_post_head_owner() -> None:
    derived = _theory("Хор", "Роль хора в концерте.")
    low = derived.planned_result.casefold()
    assert low.startswith("характеризует роль хора")
    assert "хор" in derived.assessment_method.casefold()
    assert derived.assessment_method != "устный опрос по роли"


def test_tautology_salvages_subject_tail() -> None:
    derived = _theory("Глина", "Характеристика и особенности видов глины.")
    low = derived.planned_result.casefold()
    assert not low.startswith("характеризует материал по теме")
    assert "глины" in low or "виды глины" in low
    assert "глины" in derived.assessment_method.casefold() or "видам" in derived.assessment_method.casefold()


def test_bare_knowledge_heads_are_not_valid_results() -> None:
    for source in ("значение.", "роль.", "устройство.", "правила."):
        derived = _theory("Тема", source)
        assert not _is_bare_head_result(derived.planned_result, source.rstrip("."))
        assert derived.planned_result.startswith("Характеризует материал по теме")


def test_empty_source_keeps_generic_fallback() -> None:
    derived = fill_from_source(
        topic_title="Привал (бивак)",
        program_content="",
        theory_hours=1,
        practice_hours=0,
    )
    assert derived.planned_result == "Характеризует материал по теме „Привал (бивак)“."
    assert derived.assessment_method == "устный опрос по теме „Привал (бивак)“"


def test_equipment_np_does_not_regress() -> None:
    derived = _theory("Туристское снаряжение", "Снаряжение личное и групповое.")
    assert derived.planned_result == "Характеризует снаряжение личное и групповое."
    assert "снаряжен" in derived.assessment_method.casefold()


def test_tourists_w01_role_does_not_regress() -> None:
    derived = _fill_tp_topic("1.2")
    assert derived.planned_result.startswith("Характеризует роль туризма")
    assert "роли туризма" in derived.assessment_method


def test_quality_rejects_bare_meaning_result_and_oral() -> None:
    assert (
        _quality_issue("Характеризует значение.", "устный опрос по значению")
        == "missing_knowledge_owner"
    )
    assert (
        _quality_issue(
            "Характеризует роль хора в концерте.",
            "устный опрос по роли",
        )
        == "unsafe_oral_control"
    )
