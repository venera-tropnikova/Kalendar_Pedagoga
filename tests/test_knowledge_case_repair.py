from calendar_pedagoga.content_engine_v2 import _result_grammar_issue
from calendar_pedagoga.morphology import (
    is_proven_acc,
    repair_rejected_knowledge_sentence,
)


def test_inanimate_plural_keeps_surface_and_is_proven_acc() -> None:
    sentence = "Характеризует памятники прославленным людям."
    assert _result_grammar_issue(sentence) == "unproven_knowledge_object_case"
    repaired = repair_rejected_knowledge_sentence(sentence)
    assert repaired == sentence
    assert is_proven_acc("памятники")


def test_animate_plural_inflects_to_accusative() -> None:
    repaired = repair_rejected_knowledge_sentence("Характеризует птицы.")
    assert repaired == "Характеризует птиц."
    assert is_proven_acc("птиц")


def test_unparsable_leftover_is_fail_closed() -> None:
    assert repair_rejected_knowledge_sentence("Характеризует деревью и кустарники.") is None


def test_topic_fallback_is_refused() -> None:
    assert repair_rejected_knowledge_sentence("Характеризует материал по теме «Школа».") is None


def test_already_proven_knowledge_sentence_is_not_the_repair_target() -> None:
    sentence = "Характеризует строение организма."
    assert not _result_grammar_issue(sentence)
