"""Independent semantic acceptance of production 40684af.

Specification: ccbf516:docs/SEMANTIC_CONTRACT.md.
All sources, anchors and admissible alternatives below are authored manually.
No old snapshots, generator-produced expected values, xfail or monkeypatches.
TestEngineAcceptance measures the engine; TestAnchorCalibration tests the
test oracle with hand-written positive and negative examples, NOT the engine.
These finite-vocabulary anchors cover these fixtures, not arbitrary Russian.
"""
import re

import pytest

from calendar_pedagoga.content_engine_v2 import (
    ActionFrame,
    control_from_frame,
    derive_fields_v2,
)
from calendar_pedagoga.content_generation import (
    _assign_section_blocks,
    _section_blocks_from_item,
)
from calendar_pedagoga.program_parsing import ProgramContentItem


def normalized(text):
    return re.sub(r"\s+", " ", text.casefold().replace("ё", "е")).strip()


def require_anchors(text, anchors, rule):
    """Each regex is a manually authored semantic relation, not a snapshot."""
    missing = [label for label, pattern in anchors
               if not re.search(pattern, normalized(text))]
    assert not missing, f"{rule}: missing {missing}; actual={text!r}"


# Action-object relations: nearby unrelated words cannot satisfy these.
WARMUP = ("perform warmup", r"(?:выполня\w*|выполнени\w*)\s+размин\w*")
PULSE = ("measure pulse", r"(?:измеря\w*|измерени\w*)\s+(?:частот\w*\s+)?пульс\w*")
PAIR = (WARMUP, PULSE)
ORIENT = ("orient map by compass",
          r"ориентир\w*\s+карт\w*\s+по\s+компас\w*")
AZIMUTH = ("determine azimuth to landmark",
           r"определ\w*\s+азимут\w*\s+на\s+ориентир\w*")
MOVE = ("move by azimuth", r"(?:движ\w*|двига\w*)\s+по\s+азимут\w*")
DENSE = (ORIENT, AZIMUTH, MOVE)
C38_RESULT = (
    "Отбирает основные контрольные ориентиры на карте по заданному маршруту, "
    "находит сходные ситуации и определяет способы привязки."
)
C38_SOURCE = (
    "Отбор основных контрольных ориентиров на карте по заданному маршруту. "
    "Нахождение сходных ситуаций. Определение способов привязки."
)
C38_ANCHORS = (
    ("landmark selection", r"(?:отбор\w*|отбира\w*|выбор\w*)\s+"
     r"(?:основн\w*\s+)?(?:контрольн\w*\s+)?ориентир\w*"),
    ("finding similar situations", r"(?:нахождени\w*|наход\w*|отыскани\w*|поиск\w*)"
     r"\s+сходн\w*\s+ситуаци\w*"),
    ("determining ways of reference", r"определ\w*\s+способ\w*\s+привязк\w*"),
)
FEEDER = (
    ("make feeder", r"(?:изготов\w*|создани\w*)\s+"
     r"(?:(?:и\s+)?(?:развеш\w*|развес\w*)\s+)?кормуш\w*"),
    ("hang feeder", r"(?:развеш\w*|развес\w*)\s+"
     r"(?:(?:и\s+)?(?:изготов\w*|создани\w*)\s+)?кормуш\w*"),
)
LIST_ANCHORS = (
    ("diagonal step", r"диагональн\w*\s+шаг\w*"),
    ("roll", r"\bнакат\w*"),
    ("twist", r"\bскрутк\w*"),
)
SIX_NAMES = ("Альфа", "Бета", "Гамма", "Дельта", "Эпсилон", "Дзета")
SIX_ANCHORS = tuple((name, rf"\b{name.casefold()}\b") for name in SIX_NAMES)


def derive(source):
    return derive_fields_v2(
        topic_title="Учебная тема",
        theory_text="",
        practice_text=source,
        program_content="Практика. " + source,
        theory_hours=0,
        practice_hours=2,
    )


def control(source, result):
    return control_from_frame(
        ActionFrame(source, "", "", ""),
        planned_result=result,
        lesson_type="практикум",
        theory_hours=0,
        practice_hours=2,
    )


def require_choice(text, *, allow_reference=False):
    value = normalized(text)
    explicit = re.search(r"открытк\w*\s+или\s+маск\w*", value)
    reverse = re.search(r"маск\w*\s+или\s+открытк\w*", value)
    reference = allow_reference and re.search(
        r"(?:выбранн\w*\s+издели\w*|издели\w*\s+по\s+выбору)", value
    )
    assert explicit or reverse or reference, (
        f"R13/R23 C22: alternative lost; actual={text!r}"
    )
    assert not re.search(r"открытк\w*\s+и\s+маск\w*|маск\w*\s+и\s+открытк\w*", value), (
        f"R13 C22: OR became AND; actual={text!r}"
    )


