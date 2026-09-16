"""Synthetic coverage for the safe QUALITY-2 features retained on current CE2."""

from calendar_pedagoga.content_engine_v2 import derive_fields_v2
from calendar_pedagoga.practice_slots import split_catalog_across_weeks


def _derive(source: str, *, index: int = 0, weeks: int = 0):
    return derive_fields_v2(
        topic_title="Практическая тема",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
        occurrence_index=index,
        practice_appearance_count=weeks,
    )


def test_action_reconstruction_converts_assembly_to_finite_verb() -> None:
    derived = _derive("Сборка учебной модели.")

    assert derived.planned_result.startswith("Собирает учебную модель")
    assert derived.assessment_method
    assert derived.clause_coverage == (("Сборка учебной модели", "COVERED"),)


def test_programming_types_are_grounded_in_week_content() -> None:
    programming = _derive(
        "Составление программ движения по линии и остановки у стены."
    )
    algorithm = _derive("Разработка алгоритма ветвления по датчику.")

    assert programming.lesson_type == "практикум программирования"
    assert algorithm.lesson_type == "практикум по разработке алгоритма"
    assert all(
        status == "COVERED"
        for derived in (programming, algorithm)
        for _clause, status in derived.clause_coverage
    )


def test_colon_object_catalog_is_distributed_in_order_without_leakage() -> None:
    source = (
        "Изготовление учебных макетов: макет-мельница, макет-мост, "
        "макет-башня."
    )
    tokens = ("макет-мельница", "макет-мост", "макет-башня")

    parts = split_catalog_across_weeks(source, 3)
    assert parts is not None
    assert tuple(
        next(token for token in tokens if token in part)
        for part in parts
    ) == tokens

    rows = tuple(_derive(source, index=index, weeks=3) for index in range(3))
    for index, row in enumerate(rows):
        own = tokens[index]
        others = set(tokens) - {own}
        assert own in row.planned_result.casefold()
        assert not any(token in row.planned_result.casefold() for token in others)
        assert all(status == "COVERED" for _clause, status in row.clause_coverage)


def test_ambiguous_colon_object_catalog_is_fail_closed() -> None:
    source = (
        "Изготовление учебных макетов: макет-мельница; окраска макета, "
        "макет-башня."
    )

    assert split_catalog_across_weeks(source, 3) is None
    derived = _derive(source, weeks=3)
    assert any(status == "NEEDS_REVIEW" for _clause, status in derived.clause_coverage)
    assert any("неоднозначный каталог" in warning for warning in derived.warnings)
