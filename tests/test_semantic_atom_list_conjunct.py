"""Homogeneous list-conjuncts keep independently proven members only."""

from __future__ import annotations

from itertools import permutations

from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.atom_adapter import atomize
from calendar_pedagoga.semantic_atom.diff_adapter import (
    OldSnapshot,
    _frame_matches_clause,
    classify_snapshots,
    snapshot_shadow,
)
from calendar_pedagoga.semantic_atom.frame_adapter import project_frames
from calendar_pedagoga.semantic_atom.models import FrameKind, ObjectStatus
from calendar_pedagoga.semantic_atom.passthrough import DiffKind
from calendar_pedagoga.semantic_atom.span_cover import action_cover_text
from tests.test_semantic_atom_action_event_tail import LIVE_EVENT_SOURCE
from tests.test_semantic_atom_semicolon_atoms import DIDACTIC_SOURCE


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def _proven_action(source: str):
    projection = project_frames(source)
    return next(
        item for item in projection.frames if item.status is ObjectStatus.PROVEN
    )


def test_three_homogeneous_activity_objects_keep_all_proven_members() -> None:
    source = "Викторины, кроссворды, ребусы."
    frame = _proven_action(source)
    folded = frame.projected_result.casefold()
    assert frame.kind is FrameKind.ACTION
    assert folded.startswith("участвует")
    assert "викторин" in folded
    assert "кроссворд" in folded
    assert "ребус" in folded
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert _frame_matches_clause("Викторины", proven, shadow)
    assert _frame_matches_clause("кроссворды", proven, shadow)
    assert _frame_matches_clause("ребусы", proven, shadow)
    covered = source[proven.span.start : proven.span.end]
    assert "Викторины" in covered
    assert "кроссворды" in covered
    assert "ребусы" in covered
    assert "кроссворд" in proven.projected_result.casefold()
    assert proven.span.start >= 0
    atom = next(item for item in shadow.atoms if item.id == proven.atom_id)
    assert action_cover_text(atom.text, proven)
    old = OldSnapshot(
        result="Участвует в викторине.",
        control=shadow.control,
        coverage=(("Викторины, кроссворды, ребусы", "UNCOVERED"),),
        source=source,
    )
    diff = classify_snapshots(old, shadow)
    assert diff.kind is not DiffKind.NEW_INVENTS


def test_quiz_crossword_rebus_keep_proven_locative() -> None:
    source = "Викторины, кроссворды, ребусы."
    frame = _proven_action(source)
    folded = frame.projected_result.casefold()
    assert "викторине" in folded
    assert "кроссвордах" in folded
    assert "ребусах" in folded
    assert "кроссворды," not in folded
    assert "ребусы." not in folded


def test_clothes_and_winter_gear_are_knowledge_with_proven_case() -> None:
    source = "Одежда, зимний инвентарь."
    projection = project_frames(source)
    frame = next(
        item for item in projection.frames if item.status is ObjectStatus.PROVEN
    )
    assert frame.kind is FrameKind.KNOWLEDGE
    folded = frame.projected_result.casefold()
    assert folded.startswith("характеризует")
    assert "одежду" in folded
    assert "зимний инвентарь" in folded
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert _frame_matches_clause("Одежда", proven, shadow)
    assert _frame_matches_clause("зимний инвентарь", proven, shadow)
    covered = source[proven.span.start : proven.span.end]
    assert "Одежда" in covered
    assert "зимний инвентарь" in covered
    standalone = project_frames("Зимний инвентарь.").frames[0]
    if standalone.status is ObjectStatus.PROVEN:
        assert standalone.kind is FrameKind.KNOWLEDGE
        assert "инвентарь" in standalone.projected_result.casefold()


def test_last_conjunct_with_other_predicate_stays_unresolved() -> None:
    source = "Викторины, кроссворды, способы поворота."
    frame = _proven_action(source)
    folded = frame.projected_result.casefold()
    assert "викторин" in folded
    assert "кроссворд" in folded
    assert "способ" not in folded
    assert "поворот" not in folded
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert _frame_matches_clause("Викторины", proven, shadow)
    assert _frame_matches_clause("кроссворды", proven, shadow)
    assert not _frame_matches_clause("способы поворота", proven, shadow)
    covered = source[proven.span.start : proven.span.end]
    assert "способ" not in covered.casefold()
    atom = next(item for item in shadow.atoms if item.id == proven.atom_id)
    assert (proven.span.start, proven.span.end) != (atom.span.start, atom.span.end)


def test_open_tail_is_not_covered() -> None:
    for source in (
        "Викторины, кроссворды и другие.",
        "Викторины, кроссворды и т. д.",
        "Викторины, кроссворды, ...",
    ):
        frame = _proven_action(source)
        folded = frame.projected_result.casefold()
        assert "викторин" in folded
        assert "другие" not in folded
        assert "т. д" not in folded
        assert "..." not in folded
        shadow = snapshot_shadow(source)
        proven = next(item for item in shadow.frames if item.id == frame.id)
        covered = source[proven.span.start : proven.span.end]
        assert "другие" not in covered.casefold()
        assert "т. д" not in covered.casefold()
        atom = next(item for item in shadow.atoms if item.id == proven.atom_id)
        assert (proven.span.start, proven.span.end) != (atom.span.start, atom.span.end)


def test_excursions_and_observations_do_not_share_one_predicate() -> None:
    source = "Экскурсии в природу, наблюдения."
    projection = project_frames(source)
    shadow = snapshot_shadow(source)
    for frame in (*projection.frames, *shadow.frames):
        if frame.kind is not FrameKind.ACTION:
            continue
        result = frame.projected_result.casefold()
        if result.startswith("совершает"):
            assert "наблюден" not in result
        assert not _frame_matches_clause(
            "Экскурсии в природу, наблюдения", frame, shadow
        )
        if frame.status is ObjectStatus.PROVEN:
            cover = action_cover_text(
                next(atom.text for atom in shadow.atoms if atom.id == frame.atom_id),
                frame,
            )
            assert "наблюден" not in cover.casefold()


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
    assert left.predicate.casefold() != right.predicate.casefold()
    didactic = atomize(DIDACTIC_SOURCE)
    drawing = next(atom for atom in didactic.atoms if atom.text.startswith("Рисунки"))
    assert drawing.text == "Рисунки."


def test_exact_spans_and_permutation_do_not_invent() -> None:
    members = ("Викторины", "кроссворды", "ребусы")
    for order in permutations(members):
        source = f"{', '.join(order)}."
        frame = _proven_action(source)
        folded = frame.projected_result.casefold()
        assert folded.startswith("участвует"), source
        assert "викторин" in folded, source
        assert "кроссворд" in folded, source
        assert "ребус" in folded, source
        shadow = snapshot_shadow(source)
        proven = next(item for item in shadow.frames if item.id == frame.id)
        covered = source[proven.span.start : proven.span.end]
        for member in members:
            assert _frame_matches_clause(member, proven, shadow), source
            assert member.casefold() in covered.casefold(), source
        old = OldSnapshot(
            result="Участвует в викторине.",
            control=shadow.control,
            coverage=((source.rstrip("."), "UNCOVERED"),),
            source=source,
        )
        diff = classify_snapshots(old, shadow)
        assert diff.kind is not DiffKind.NEW_INVENTS, source