def require_prohibition(text):
    value = normalized(text)
    # Finite positive performance followed by 'запрещено' is not grammatical
    # preservation of a prohibition (the actual observed corruption).
    positive = re.search(r"\b(?:выполняет|выполняют)\b", value)
    negative = re.search(r"\bне\s+(?:выполняет|выполняют)\b", value)
    assert not positive or negative, f"R13 C23: positive action; actual={text!r}"
    assert re.search(r"\bне\s+выполн\w*|запрещ\w*|нельзя\s+выполн\w*", value), (
        f"R13 C23: prohibition missing; actual={text!r}"
    )


def require_observer(text):
    require_anchors(text, (
        ("child observes teacher demonstration",
         r"(?:наблюда\w*|наблюдени\w*)\s+за\s+"
         r"(?:демонстраци\w*|показ\w*)\s+педагог\w*"),
    ), "R13 C24")
    assert not re.search(r"\b(?:ребенок\s+)?демонстрирует\b", normalized(text)), (
        f"R13 C24: demonstration attributed to pupil; actual={text!r}"
    )


def require_review_without_action(result):
    value = normalized(result.planned_result)
    assert not re.search(
        r"\b(?:выполняет|изучает|отрабатывает|характеризует|изготавливает|"
        r"демонстрирует|осваивает|называет)\b", value
    ), f"R13 BARE LIST: invented action; actual={result.planned_result!r}"
    status = str(getattr(result, "status", ""))
    signals = " ".join((status, result.planned_result, *result.warnings))
    assert re.search(r"needs_review|требуется\s+уточнение|недостаточно\s+данных",
                     normalized(signals)), (
        f"R13 BARE LIST: no explicit review state; actual={signals!r}"
    )


class TestEngineAcceptance:
    def test_c11_week_keeps_two_actions(self):
        item = ProgramContentItem(
            None, "Подготовка", "Практика\nВыполнение разминки. Измерение пульса.",
            "Раздел",
        )
        assigned = _assign_section_blocks(
            _section_blocks_from_item(item), "practice", appearances=1
        )
        require_anchors(" ".join(x.content for x in assigned), PAIR, "R07 C11 WEEK")

    @pytest.mark.parametrize("field", ["planned_result", "assessment_method"])
    def test_c11_triad_keeps_two_actions(self, field):
        result = derive("Выполнение разминки. Измерение пульса.")
        require_anchors(getattr(result, field), PAIR, f"R21/R23 C11 {field}")

    def test_c12_all_six_subtopics_assigned(self):
        # Equal count per week is NOT an expectation: only complete ordered
        # assignment; opaque names cannot acquire invented pedagogical meaning.
        blocks = tuple(
            block
            for name in SIX_NAMES
            for block in _section_blocks_from_item(ProgramContentItem(
                None, name, f"Практика\nВыполнение упражнения «{name}».", "Раздел"
            ))
        )
        assigned = _assign_section_blocks(blocks, "practice", appearances=2)
        assert len(assigned) == 2, "R07 C12: weekly capacity changed"
        text = " ".join(x.content for x in assigned)
        require_anchors(text, SIX_ANCHORS, "R07/R08 C12 WEEK")
        positions = [normalized(text).index(name.casefold()) for name in SIX_NAMES]
        assert positions == sorted(positions), "R08 C12: source order changed"

    @pytest.mark.parametrize("members", [
        "диагональный шаг, накат, скрутка",
        "скрутка, накат, диагональный шаг",
    ], ids=["source_order", "permuted_order"])
    def test_c21_all_mandatory_members(self, members):
        result = derive("Изучение и отработка основных технических приемов: " + members)
        for field in ("planned_result", "assessment_method"):
            require_anchors(getattr(result, field), LIST_ANCHORS, f"R12 C21 {field}")

    @pytest.mark.parametrize("field", ["planned_result", "assessment_method"])
    def test_c22_choice_preserved(self, field):
        result = derive("Изготовление открытки или маски по выбору.")
        require_choice(getattr(result, field), allow_reference=field == "assessment_method")

    @pytest.mark.parametrize("source", [
        "Выполнение упражнения без страховки запрещено.",
        "Не выполнять упражнение без страховки.",
    ], ids=["prohibition", "negative_imperative"])
    def test_c23_prohibition_not_positive(self, source):
        result = derive(source)
        # A review/abstention with no invented action is admissible, as is an
        # explicit preserved prohibition. Generic positive action is not.
        review = re.search(r"needs_review|требуется\s+уточнение",
                           normalized(" ".join(result.warnings)))
        if review and not result.planned_result.strip():
            return
        require_prohibition(result.planned_result)

    def test_c24_teacher_action_not_pupil_action(self):
        result = derive("Педагог демонстрирует упражнение; ребёнок наблюдает.")
        require_observer(result.planned_result)

    @pytest.mark.parametrize("field", ["planned_result", "assessment_method"])
    def test_c35_dense_week_keeps_operations(self, field):
        result = derive(
            "Ориентирование карты по компасу. "
            "Определение азимута на ориентир. Движение по азимуту."
        )
        require_anchors(getattr(result, field), DENSE, f"R21/R23 C35 {field}")

    def test_c38_control_covers_three_operations(self):
        actual = control(C38_SOURCE, C38_RESULT)
        require_anchors(actual, C38_ANCHORS, "R23 C38 CONTROL")

    def test_c38_shared_object_does_not_hide_second_operation(self):
        actual = control(
            "Изготовление кормушки. Развешивание кормушки.",
            "Изготавливает кормушку и развешивает кормушку.",
        )
        require_anchors(actual, FEEDER, "R23 C38 shared object")

    @pytest.mark.parametrize("source", [
        "Диагональный шаг, накат, скрутка.",
        "Перечень: диагональный шаг, накат, скрутка.",
    ], ids=["bare", "labelled"])
    def test_bare_list_no_invented_predicate(self, source):
        require_review_without_action(derive(source))


