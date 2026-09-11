"""Ярлык роли с перечнем остаётся SOURCE; падеж роли проверяется в CONTROL."""

from calendar_pedagoga.content_engine_v2 import _phrase_to_dative, derive_fields_v2


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


def test_role_catalogue_keeps_source_and_dative_role_in_control():
    derived = _theory(
        "Ответственные: за питание, за походный дневник, по охране природы."
    )
    result = derived.planned_result.casefold()
    # Перечень обязанностей не задаёт действия ученика: предикат не выдумывается.
    assert "характеризует" not in result
    assert "называет" not in result
    assert _kept_for_review(derived, "Ответственные")
    control = derived.assessment_method.casefold()
    assert "устный опрос по ответственным за питание" in control
    assert "ответственным за" in control
    assert "ответственных за" not in control
    assert "ответственныму" not in control


def test_same_role_catalogue_rule_for_another_label():
    derived = _theory(
        "Дежурные: за питание, за походный дневник, по охране природы."
    )
    result = derived.planned_result.casefold()
    assert "характеризует" not in result
    assert "называет" not in result
    assert _kept_for_review(derived, "Дежурные")
    control = derived.assessment_method.casefold()
    assert "устный опрос по дежурным за питание" in control
    assert "дежурных за" not in control
    assert "дежурныму" not in control


def test_trip_catalogue_keeps_inanimate_dative_in_control():
    derived = _theory(
        "Экскурсионные поездки: Стерлитамакские Шиханы, водопад Кук-Караук и другие.",
        topic="Экскурсии",
    )
    result = derived.planned_result.casefold()
    assert "характеризует" not in result
    assert "называет" not in result
    assert _kept_for_review(derived, "Экскурсионные поездки")
    control = derived.assessment_method.casefold()
    assert "по экскурсионным поездкам" in control
    assert "по экскурсионных" not in control
    assert "шиханы" not in control


def test_dative_from_already_accusative_role_head_stays_correct():
    assert _phrase_to_dative("ответственных за питание") == (
        "ответственным за питание"
    )
    assert _phrase_to_dative("дежурных за питание") == "дежурным за питание"
