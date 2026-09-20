"""Grounded action-catalog builder: Head: item1, item2… from SOURCE only."""

from __future__ import annotations

from itertools import permutations
from types import SimpleNamespace

from calendar_pedagoga.content_engine_v2 import derive_fields_v2
from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.action_builders import ACTION_REGISTRY
from calendar_pedagoga.semantic_atom.control_adapter import compose_control
from calendar_pedagoga.semantic_atom.dispatcher import (
    AMBIGUOUS_FRAME_CANDIDATES,
    SemanticFrameDispatcher,
    RegisteredBuilder,
)
from calendar_pedagoga.semantic_atom.frame_adapter import (
    C5_REGISTRY,
    full_dispatcher,
    project_frames,
)
from calendar_pedagoga.semantic_atom.lexical import (
    TEMPLATE_FUNCTION_WORDS,
    pedagogical_predicates,
    tokenize,
)
from calendar_pedagoga.semantic_atom.models import (
    CandidateConfidence,
    FrameCandidate,
    FrameKind,
    LexicalCheckResult,
    ObjectStatus,
    SourceAtom,
    StructuralEvidence,
)


def _row(title: str = "Тема") -> SimpleNamespace:
    return SimpleNamespace(
        lesson_type="",
        planned_result="",
        assessment_method="",
        topic_title=title,
        week_number=7,
    )


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_full_dispatcher_registers_action_catalog() -> None:
    assert "action_catalog" in full_dispatcher().builder_ids
    assert any(item.builder_id == "action_catalog" for item in ACTION_REGISTRY)


def test_unseen_exercise_game_excursion_and_making() -> None:
    cases = (
        "Упражнения: флумберы, квиллинги.",
        "Игры на поляне: «Зигзаг», «Тихий шепот».",
        "Экскурсионные поездки: озеро Тишь, мыс Ветер.",
        "Изготовление поделок: флумберы, квиллинги.",
    )
    for source in cases:
        projection = project_frames(source)
        frame = projection.frames[0]
        assert frame.status is ObjectStatus.PROVEN, (source, frame.reason)
        assert frame.kind in {FrameKind.ACTION, FrameKind.CREATIVE_PRODUCT}
        folded = frame.projected_result.casefold()
        assert "флумбер" in folded or "квиллинг" in folded or "тишь" in folded or (
            "зигзаг" in folded and "шепот" in folded
        ) or ("шепот" in folded and "зигзаг" in folded)


def test_several_members_and_permutation() -> None:
    members = ("флумберы", "квиллинги", "гомфры")
    texts = []
    for order in permutations(members):
        source = "Упражнения: " + ", ".join(order) + "."
        projection = project_frames(source)
        assert projection.frames[0].status is ObjectStatus.PROVEN, source
        texts.append(
            frozenset(tokenize(projection.frames[0].projected_result.casefold()))
        )
    assert len(set(texts)) == 1


def test_confirmed_member_is_selector_only() -> None:
    source = "Экскурсионные поездки: озеро Тишь, мыс Ветер, каньон Шёпот."
    projection = project_frames(source, _row("мыс Ветер"))
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    folded = frame.projected_result.casefold()
    assert "ветер" in folded
    assert "тишь" not in folded
    assert "шёпот" not in folded and "шепот" not in folded


def test_does_not_assign_whole_list_to_selected_row() -> None:
    source = "Игры на поляне: «Зигзаг», «Тихий шепот», «Каньон шёпот»."
    projection = project_frames(source, _row("«Тихий шепот»"))
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    folded = frame.projected_result.casefold()
    assert "шепот" in folded
    assert "зигзаг" not in folded
    assert "каньон" not in folded


def test_does_not_generate_catalog_from_title() -> None:
    source = "мыс Ветер."
    projection = project_frames(
        source, _row("Экскурсионные поездки: озеро Тишь, мыс Ветер")
    )
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED
    assert projection.candidate_result == ""


