"""Перечень обязанностей не усекается: дополнения остаются в CONTROL."""

from calendar_pedagoga.content_engine_v2 import derive_fields_v2


def _theory(source: str, topic: str = "Туристские роли"):
    return derive_fields_v2(
        topic_title=topic,
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=1,
        practice_hours=0,
    )


def _kept_for_review(derived, fragment: str) -> bool:
    """SOURCE сохранён, клауза NEEDS_REVIEW, и это объявлено в warnings."""
    flagged = [
        clause
        for clause, status in derived.clause_coverage
        if fragment in clause and status == "NEEDS_REVIEW"
    ]
    return bool(flagged) and any(
        "NEEDS_REVIEW" in warning and fragment in warning for warning in derived.warnings
    )


def test_bare_substantivized_heading_does_not_become_the_object():
    derived = _theory("Дежурные.")
    low = derived.planned_result.casefold()
    assert "характеризует дежурные" not in low
    assert not derived.assessment_method.endswith("по дежурным")


def test_colon_catalogue_keeps_concrete_complements():
    derived = _theory(
        "Дежурные: за питание, за походный дневник, по охране природы."
    )
    low = derived.planned_result.casefold()
    # Действия ученика в перечне нет, поэтому RESULT не выдумывается; при этом
    # ни одно дополнение источника не теряется в CONTROL.
    assert "характеризует" not in low
    assert "называет" not in low
    assert _kept_for_review(derived, "Дежурные")
    control = derived.assessment_method.casefold()
    assert "за питание" in control
    assert "походн" in control
    assert "охран" in control
    assert "дежурныму" not in control


def test_same_rule_holds_for_another_adjectival_role_list():
    derived = _theory("Старшие: за маршрут, по карте.")
    low = derived.planned_result.casefold()
    assert "характеризует" not in low
    assert "называет" not in low
    assert _kept_for_review(derived, "Старшие")
    control = derived.assessment_method.casefold()
    assert "маршрут" in control
    assert "карт" in control
    assert "старшиму" not in control