class TestAnchorCalibration:
    """Mutation checks of the oracle: a PASS here is not an engine PASS."""

    @pytest.mark.parametrize("kind,bad", [
        ("negation", "Выполняет упражнение без страховки."),
        ("choice", "Изготавливает открытку и маску."),
        ("roles", "Ребёнок демонстрирует; педагог наблюдает."),
        ("catalogue", "Отрабатывает диагональный шаг и накат."),
        ("unassigned", "Выполнение разминки."),
        ("wrong_object", "Выполняет пульс и измеряет разминку."),
        ("general_control", "Отбор контрольных ориентиров; поиск сходных ситуаций; проверка на местности."),
        ("bare_list", "Изучает диагональный шаг, накат, скрутку."),
        ("shared_object", "Проверка изготовления кормушки."),
    ], ids=["deleted_not", "or_to_and", "swapped_roles", "required_object_lost",
            "action_disappeared", "words_with_wrong_relations",
            "general_control_missing_operation", "bare_list_invented_action",
            "same_object_missing_operation"])
    def test_rejects_handwritten_semantic_mutation(self, kind, bad):
        with pytest.raises(AssertionError):
            if kind == "negation":
                require_prohibition(bad)
            elif kind == "choice":
                require_choice(bad)
            elif kind == "roles":
                require_observer(bad)
            elif kind == "catalogue":
                require_anchors(bad, LIST_ANCHORS, "R12 C21")
            elif kind in {"unassigned", "wrong_object"}:
                require_anchors(bad, PAIR, "R21 C11")
            elif kind == "general_control":
                require_anchors(bad, C38_ANCHORS, "R23 C38")
            elif kind == "shared_object":
                require_anchors(bad, FEEDER, "R23 C38")
            else:
                # Hand-authored fake output, never produced by the engine.
                from types import SimpleNamespace
                require_review_without_action(SimpleNamespace(
                    planned_result=bad, warnings=(), status=""
                ))

    @pytest.mark.parametrize("good", [
        "Выполняет разминку; измеряет пульс.",
        "Проверка измерения пульса и выполнения разминки.",
    ])
    def test_accepts_equivalent_relations_not_snapshots(self, good):
        require_anchors(good, PAIR, "C11")

    def test_accepts_reference_for_choice(self):
        require_choice("Проверка выбранного изделия.", allow_reference=True)

    @pytest.mark.parametrize("good", [
        "Проверка изготовления и развешивания кормушки.",
        "Проверка развешивания и изготовления кормушки.",
    ])
    def test_accepts_shared_object_with_both_operations(self, good):
        require_anchors(good, FEEDER, "R23 C38")

    def test_accepts_negative_and_teacher_observer(self):
        require_prohibition("Не выполняет упражнение без страховки.")
        require_observer("Наблюдает за показом педагога.")

    def test_accepts_one_control_with_three_explicit_areas(self):
        require_anchors(
            "Практическая проверка отбора контрольных ориентиров, "
            "поиска сходных ситуаций и определения способов привязки.",
            C38_ANCHORS, "R23 C38",
        )
