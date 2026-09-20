"""Grounded knowledge NP and knowledge-catalog builder. No new predicates."""

from __future__ import annotations

from types import SimpleNamespace

from calendar_pedagoga.content_engine_v2 import derive_fields_v2
from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.action_builders import (
    AMBIGUOUS_CATALOG_ASSIGNMENT,
    OPEN_TAIL_UNRESOLVED,
)
from calendar_pedagoga.semantic_atom.control_adapter import compose_control
from calendar_pedagoga.semantic_atom.diff_adapter import (
    _frame_matches_clause,
    classify_snapshots,
    snapshot_shadow,
    OldSnapshot,
)
from calendar_pedagoga.semantic_atom.dispatcher import (
    AMBIGUOUS_FRAME_CANDIDATES,
    SemanticFrameDispatcher,
    RegisteredBuilder,
)
from calendar_pedagoga.semantic_atom.passthrough import DiffKind
from calendar_pedagoga.semantic_atom.frame_adapter import (
    C5_REGISTRY,
    full_dispatcher,
    project_frames,
)
from calendar_pedagoga.semantic_atom.knowledge_builders import KNOWLEDGE_REGISTRY
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


def _catalog_notes(projection, builder_id: str = "knowledge_catalog") -> set[str]:
    return {
        note
        for item in projection.candidates
        if item.builder_id == builder_id
        for note in item.structural_evidence.notes
    }


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_full_dispatcher_registers_knowledge_noun_phrase() -> None:
    assert "knowledge_noun_phrase" in full_dispatcher().builder_ids
    assert any(item.builder_id == "knowledge_noun_phrase" for item in KNOWLEDGE_REGISTRY)


def test_unseen_bare_knowledge_np() -> None:
    source = "Правила поведения флумбера на квиллинге."
    projection = project_frames(source)
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    assert frame.kind is FrameKind.KNOWLEDGE
    folded = frame.projected_result.casefold()
    assert folded.startswith("характеризует")
    assert "флумбер" in folded
    assert "квиллинг" in folded


def test_closed_knowledge_catalog() -> None:
    source = "Птицы края: флумберы, квиллинги, гомфры."
    projection = project_frames(source)
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    assert frame.kind is FrameKind.KNOWLEDGE
    folded = frame.projected_result.casefold()
    assert folded.startswith("называет")
    assert "флумбер" in folded and "квиллинг" in folded and "гомфр" in folded
    assert frame.kind is not FrameKind.ACTION


def test_open_knowledge_catalog() -> None:
    source = "Птицы края: флумберы, квиллинги и другие."
    projection = project_frames(source)
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    assert frame.kind is FrameKind.KNOWLEDGE
    folded = frame.projected_result.casefold()
    assert folded.startswith("называет")
    assert "флумбер" in folded and "квиллинг" in folded
    assert "другие" not in folded
    assert OPEN_TAIL_UNRESOLVED in _catalog_notes(projection)


def test_morphology_and_lexical_trace() -> None:
    source = "Правила поведения флумбера."
    projection = project_frames(source)
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    assert frame.atom_id == projection.atoms[0].id
    assert frame.provenance.adapter
    assert any(item.atom_id == frame.atom_id for item in projection.bindings)
    allowed = {token.casefold() for token in tokenize(source)}
    allowed.update(item.casefold() for item in pedagogical_predicates())
    allowed.update(TEMPLATE_FUNCTION_WORDS)
    for token in tokenize(frame.projected_result):
        assert token.casefold() in allowed, token


def test_action_game_event_and_role_are_rejected() -> None:
    sources = (
        "Упражнения: флумберы, квиллинги.",
        "Игры на поляне: «Зигзаг», «Тихий шепот».",
        "Праздник: «День флумбера», «Ночь квиллинга».",
        "Ответственные: за флумберы, за квиллинги.",
    )
    for source in sources:
        projection = project_frames(source)
        frame = projection.frames[0]
        folded = frame.projected_result.casefold()
        assert "называет" not in folded, source
        assert "характеризует" not in folded, source
        knowledge = [
            item
            for item in projection.candidates
            if item.builder_id.startswith("knowledge_") and item.is_valid
        ]
        assert not knowledge, source


def test_type_hours_program_week_are_not_evidence() -> None:
    source = "Флумбер."
    row = derive_fields_v2(
        topic_title="Теория",
        theory_text=source,
        practice_text="",
        program_content="Правила поведения флумбера.",
        theory_hours=2,
        practice_hours=0,
    )
    wrapped = SimpleNamespace(
        lesson_type=row.lesson_type,
        planned_result=row.planned_result,
        assessment_method=row.assessment_method,
        topic_title="Правила поведения флумбера.",
        week_number=12,
        theory_hours=2,
        practice_hours=0,
        program_content="Правила поведения флумбера.",
    )
    assert wrapped.lesson_type
    projection = project_frames(source, wrapped)
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED
    assert projection.candidate_result == ""


def test_multi_row_catalog_needs_exact_member() -> None:
    source = "Птицы края: флумберы, квиллинги, гомфры."
    blocked = project_frames(source, _row("Птицы края"))
    assert blocked.frames[0].status is ObjectStatus.UNRESOLVED
    assert blocked.frames[0].reason == AMBIGUOUS_CATALOG_ASSIGNMENT
    assert "флумбер" not in blocked.candidate_result.casefold()

    selected = project_frames(source, _row("квиллинги"))
    frame = selected.frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    folded = frame.projected_result.casefold()
    assert "квиллинг" in folded
    assert "флумбер" not in folded
    assert "гомфр" not in folded