def test_knowledge_role_and_unclear_head_are_not_action() -> None:
    sources = (
        "Птицы края: флумберы, квиллинги.",
        "Ответственные: за флумберы, за квиллинги.",
        "Тема занятия: флумберы, квиллинги.",
    )
    for source in sources:
        projection = project_frames(source)
        assert projection.frames[0].kind is not FrameKind.ACTION or (
            projection.frames[0].status is ObjectStatus.UNRESOLVED
        ), source


def test_type_hours_program_week_are_not_evidence() -> None:
    source = "Основные сведения о крае."
    row = derive_fields_v2(
        topic_title="Упражнения: флумберы, квиллинги",
        theory_text="",
        practice_text=source,
        program_content="Игры: «Зигзаг», «Тихий шепот».",
        theory_hours=0,
        practice_hours=4,
    )
    assert row.lesson_type
    wrapped = SimpleNamespace(
        lesson_type=row.lesson_type,
        planned_result=row.planned_result,
        assessment_method=row.assessment_method,
        topic_title="Упражнения: флумберы, квиллинги",
        week_number=12,
        theory_hours=0,
        practice_hours=4,
        program_content="Игры: «Зигзаг», «Тихий шепот».",
    )
    projection = project_frames(source, wrapped)
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED
    assert projection.candidate_result == ""


def test_quotes_and_parentheses_are_kept() -> None:
    source = "Игры на поляне: «Зигзаг», «Тихий шепот» (тихий круг)."
    frame = project_frames(source).frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert "«Зигзаг»" in frame.projected_result
    assert "«Тихий шепот»" in frame.projected_result
    assert "(тихий круг)" in frame.projected_result


def test_lexical_provenance_and_binding() -> None:
    source = "Упражнения: флумберы, квиллинги."
    projection = project_frames(source)
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert frame.atom_id == projection.atoms[0].id
    assert frame.provenance.adapter
    assert any(item.atom_id == frame.atom_id for item in projection.bindings)
    allowed = {token.casefold() for token in tokenize(source)}
    allowed.update(item.casefold() for item in pedagogical_predicates())
    allowed.update(TEMPLATE_FUNCTION_WORDS)
    for token in tokenize(frame.projected_result):
        assert token.casefold() in allowed, token


def test_control_comes_only_from_c9() -> None:
    source = "Изготовление поделок: флумберы, квиллинги."
    projection = project_frames(source)
    assert projection.frames[0].status is ObjectStatus.PROVEN
    control = compose_control(projection)
    assert projection.identity_control == ""
    if control.composed_control:
        assert all(piece.provenance.adapter == "control_c9" for piece in control.pieces)


def test_conflicting_candidates_stay_ambiguous() -> None:
    source = "Упражнения: флумберы, квиллинги."
    atom = project_frames(source).atoms[0]
    conflicting = FrameCandidate(
        builder_id="fake_conflict",
        atom_id=atom.id,
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        proposed_kind=FrameKind.KNOWLEDGE,
        proposed_predicate="называет",
        proposed_object="другой объект",
        proposed_complement="",
        proposed_result="Называет другой объект.",
        proposed_control="",
        structural_evidence=StructuralEvidence(notes=("fake_conflict",)),
        lexical_check=LexicalCheckResult(passed=True),
        confidence=CandidateConfidence.VALID,
    )

    def propose(_atom: SourceAtom) -> FrameCandidate:
        return conflicting

    mixed = SemanticFrameDispatcher(
        (*C5_REGISTRY, *ACTION_REGISTRY, RegisteredBuilder("fake_conflict", propose))
    )
    decision = project_frames(source, dispatcher=mixed)
    assert decision.frames[0].status is ObjectStatus.UNRESOLVED
    assert decision.frames[0].reason == AMBIGUOUS_FRAME_CANDIDATES


def test_single_quoted_game_stays_unresolved() -> None:
    source = "Игры: «Давай поговорим»."
    projection = project_frames(source)
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED
    assert projection.candidate_result == ""
