"""W=1 LOSS: обязательные действия в RESULT/CONTROL, без каталога всех C."""

from calendar_pedagoga.content_engine_v2 import fill_from_source
from test_ce2_grounded_triad import CE2_TP1_WEEK_SNAPSHOT
from test_content_engine_v2 import _fill_tp_topic


LOSS_NUMBERS = ("1.3", "1.4", "1.5", "2.1", "4.1")
TOO_DENSE_FREEZE = {
    "2.4": (
        "Ориентирует карту по компасу.",
        "практическое задание по ориентированию карты по компасу",
        "практикум по ориентированию",
    ),
    "2.5": (
        "Измеряет свой средний шаг (пару шагов), строит графики перевода пар шагов в метры для разных условий ходьбы.",
        "практическое задание по измерению шага; проверка графика перевода пар шагов в метры для разных условий ходьбы",
        "измерительный практикум",
    ),
}


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
    assert derived.planned_result == (
        "Определяет масштаб и измеряет расстояние на карте."
    )
    assert "кальк" not in derived.planned_result.casefold()
    assert "кальк" in derived.practice_text.casefold()


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
    assert "маршрут" not in low


def test_method_catalog_neighbor_is_not_added() -> None:
    derived = fill_from_source(
        topic_title="Ориентирование",
        program_content=(
            "Практические занятия. Ориентирование карты по компасу. "
            "Определение азимута на ориентир. Движение по азимуту."
        ),
        theory_hours=0,
        practice_hours=1,
    )
    low = derived.planned_result.casefold()
    assert "ориентирует карту" in low
    assert "азимут" not in low


def test_loss_topics_keep_required_actions_without_catalog() -> None:
    checks = {
        "1.3": (("укладывает", "подгоняет", "ухаживает", "ремонтирует"), ("виды ремонта",)),
        "1.4": (("места", "лагерь", "костёр"), ("виды костр", "нодья", "шалаш")),
        "1.5": (("план подготовки", "план-график", "снаряжен"), ("маршрут",)),
        "2.1": (("масштаб", "расстояни"), ("кальк",)),
        "4.1": (("гигиен", "одежд", "обув"), ("гимнастик",)),
    }
    for number, (need, forbid) in checks.items():
        derived = _fill_tp_topic(number)
        low = derived.planned_result.casefold()
        for token in need:
            assert token in low, f"{number}: missing {token!r} in {derived.planned_result!r}"
        for token in forbid:
            assert token not in low, f"{number}: unexpected {token!r} in {derived.planned_result!r}"
        assert derived.practice_text


def test_too_dense_topics_stay_frozen() -> None:
    for number, (result, control, lesson_type) in TOO_DENSE_FREEZE.items():
        derived = _fill_tp_topic(number)
        assert derived.planned_result == result
        assert derived.assessment_method == control
        assert derived.lesson_type == lesson_type


def test_selected_clause_keeps_three_orientation_operations() -> None:
    derived = _fill_tp_topic("2.6")
    assert derived.planned_result == (
        "Отбирает основные контрольные ориентиры на карте по заданному маршруту, "
        "находит сходные (параллельные) ситуации и определяет способы привязки."
    )
    assert derived.assessment_method == (
        "практическое задание по отбору основных контрольных ориентиров "
        "на карте по заданному маршруту, отысканию сходных ситуаций "
        "и определению способов привязки"
    )
    assert derived.lesson_type == "практикум по ориентированию"
    low = derived.planned_result.casefold()
    assert "легенд" not in low
    assert "абрис" not in low
    assert "мини" not in low
    assert "график" not in low
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