def test_mixed_action_and_knowledge() -> None:
    source = "Рисуют флумберы у ручья. Правила поведения квиллинга."
    projection = project_frames(source)
    assert [frame.status for frame in projection.frames] == [
        ObjectStatus.PROVEN,
        ObjectStatus.PROVEN,
    ]
    kinds = {frame.kind for frame in projection.frames}
    assert FrameKind.KNOWLEDGE in kinds
    assert kinds & {FrameKind.ACTION, FrameKind.CREATIVE_PRODUCT}


def test_control_comes_only_from_c9() -> None:
    source = "Правила поведения флумбера."
    projection = project_frames(source)
    assert projection.frames[0].status is ObjectStatus.PROVEN
    control = compose_control(projection)
    assert projection.identity_control == ""
    if control.composed_control:
        assert all(piece.provenance.adapter == "control_c9" for piece in control.pieces)


def test_conflicting_candidates_stay_ambiguous() -> None:
    source = "Правила поведения флумбера."
    atom = project_frames(source).atoms[0]
    conflicting = FrameCandidate(
        builder_id="fake_conflict",
        atom_id=atom.id,
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        proposed_kind=FrameKind.ACTION,
        proposed_predicate="рисует",
        proposed_object="другой объект",
        proposed_complement="",
        proposed_result="Рисует другой объект.",
        proposed_control="",
        structural_evidence=StructuralEvidence(notes=("fake_conflict",)),
        lexical_check=LexicalCheckResult(passed=True),
        confidence=CandidateConfidence.VALID,
    )

    def propose(_atom: SourceAtom) -> FrameCandidate:
        return conflicting

    mixed = SemanticFrameDispatcher(
        (*C5_REGISTRY, *KNOWLEDGE_REGISTRY, RegisteredBuilder("fake_conflict", propose))
    )
    decision = project_frames(source, dispatcher=mixed)
    assert decision.frames[0].status is ObjectStatus.UNRESOLVED
    assert decision.frames[0].reason == AMBIGUOUS_FRAME_CANDIDATES


def test_finite_catalog_head_is_rejected() -> None:
    source = "В лесах нашего края живут звери: растительноядные, хищные, всеядные."
    projection = project_frames(source)
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED
    assert "называет" not in projection.candidate_result.casefold()
    knowledge = [
        item
        for item in projection.candidates
        if item.builder_id.startswith("knowledge_") and item.is_valid
    ]
    assert not knowledge


def test_partial_object_does_not_cover_neighbors() -> None:
    source = (
        "Состав аптечки, упаковка, хранение, транспортировка, "
        "назначение элементарных лекарственных препаратов."
    )
    projection = project_frames(source)
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    folded = frame.projected_result.casefold()
    assert "назначение" in folded
    assert "упаковка" not in folded
    assert "состав" not in folded
    shadow = snapshot_shadow(source)
    proven = shadow.frames[0]
    assert not _frame_matches_clause("упаковка", proven, shadow)
    assert not _frame_matches_clause("Состав аптечки", proven, shadow)
    assert _frame_matches_clause(
        "назначение элементарных лекарственных препаратов", proven, shadow
    )
    covered = source[proven.span.start : proven.span.end]
    assert "назначение" in covered.casefold()
    assert "упаковка" not in covered.casefold()


def test_catalog_head_uses_proven_accusative() -> None:
    source = "Техника движения в походе: темп, режим."
    frame = project_frames(source).frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    folded = frame.projected_result.casefold()
    assert folded.startswith("называет")
    assert "технику" in folded
    assert "называет техника " not in folded


def test_partial_frame_uses_exact_span_and_binding() -> None:
    source = (
        "Состав аптечки, упаковка, хранение, транспортировка, "
        "назначение элементарных лекарственных препаратов."
    )
    projection = project_frames(source)
    atom = projection.atoms[0]
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    assert frame.atom_id == atom.id
    assert frame.span.start >= atom.span.start
    assert frame.span.end <= atom.span.end
    assert (frame.span.start, frame.span.end) != (atom.span.start, atom.span.end)
    covered = source[frame.span.start : frame.span.end]
    assert _ce2_part_is_назначение(covered)
    bound = [
        item
        for item in projection.bindings
        if item.frame_id == frame.id and item.atom_id == frame.atom_id
    ]
    assert bound
    assert bound[0].span.start == frame.span.start
    assert bound[0].span.end == frame.span.end


def _ce2_part_is_назначение(covered: str) -> bool:
    folded = covered.casefold()
    return "назначение" in folded and "упаковка" not in folded and "состав" not in folded


def test_knowledge_builder_does_not_invent() -> None:
    source = "Техника движения в походе: темп, режим."
    shadow = snapshot_shadow(source)
    assert not shadow.lexical_violations
    old = OldSnapshot(
        result=shadow.result,
        control=shadow.control,
        coverage=((source.rstrip("."), "COVERED"),),
        source=source,
    )
    diff = classify_snapshots(old, shadow)
    assert diff.kind is not DiffKind.NEW_INVENTS
