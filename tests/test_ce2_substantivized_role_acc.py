"""Substantivized role adjectives take animate accusative after характеризует."""

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


def test_characterize_puts_role_adjective_in_animate_accusative():
    derived = _theory(
        "Ответственные: за питание, за походный дневник, по охране природы."
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "характеризует ответственных за питание" in result
    assert "характеризует ответственные за" not in result
    assert "устный опрос по ответственным за питание" in control
    assert "ответственным за" in control
    assert "ответственных за" not in control


def test_same_accusative_rule_for_another_role_adjective():
    derived = _theory(
        "Дежурные: за питание, за походный дневник, по охране природы."
    )
    result = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "характеризует дежурных за питание" in result
    assert "характеризует дежурные за" not in result
    assert "устный опрос по дежурным за питание" in control


def test_modifier_plus_noun_stays_inanimate_accusative():
    derived = _theory(
        "Экскурсионные поездки: Стерлитамакские Шиханы, водопад Кук-Караук и другие.",
        topic="Экскурсии",
    )
    low = derived.planned_result.casefold()
    assert "характеризует экскурсионные поездки" in low
    assert "характеризует экскурсионных" not in low


def test_dative_from_already_accusative_role_head_stays_correct():
    assert _phrase_to_dative("ответственных за питание") == (
        "ответственным за питание"
    )
    assert _phrase_to_dative("дежурных за питание") == "дежурным за питание"
