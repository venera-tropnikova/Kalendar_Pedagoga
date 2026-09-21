"""PROVEN ACTION coverage attests a conjugated SOURCE head, not raw atom.text."""

from __future__ import annotations

from dataclasses import replace

from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.action_builders import ACTION_REGISTRY
from calendar_pedagoga.semantic_atom.diff_adapter import (
    OldSnapshot,
    ShadowSnapshot,
    _frame_matches_clause,
    classify_snapshots,
    snapshot_shadow,
)
from calendar_pedagoga.semantic_atom.models import (
    FrameKind,
    ObjectStatus,
    Provenance,
    SemanticFrame,
    SourceAtom,
    SourceSpan,
)
from calendar_pedagoga.semantic_atom.passthrough import DiffKind
from tests.test_semantic_atom_c10_audit import _atom, _binding, _frame


GAMES_SOURCE = (
    "Тренировка постановки ног при помощи игр на скалодроме: "
    "«Земля, вода, лава», «Выше ноги от земли»."
)
ENDURANCE_SOURCE = "Тренировка на выносливость."


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_action_catalog_registry_unchanged() -> None:
    assert tuple(item.builder_id for item in ACTION_REGISTRY) == (
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


def _proven_from_shadow(source: str):
    shadow = snapshot_shadow(source)
    return shadow, next(
        item for item in shadow.frames if item.status is ObjectStatus.PROVEN
    )


def _action_shadow(atom: SourceAtom, result: str, *, span=None) -> tuple[SemanticFrame, ShadowSnapshot]:
    frame = _frame(atom, status=ObjectStatus.PROVEN, result=result)
    binding = _binding(atom, frame)
    if span is not None:
        frame = replace(frame, span=span)
        binding = replace(binding, span=span)
    return frame, ShadowSnapshot(
        result=result,
        control="Педагогическое наблюдение.",
        atoms=(atom,),
        frames=(frame,),
        bindings=(binding,),
    )


def _classify_covered(clause: str, source: str, shadow: ShadowSnapshot) -> DiffKind:
    return classify_snapshots(
        OldSnapshot(
            result="Старый результат.",
            control="Педагогическое наблюдение.",
            coverage=((clause, "COVERED"),),
            source=source,
        ),
        shadow,
    ).kind


def test_training_games_conjugated_head_covers_clause() -> None:
    shadow, proven = _proven_from_shadow(GAMES_SOURCE)
    folded = proven.projected_result.casefold()
    assert proven.kind is FrameKind.ACTION
    assert folded.startswith("отрабатывает")
    assert "земля, вода, лава" in folded
    assert "выше ноги от земли" in folded
    clause = GAMES_SOURCE.rstrip(".")
    assert _frame_matches_clause(clause, proven, shadow)
    assert _classify_covered(clause, GAMES_SOURCE, shadow) is not DiffKind.NEW_LOSES


def test_endurance_training_conjugated_head_covers_clause() -> None:
    shadow, proven = _proven_from_shadow(ENDURANCE_SOURCE)
    assert proven.projected_result == "Отрабатывает на выносливость."
    clause = ENDURANCE_SOURCE.rstrip(".")
    assert _frame_matches_clause(clause, proven, shadow)
    assert _frame_matches_clause(ENDURANCE_SOURCE, proven, shadow)
    assert _classify_covered(clause, ENDURANCE_SOURCE, shadow) is not DiffKind.NEW_LOSES


def test_lost_conjunct_stays_uncovered() -> None:
    source = "Изучение голосовых команд и отработка взаимодействия."
    atom = _atom(source)
    frame, shadow = _action_shadow(atom, "Изучает голосовые команды.")
    clause = source.rstrip(".")
    assert not _frame_matches_clause(clause, frame, shadow)
    assert _classify_covered(clause, source, shadow) is DiffKind.NEW_LOSES


def test_lost_catalog_member_stays_uncovered() -> None:
    source = (
        "Тренировка постановки ног: «Земля, вода, лава», «Сигнал»."
    )
    atom = _atom(source)
    frame, shadow = _action_shadow(
        atom,
        "Отрабатывает постановку ног: «Земля, вода, лава».",
    )
    clause = source.rstrip(".")
    assert not _frame_matches_clause(clause, frame, shadow)
    assert _classify_covered(clause, source, shadow) is DiffKind.NEW_LOSES


def test_finite_not_from_source_head_stays_uncovered() -> None:
    source = "Тренировка постановки ног: «Земля, вода, лава»."
    atom = _atom(source)
    frame, shadow = _action_shadow(
        atom,
        "Выполняет постановку ног: «Земля, вода, лава».",
    )
    clause = source.rstrip(".")
    assert not _frame_matches_clause(clause, frame, shadow)
    assert _classify_covered(clause, source, shadow) is DiffKind.NEW_LOSES


def test_neighbor_atom_span_stays_uncovered() -> None:
    clause = "Тренировка на выносливость."
    atom_span = SourceSpan(
        id="span:a",
        start=0,
        end=len(clause),
        source_fingerprint="fp-a",
        provenance=Provenance(adapter="t", role="span"),
        status=ObjectStatus.PROJECTED,
    )
    neighbor_span = SourceSpan(
        id="span:b",
        start=80,
        end=99,
        source_fingerprint="fp-b",
        provenance=Provenance(adapter="t", role="span"),
        status=ObjectStatus.PROJECTED,
    )
    atom = SourceAtom(
        id="atom:1",
        span=atom_span,
        source_fingerprint=atom_span.source_fingerprint,
        provenance=Provenance(adapter="t", role="atom"),
        status=ObjectStatus.PROJECTED,
        text=clause,
        clause_id="clause:1",
    )
    neighbor = SourceAtom(
        id="atom:2",
        span=neighbor_span,
        source_fingerprint=neighbor_span.source_fingerprint,
        provenance=Provenance(adapter="t", role="atom"),
        status=ObjectStatus.PROJECTED,
        text="Практика страховки.",
        clause_id="clause:2",
    )
    result = "Отрабатывает на выносливость."
    frame = SemanticFrame(
        id="frame:1",
        span=neighbor_span,
        source_fingerprint=atom.source_fingerprint,
        provenance=Provenance(adapter="t", role="frame"),
        status=ObjectStatus.PROVEN,
        kind=FrameKind.ACTION,
        atom_id=atom.id,
        clause_id=atom.clause_id,
        projected_type="",
        projected_result=result,
        projected_control="",
        coverage_status="COVERED",
    )
    binding = replace(_binding(atom, frame), span=neighbor_span)
    shadow = ShadowSnapshot(
        result=result,
        control="Педагогическое наблюдение.",
        atoms=(atom, neighbor),
        frames=(frame,),
        bindings=(binding,),
    )
    assert not _frame_matches_clause(clause, frame, shadow)
    assert _classify_covered(clause, clause, shadow) is DiffKind.NEW_LOSES


def test_bare_atom_text_does_not_close_loss() -> None:
    source = ENDURANCE_SOURCE
    atom = _atom(source)
    frame, shadow = _action_shadow(atom, "Отрабатывает.")
    clause = source.rstrip(".")
    assert atom.text == source
    assert not _frame_matches_clause(clause, frame, shadow)
    assert not _frame_matches_clause(atom.text, frame, shadow)
    assert _classify_covered(clause, source, shadow) is DiffKind.NEW_LOSES
