"""W=1 LOSS: обязательные действия в RESULT/CONTROL, без каталога всех C."""

from calendar_pedagoga.content_engine_v2 import fill_from_source
from test_ce2_grounded_triad import CE2_TP1_WEEK_SNAPSHOT
from test_content_engine_v2 import _fill_tp_topic


LOSS_NUMBERS = ("1.3", "1.4", "1.5", "2.1", "4.1")


def test_parallel_operations_of_selected_clause_are_kept() -> None:
    derived = fill_from_source(
        topic_title="Работа с картой",
        program_content=(
            "Практические занятия. Упражнения по определению масштаба, "
            "измерению расстояния на карте. Копирование на кальку участка карты."
        ),
        theory_hours=0,
        practice_hours=1,
    )
    low = derived.planned_result.casefold()
    assert "масштаб" in low
    assert "расстояни" in low
    assert "кальк" in derived.practice_text.casefold()
    if "кальк" not in low:
        assert any("кальк" in warning.casefold() and "NEEDS_REVIEW" in warning
                   for warning in derived.warnings)


def test_obligatory_neighbor_is_added_without_kinds_catalog() -> None:
    derived = fill_from_source(
        topic_title="Снаряжение",
        program_content=(
            "Практические занятия. Укладка рюкзаков, подгонка снаряжения. "
            "Работа со снаряжением, уход за ним и ремонт. "
            "Виды ремонта: замена пряжки, штопка."
        ),
        theory_hours=0,
        practice_hours=1,
    )
    low = derived.planned_result.casefold()
    assert "укладывает" in low
    assert "подгоняет" in low
    assert "ухаживает" in low
    assert "ремонтирует" in low
    assert "пряжк" not in low
    assert "штопк" not in low
    assert "виды ремонта" not in low


def test_auxiliary_study_clause_is_not_added() -> None:
    derived = fill_from_source(
        topic_title="Подготовка похода",
        program_content=(
            "Практические занятия. Составление плана подготовки похода. "
            "Изучение маршрутов походов. Составление плана-графика движения. "
            "Подготовка личного и общественного снаряжения."
        ),
        theory_hours=0,
        practice_hours=1,
    )
    low = derived.planned_result.casefold()
    assert "план подготовки" in low
    assert "план-график" in low
    assert "снаряжен" in low
    assert "маршрут" in low


def test_loss_topics_keep_required_actions_without_catalog() -> None:
    checks = {
        "1.3": (("укладывает", "подгоняет", "ухаживает", "ремонтирует"), ("виды ремонта",)),
        "1.4": (("места", "лагерь", "костёр"), ("виды костр", "нодья", "шалаш")),
        "1.5": (("план подготовки", "план-график", "снаряжен", "маршрут"), ()),
        "2.1": (("масштаб", "расстояни"), ()),
        # Гимнастика — первая фраза собственных практических занятий 4.1,
        # а не каталог: сохраняется вместе с гигиеной, одеждой и обувью.
        "4.1": (
            ("гигиен", "одежд", "обув", "гимнастик"),
            ("парная баня", "обтирание"),
        ),
    }
    for number, (need, forbid) in checks.items():
        derived = _fill_tp_topic(number)
        low = derived.planned_result.casefold()
        for token in need:
            assert token in low, f"{number}: missing {token!r} in {derived.planned_result!r}"
        for token in forbid:
            assert token not in low, f"{number}: unexpected {token!r} in {derived.planned_result!r}"
        if number == "2.1" and "кальк" not in low:
            assert any("кальк" in warning.casefold() and "NEEDS_REVIEW" in warning
                       for warning in derived.warnings)
        assert derived.practice_text


def test_too_dense_topics_stay_frozen() -> None:
    dense_24 = _fill_tp_topic("2.4")
    low = dense_24.planned_result.casefold()
    control = dense_24.assessment_method.casefold()
    assert dense_24.lesson_type == "практикум по ориентированию"
    assert "карт" in low and "компас" in low
    assert "азимут" in low
    assert "карт" in control and "азимут" in control
    dense_25 = _fill_tp_topic("2.5")
    step = dense_25.planned_result.casefold()
    assert dense_25.lesson_type == "измерительный практикум"
    assert "шаг" in step
    assert "график" in step or "график" in dense_25.assessment_method.casefold()


def test_selected_clause_keeps_three_orientation_operations() -> None:
    derived = _fill_tp_topic("2.6")
    assert derived.lesson_type == "практикум по ориентированию"
    low = derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert "ориентир" in low
    assert "сходн" in low
    assert "привязк" in low
    assert "ориентир" in control
    assert "сходн" in control or "ситуаци" in control
    assert "привязк" in control
    practice = derived.practice_text.casefold()
    assert "легенд" in practice
    assert "абрис" in practice


def test_snapshot_changes_only_five_loss_weeks() -> None:
    loss_weeks = {
        index + 1
        for index, row in enumerate(CE2_TP1_WEEK_SNAPSHOT)
        if row[0] in LOSS_NUMBERS
    }
    assert loss_weeks == {2, 3, 4, 11, 23}
    unchanged_numbers = {
        row[0] for row in CE2_TP1_WEEK_SNAPSHOT if row[0] not in LOSS_NUMBERS
    }
    assert "2.4" in unchanged_numbers
    assert "2.5" in unchanged_numbers
    assert "2.6" in unchanged_numbers
    # 29 номерных недель в снимке минус 5 LOSS; тема 1.2 живёт в той же неделе, что 1.1.
    assert len(unchanged_numbers) == 24
    topic_12 = _fill_tp_topic("1.2")
    assert topic_12.planned_result.startswith("Характеризует роль туризма")
