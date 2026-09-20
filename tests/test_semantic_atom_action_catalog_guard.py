"""Safety guards for action-catalog assignment. No new predicates."""

from __future__ import annotations

from types import SimpleNamespace

from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.action_builders import (
    AMBIGUOUS_CATALOG_ASSIGNMENT,
    OPEN_TAIL_UNRESOLVED,
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


def test_open_list_keeps_proven_c7_head() -> None:
    source = "Экскурсионные поездки: озеро Тишь, мыс Ветер и другие."
    projection = project_frames(source)
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert frame.kind is FrameKind.ACTION
    folded = frame.projected_result.casefold()
    assert "совершает" in folded
    assert "поездк" in folded
    assert "тишь" in folded
    assert "ветер" in folded
    assert "другие" not in folded
    notes = []
    for item in projection.candidates:
        if item.builder_id == "action_catalog":
            notes.extend(item.structural_evidence.notes)
    assert OPEN_TAIL_UNRESOLVED in notes


def test_open_list_is_not_fully_covered() -> None:
    source = "Игры на поляне: «Зигзаг», «Тихий шепот» и т. д."
    projection = project_frames(source)
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert "и т" not in frame.projected_result.casefold()
    assert "т. д" not in frame.projected_result.casefold()
    assert frame.coverage_status != "COVERED" or OPEN_TAIL_UNRESOLVED in {
        note
        for item in projection.candidates
        if item.builder_id == "action_catalog"
        for note in item.structural_evidence.notes
    }


def test_shared_catalog_without_exact_member_is_unresolved() -> None:
    source = "Экскурсионные поездки: озеро Тишь, мыс Ветер, каньон Шёпот."
    projection = project_frames(source, _row("Экскурсионные поездки"))
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.UNRESOLVED
    assert frame.reason == AMBIGUOUS_CATALOG_ASSIGNMENT
    folded = projection.candidate_result.casefold()
    assert "тишь" not in folded
    assert "ветер" not in folded
    assert "шёпот" not in folded and "шепот" not in folded


def test_exact_member_selector_still_works() -> None:
    source = "Экскурсионные поездки: озеро Тишь, мыс Ветер, каньон Шёпот."
    projection = project_frames(source, _row("мыс Ветер"))
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    folded = frame.projected_result.casefold()
    assert "ветер" in folded
    assert "тишь" not in folded
    assert "шёпот" not in folded and "шепот" not in folded


def test_generic_head_selector_is_blocked() -> None:
    source = "Игры на поляне: «Зигзаг», «Тихий шепот»."
    projection = project_frames(source, _row("Игры на поляне"))
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED
    assert projection.frames[0].reason == AMBIGUOUS_CATALOG_ASSIGNMENT
    assert "зигзаг" not in projection.candidate_result.casefold()
    assert "шепот" not in projection.candidate_result.casefold()


def test_single_row_catalog_keeps_full_list() -> None:
    source = "Упражнения: флумберы, квиллинги."
    projection = project_frames(source)
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    folded = frame.projected_result.casefold()
    assert "флумбер" in folded
    assert "квиллинг" in folded


def test_foreign_week_title_still_allows_own_catalog() -> None:
    source = "Круговое ОФП: флумберы, квиллинги."
    projection = project_frames(source, _row("Специальная физическая подготовка"))
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert "офп" in frame.projected_result.casefold()
