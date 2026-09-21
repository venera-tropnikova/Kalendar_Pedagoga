"""Grounded process NP becomes ACTION only with a proven governed object."""

from __future__ import annotations

from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.action_builders import ACTION_REGISTRY
from calendar_pedagoga.semantic_atom.atom_adapter import atomize
from calendar_pedagoga.semantic_atom.control_adapter import compose_control
from calendar_pedagoga.semantic_atom.diff_adapter import (
    OldSnapshot,
    _frame_matches_clause,
    classify_snapshots,
    snapshot_shadow,
)
from calendar_pedagoga.semantic_atom.dispatcher import (
    AMBIGUOUS_FRAME_CANDIDATES,
    SemanticFrameDispatcher,
    RegisteredBuilder,
)
from calendar_pedagoga.semantic_atom.frame_adapter import (
    C5_REGISTRY,
    project_frames,
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
from calendar_pedagoga.semantic_atom.passthrough import DiffKind
from calendar_pedagoga.semantic_atom.span_cover import action_cover_text
from tests.test_semantic_atom_action_event_tail import LIVE_EVENT_SOURCE
from tests.test_semantic_atom_semicolon_atoms import DIDACTIC_SOURCE


ACTION_BUILDER_IDS = (
    "finite_produce",
    "explicit_action",
    "nominal_activity",
    "closed_form_activity",
    "unconjugated_practice",
    "grounded_process_np",
    "care_and_repair",
    "paired_shared_object",
    "proven_finite",
    "walk_travel",
    "exercise",
    "action_catalog",
)


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_grounded_process_np_is_registered_after_unconjugated() -> None:
    ids = tuple(item.builder_id for item in ACTION_REGISTRY)
    assert ids == ACTION_BUILDER_IDS
    assert ids.index("grounded_process_np") == ids.index("unconjugated_practice") + 1


def _proven_action(source: str):
    projection = project_frames(source)
    return next(
        item for item in projection.frames if item.status is ObjectStatus.PROVEN
    )


def _binding_for(shadow, frame):
    return next(
        item
        for item in shadow.bindings
        if item.span.id == frame.span.id
        or (item.span.start == frame.span.start and item.span.end == frame.span.end)
    )


def test_process_np_with_governed_object_is_action() -> None:
    source = "Вязка туристских узлов."
    frame = _proven_action(source)
    folded = frame.projected_result.casefold()
    assert frame.kind is FrameKind.ACTION
    assert folded.startswith("выполняет вязку")
    assert "выполняет вязка" not in folded
    assert "вязку" in folded
    assert "туристских узлов" in folded
    assert frame.object
    assert "вязку" in frame.object.casefold()
    assert "туристских узлов" in frame.object.casefold()
    builders = {
        item.builder_id
        for item in project_frames(source).candidates
        if item.is_valid
    }
    assert "grounded_process_np" in builders


def test_vyazka_accusative_is_proven_source_form() -> None:
    source = "Вязка туристских узлов."
    shadow = snapshot_shadow(source)
    folded = shadow.result.casefold()
    assert "выполняет вязку туристских узлов" in folded
    assert "выполняет вязка" not in folded
    assert shadow.lexical_violations == ()
    from calendar_pedagoga.semantic_atom.lexical import _lemmas

    assert "вязка" in _lemmas("вязку")
    assert "вязка" in {token.casefold() for token in source.replace(".", "").split()}


def test_nested_aid_techniques_keep_governed_object_and_locative() -> None:
    source = "Основные приёмы оказания первой доврачебной помощи при ожогах."
    frame = _proven_action(source)
    folded = frame.projected_result.casefold()
    assert frame.kind is FrameKind.ACTION
    assert folded.startswith("оказывает")
    assert "первую доврачебную помощь" in folded
    assert "при ожогах" in folded
    assert "первую доврачебную помощь" in frame.object.casefold()
    assert "ожогах" in (frame.complement or frame.projected_result).casefold()
    nested = (
        "Основные приёмы оказания первой доврачебной помощи при ожогах, обморожениях."
    )
    leftover = _proven_action(nested)
    leftover_folded = leftover.projected_result.casefold()
    assert leftover_folded.startswith("оказывает")
    assert "первую доврачебную помощь" in leftover_folded
    assert "ожогах" in leftover_folded
    assert "обморожениях" not in leftover_folded
    assert "основные приёмы" not in leftover_folded
    shadow = snapshot_shadow(nested)
    proven = next(item for item in shadow.frames if item.id == leftover.id)
    covered = nested[proven.span.start : proven.span.end]
    assert "ожогах" in covered
    assert "обморожениях" not in covered.casefold()


def test_exact_span_binding_and_provenance() -> None:
    source = "Вязка туристских узлов."
    frame = _proven_action(source)
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    atom = next(item for item in shadow.atoms if item.id == proven.atom_id)
    binding = _binding_for(shadow, proven)
    covered = source[proven.span.start : proven.span.end]
    assert "Вязка" in covered
    assert "туристских узлов" in covered
    assert proven.span.start >= atom.span.start
    assert proven.span.end <= atom.span.end
    assert proven.source_fingerprint == atom.source_fingerprint
    assert binding.source_fingerprint == atom.source_fingerprint
    assert (binding.span.start, binding.span.end) == (proven.span.start, proven.span.end)
    assert "grounded_process_np" in proven.provenance.note
    assert action_cover_text(atom.text, proven)
    assert _frame_matches_clause("Вязка туристских узлов", proven, shadow)
    assert shadow.lexical_violations == ()
    assert "вязку" in proven.projected_result.casefold()
    assert "выполняет вязка" not in proven.projected_result.casefold()
    control = compose_control(project_frames(source))
    if control.composed_control:
        assert all(piece.provenance.adapter == "control_c9" for piece in control.pieces)


def test_bare_ka_without_object_is_not_action() -> None:
    for source in ("Вязка.", "Упаковка."):
        projection = project_frames(source)
        assert projection.frames
        assert all(
            frame.kind is not FrameKind.ACTION
            or frame.status is ObjectStatus.UNRESOLVED
            for frame in projection.frames
        ), source
        folded = snapshot_shadow(source).result.casefold()
        assert "выполняет" not in folded
        assert not folded.startswith("оказывает")
        process = [
            item
            for item in projection.candidates
            if item.builder_id == "grounded_process_np" and item.is_valid
        ]
        assert not process, source


def test_theory_knowledge_event_role_and_metadata_stay_out() -> None:
    cases = (
        ("Основные сведения о крае.", None),
        ("Правила поведения флумбера.", FrameKind.KNOWLEDGE),
        ("Основные виды туристских узлов.", FrameKind.KNOWLEDGE),
        ("Понятия: ритм, темп, динамика.", FrameKind.KNOWLEDGE),
        ("Туриада, «Зимние забавы», «Туристскими тропами».", None),
        ("Ответственные: за флумберы, за квиллинги.", None),
        ("Тема занятия: флумберы, квиллинги.", None),
        ("Города Башкортостана.", None),
    )
    for source, kind in cases:
        projection = project_frames(source)
        process = [
            item
            for item in projection.candidates
            if item.builder_id == "grounded_process_np" and item.is_valid
        ]
        assert not process, source
        for frame in projection.frames:
            if frame.status is ObjectStatus.PROVEN:
                assert frame.kind is not FrameKind.ACTION, source
                if kind is not None:
                    assert frame.kind is kind, source
            else:
                assert frame.kind is not FrameKind.ACTION or (
                    frame.status is ObjectStatus.UNRESOLVED
                ), source


def test_conflicting_candidates_stay_ambiguous() -> None:
    source = "Вязка туристских узлов."
    atom = project_frames(source).atoms[0]
    conflicting = FrameCandidate(
        builder_id="fake_conflict",
        atom_id=atom.id,
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        proposed_kind=FrameKind.KNOWLEDGE,
        proposed_predicate="характеризует",
        proposed_object="другой объект",
        proposed_complement="",
        proposed_result="Характеризует другой объект.",
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


def test_neighbor_conjunct_is_not_covered() -> None:
    source = "Вязка туристских узлов, способы поворота."
    frame = _proven_action(source)
    folded = frame.projected_result.casefold()
    assert folded.startswith("выполняет вязку")
    assert "туристских узлов" in folded
    assert "выполняет вязка" not in folded
    assert "поворота" not in folded
    assert "способы" not in folded
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    atom = next(item for item in shadow.atoms if item.id == proven.atom_id)
    covered = source[proven.span.start : proven.span.end]
    assert "Вязка" in covered
    assert "туристских узлов" in covered
    assert "способы" not in covered.casefold()
    assert "поворота" not in covered.casefold()
    assert not _frame_matches_clause("способы поворота", proven, shadow)
    assert _frame_matches_clause("Вязка туристских узлов", proven, shadow)
    assert (proven.span.start, proven.span.end) != (atom.span.start, atom.span.end)
    assert action_cover_text(atom.text, proven) != atom.text
    old = OldSnapshot(
        result="Выполняет вязка туристских узлов.",
        control=shadow.control,
        coverage=(("Вязка туристских узлов, способы поворота", "UNCOVERED"),),
        source=source,
    )
    diff = classify_snapshots(old, shadow)
    assert diff.kind is not DiffKind.NEW_INVENTS


def test_already_eligible_process_stays_unambiguous() -> None:
    source = "Установка палатки."
    projection = project_frames(source)
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert frame.kind is FrameKind.ACTION
    assert frame.projected_result.casefold().startswith("выполняет")
    process = [
        item
        for item in projection.candidates
        if item.builder_id == "grounded_process_np" and item.is_valid
    ]
    assert not process


def test_ambiguous_process_case_stays_unresolved() -> None:
    for source in (
        "Вязки туристских узлов.",
        "Города флумбера.",
        "Города Башкортостана.",
    ):
        projection = project_frames(source)
        process = [
            item
            for item in projection.candidates
            if item.builder_id == "grounded_process_np" and item.is_valid
        ]
        assert not process, source
        folded = snapshot_shadow(source).result.casefold()
        assert "выполняет вязка" not in folded, source
        assert "выполняет вязку" not in folded, source
        assert all(
            frame.kind is not FrameKind.ACTION or frame.status is ObjectStatus.UNRESOLVED
            for frame in projection.frames
        ), source


def test_named_game_regression_keeps_quoted_title() -> None:
    source = "Игра-викторина «Вода»."
    frame = _proven_action(source)
    folded = frame.projected_result.casefold()
    assert folded.startswith("участвует")
    assert "«вода»" in folded
    process = [
        item
        for item in project_frames(source).candidates
        if item.builder_id == "grounded_process_np" and item.is_valid
    ]
    assert not process


def test_list_conjunct_regression_keeps_proven_members() -> None:
    source = "Викторины, кроссворды, ребусы."
    frame = _proven_action(source)
    folded = frame.projected_result.casefold()
    assert folded.startswith("участвует")
    assert "викторин" in folded
    assert "кроссворд" in folded
    assert "ребус" in folded
    leftover = "Спуск с горы, способы поворота."
    action = next(
        item
        for item in project_frames(leftover).frames
        if item.kind is FrameKind.ACTION
    )
    assert "поворота" not in action.projected_result.casefold()


def test_catalog_regression_keeps_quoted_members() -> None:
    source = "Игры на поляне: «Зигзаг», «Тихий шепот»."
    frame = _proven_action(source)
    assert "«Зигзаг»" in frame.projected_result
    assert "«Тихий шепот»" in frame.projected_result
    process = [
        item
        for item in project_frames(source).candidates
        if item.builder_id == "grounded_process_np" and item.is_valid
    ]
    assert not process


def test_event_tail_regression_keeps_parent_predicate() -> None:
    shadow = snapshot_shadow(LIVE_EVENT_SOURCE)
    folded = shadow.result.casefold()
    assert "туриада" in folded
    assert "зимние забавы" in folded
    assert "туристскими тропами" in folded
    assert any(
        folded.startswith(item) or f". {item}" in folded
        for item in ("подготавливает", "участвует")
    )


def test_semicolon_regression_keeps_independent_atoms() -> None:
    source = "Выполняет упражнение; участие в походе."
    result = atomize(source)
    assert [atom.text for atom in result.atoms] == [
        "Выполняет упражнение",
        "участие в походе.",
    ]
    projection = project_frames(source)
    left, right = projection.frames
    assert left.status is ObjectStatus.PROVEN
    assert right.status is ObjectStatus.PROVEN
    assert "походе" not in left.projected_result.casefold()
    drawing = atomize(DIDACTIC_SOURCE)
    assert any(atom.text.startswith("Рисунки") for atom in drawing.atoms)
    process = [
        item
        for item in projection.candidates
        if item.builder_id == "grounded_process_np" and item.is_valid
    ]
    assert not process
