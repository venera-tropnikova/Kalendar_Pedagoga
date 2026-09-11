"""W2: in-clause possessive owner stays on the knowledge object in the genitive."""

from calendar_pedagoga.content_engine_v2 import derive_fields_v2
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


def _is_generic(result: str) -> bool:
    return result.startswith("Характеризует материал по теме")


def test_compass_keeps_genitive_owner() -> None:
    derived = _theory("Компас", "Компас, его устройство и назначение")
    assert derived.planned_result == "Характеризует устройство и назначение компаса."
    assert derived.assessment_method == "устный опрос по устройству и назначению компаса"
    assert not _is_generic(derived.planned_result)


def test_compass_with_rules_tail_keeps_owner() -> None:
    derived = _theory(
        "Компас",
        "Компас, его устройство и назначение, правила обращения.",
    )
    low = derived.planned_result.casefold()
    assert "компас" in low
    assert "устройств" in low
    assert "назначен" in low
    assert not low.startswith("характеризует компас")
    assert "устройство компас" not in low
    assert not _is_generic(derived.planned_result)
    assert "компас" in derived.assessment_method.casefold()
    assert derived.assessment_method.startswith("устный опрос по")


def test_first_aid_kit_keeps_genitive_owner() -> None:
    derived = _theory("Аптечка", "Аптечка, её состав и назначение")
    low = derived.planned_result.casefold()
    assert "аптечк" in low
    assert "назначен" in low
    assert not low.startswith("характеризует аптечку, её")
    assert not _is_generic(derived.planned_result)
    assert "аптечк" in derived.assessment_method.casefold()


def test_animals_keeps_genitive_owner() -> None:
    derived = _theory("Животные", "Животные, их значение в жизни человека")
    low = derived.planned_result.casefold()
    assert derived.planned_result == "Характеризует значение животных в жизни человека."
    assert derived.assessment_method == "устный опрос по значению животных в жизни человека"
    assert "значение животные" not in low
    assert not _is_generic(derived.planned_result)
    assert "животным" not in derived.assessment_method.casefold()


def test_two_comma_owners_abstain() -> None:
    derived = _theory("Приборы", "Компас, линейка, их устройство")
    assert "устройств" not in derived.planned_result.casefold()
    if derived.planned_result.strip():
        assert _is_generic(derived.planned_result)
    else:
        assert any("NEEDS_REVIEW" in warning for warning in derived.warnings)


def test_coordinated_two_owners_abstain() -> None:
    derived = _theory("Приборы", "Компас и линейка, их устройство")
    assert "устройств" not in derived.planned_result.casefold()
    if derived.planned_result.strip():
        assert _is_generic(derived.planned_result)
    else:
        assert any("NEEDS_REVIEW" in warning for warning in derived.warnings)


def test_neighbor_clause_is_not_owner() -> None:
    dangling = _theory("Компас", "Компас. Их устройство и назначение.")
    assert "устройств" not in dangling.planned_result.casefold()
    if dangling.planned_result.strip():
        assert _is_generic(dangling.planned_result)
    else:
        assert any("NEEDS_REVIEW" in warning for warning in dangling.warnings)

    neighbor = _theory("Приборы", "Компас. Линейка, её устройство")
    low = neighbor.planned_result.casefold()
    assert "компас" not in low
    if not _is_generic(neighbor.planned_result):
        assert "линейк" in low
        assert "устройств" in low


def test_post_head_role_owner_does_not_regress() -> None:
    choir = _theory("Хор", "Роль хора в концерте.")
    assert choir.planned_result.casefold().startswith("характеризует роль хора")
    assert "хор" in choir.assessment_method.casefold()

    tourism = _fill_tp_topic("1.2")
    assert tourism.planned_result.startswith("Характеризует роль туризма")
    assert "роли туризма" in tourism.assessment_method
    assert "защите Родины" in tourism.assessment_method
    assert "выборе профессии" in tourism.assessment_method
