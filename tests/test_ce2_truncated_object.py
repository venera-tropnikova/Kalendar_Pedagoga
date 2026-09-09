"""Truncated substantivized objects must keep a meaningful complement."""

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
    control = derived.assessment_method.casefold()
    assert "за питание" in low
    assert "походн" in low
    assert "охран" in low
    assert not low.startswith("характеризует дежурные.")
    assert "за питание" in control
    assert "дежурныму" not in control


def test_same_rule_holds_for_another_adjectival_role_list():
    derived = _theory("Старшие: за маршрут, по карте.")
    low = derived.planned_result.casefold()
    assert "маршрут" in low
    assert "карт" in low
    assert low != "характеризует старшие."
    assert "маршрут" in derived.assessment_method.casefold()
