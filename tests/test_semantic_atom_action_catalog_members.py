"""Action catalog RESULT keeps explicit SOURCE items. No new predicates."""

from __future__ import annotations

from types import SimpleNamespace

from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.action_builders import (
    AMBIGUOUS_CATALOG_ASSIGNMENT,
    OPEN_TAIL_UNRESOLVED,
)
from calendar_pedagoga.semantic_atom.control_adapter import compose_control
from calendar_pedagoga.semantic_atom.diff_adapter import (
    _frame_matches_clause,
    snapshot_shadow,
)
from calendar_pedagoga.semantic_atom.frame_adapter import project_frames
from calendar_pedagoga.semantic_atom.models import FrameKind, ObjectStatus


def _row(title: str) -> SimpleNamespace:
    return SimpleNamespace(
        lesson_type="",
        planned_result="",
        assessment_method="",
        topic_title=title,
    )


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_closed_catalog_keeps_all_items() -> None:
    source = "Круговое ОФП: флумберы, квиллинги, гомфры (2 круга)."
    frame = project_frames(source).frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    assert frame.kind is FrameKind.ACTION
    folded = frame.projected_result.casefold()
    assert "флумбер" in folded
    assert "квиллинг" in folded
    assert "гомфр" in folded
    assert "(2 круга)" in frame.projected_result


def test_game_names_and_quotes_are_kept() -> None:
    source = "Игры на поляне: «Зигзаг», «Тихий шепот» (тихий круг)."
    frame = project_frames(source).frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert "«Зигзаг»" in frame.projected_result
    assert "«Тихий шепот»" in frame.projected_result
    assert "(тихий круг)" in frame.projected_result


def test_open_tail_stays_unresolved_but_keeps_explicit_items() -> None:
    source = "Игры на поляне: «Зигзаг», «Тихий шепот» и другие."
    projection = project_frames(source)
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    folded = frame.projected_result.casefold()
    assert "зигзаг" in folded
    assert "шепот" in folded
    assert "другие" not in folded
    notes = {
        note
        for item in projection.candidates
        if item.builder_id == "action_catalog"
        for note in item.structural_evidence.notes
    }
    assert OPEN_TAIL_UNRESOLVED in notes
    shadow = snapshot_shadow(source)
    proven = shadow.frames[0]
    assert not _frame_matches_clause("и другие", proven, shadow)
    assert not _frame_matches_clause("другие", proven, shadow)


def test_exact_member_selector_emits_only_chosen_item() -> None:
    source = "Экскурсионные поездки: озеро Тишь, мыс Ветер, каньон Шёпот."
    projection = project_frames(source, _row("мыс Ветер"))
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    folded = frame.projected_result.casefold()
    assert "ветер" in folded
    assert "тишь" not in folded
    assert "шёпот" not in folded and "шепот" not in folded
    covered = source[frame.span.start : frame.span.end]
    assert "тишь" not in covered.casefold()
    assert "шёпот" not in covered.casefold() and "шепот" not in covered.casefold()
    bound = [item for item in projection.bindings if item.frame_id == frame.id]
    assert bound
    assert bound[0].span.start == frame.span.start
    assert bound[0].span.end == frame.span.end


def test_generic_selector_does_not_emit_full_list() -> None:
    source = "Экскурсионные поездки: озеро Тишь, мыс Ветер, каньон Шёпот."
    projection = project_frames(source, _row("Экскурсионные поездки"))
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED
    assert projection.frames[0].reason == AMBIGUOUS_CATALOG_ASSIGNMENT
    folded = projection.candidate_result.casefold()
    assert "тишь" not in folded
    assert "ветер" not in folded


def test_missed_item_is_not_covered() -> None:
    source = "Упражнения: флумберы, квиллинги, гомфры."
    projection = project_frames(source, _row("квиллинги"))
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    shadow = snapshot_shadow(source, _row("квиллинги"))
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert _frame_matches_clause("квиллинги", proven, shadow)
    assert not _frame_matches_clause("флумберы", proven, shadow)
    assert not _frame_matches_clause("гомфры", proven, shadow)


def test_closed_catalog_items_are_covered() -> None:
    source = "Круговое ОФП: флумберы, квиллинги."
    shadow = snapshot_shadow(source)
    proven = shadow.frames[0]
    assert proven.status is ObjectStatus.PROVEN
    assert _frame_matches_clause("флумберы", proven, shadow)
    assert _frame_matches_clause("квиллинги", proven, shadow)
    assert _frame_matches_clause(source.rstrip("."), proven, shadow)


def test_control_still_comes_from_c9() -> None:
    source = "Круговое ОФП: флумберы, квиллинги."
    projection = project_frames(source)
    assert projection.frames[0].status is ObjectStatus.PROVEN
    control = compose_control(projection)
    assert projection.identity_control == ""
    if control.composed_control:
        assert all(piece.provenance.adapter == "control_c9" for piece in control.pieces)
