"""Heterogeneous ACTION conjuncts cannot share an unproven predicate."""

from __future__ import annotations

from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
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


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_entertainment_series_is_not_covered_by_perform() -> None:
    source = "Развлечения, школьные праздники."
    projection = project_frames(source)
    shadow = snapshot_shadow(source)
    for frame in (*projection.frames, *shadow.frames):
        result = frame.projected_result.casefold()
        if frame.status is ObjectStatus.PROVEN:
            assert not result.startswith("выполняет")
            assert not _frame_matches_clause(
                "Развлечения, школьные праздники", frame, shadow
            )
    assert "выполняет" not in shadow.result.casefold()
    assert all(frame.status is not ObjectStatus.PROVEN for frame in projection.frames)


def test_excursion_observation_series_is_not_wholly_covered() -> None:
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


def test_knowledge_catalog_stays_covers_more() -> None:
    source = "Техника движения в походе: темп, режим."
    frame = project_frames(source).frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    folded = frame.projected_result.casefold()
    assert folded.startswith("называет")
    assert "технику" in folded
    assert "темп" in folded
    assert "режим" in folded
    shadow = snapshot_shadow(source)
    assert not shadow.lexical_violations
    old = OldSnapshot(
        result="Называет технику движения в походе.",
        control=shadow.control,
        coverage=(
            ("Техника движения в походе: темп, режим", "UNCOVERED"),
        ),
        source=source,
    )
    diff = classify_snapshots(old, shadow)
    assert diff.kind is DiffKind.NEW_COVERS_MORE
    assert diff.kind is not DiffKind.NEW_INVENTS


def test_homogeneous_walks_keep_one_proven_predicate() -> None:
    source = "Прогулки по оврагу, экскурсии по роще."
    frame = next(
        item
        for item in project_frames(source).frames
        if item.status is ObjectStatus.PROVEN
    )
    folded = frame.projected_result.casefold()
    assert folded.startswith("совершает")
    assert "прогулк" in folded
    assert "экскурси" in folded
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert _frame_matches_clause("Прогулки по оврагу", proven, shadow)
    assert _frame_matches_clause("экскурсии по роще", proven, shadow)


def test_homogeneous_perform_nouns_keep_one_proven_predicate() -> None:
    source = "Спуск с валуна, подъём на карниз."
    frame = next(
        item
        for item in project_frames(source).frames
        if item.status is ObjectStatus.PROVEN
    )
    folded = frame.projected_result.casefold()
    assert folded.startswith("выполняет")
    assert "спуск" in folded
    assert "подъём" in folded or "подъем" in folded
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert _frame_matches_clause("Спуск с валуна", proven, shadow)
    assert _frame_matches_clause("подъём на карниз", proven, shadow)


def test_mixed_perform_and_knowledge_stays_partial() -> None:
    source = "Спуск с горы, способы поворота."
    projection = project_frames(source)
    frame = next(
        item for item in projection.frames if item.status is ObjectStatus.PROVEN
    )
    folded = frame.projected_result.casefold()
    assert "спуск" in folded
    assert "поворота" not in folded
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert _frame_matches_clause("Спуск с горы", proven, shadow)
    assert not _frame_matches_clause("способы поворота", proven, shadow)


def test_coordinated_object_nouns_stay_under_one_head() -> None:
    source = "Отработка техники спуска и приземления."
    frame = next(
        item
        for item in project_frames(source).frames
        if item.status is ObjectStatus.PROVEN
    )
    folded = frame.projected_result.casefold()
    assert "спуск" in folded
    assert "приземлен" in folded


def test_shared_complement_after_one_head_stays_merged() -> None:
    source = "Отработка постановки ног и рук на карнизе."
    frame = next(
        item
        for item in project_frames(source).frames
        if item.status is ObjectStatus.PROVEN
    )
    folded = frame.projected_result.casefold()
    assert "ног" in folded
    assert "рук" in folded


def test_location_list_after_one_head_stays_merged() -> None:
    source = "Висы на зацепках, планках, турнике."
    frame = next(
        item
        for item in project_frames(source).frames
        if item.status is ObjectStatus.PROVEN
    )
    folded = frame.projected_result.casefold()
    assert "зацепк" in folded
    assert "планк" in folded
    assert "турник" in folded


def test_paired_verbal_nouns_keep_both_predicates() -> None:
    source = "Разведение и поддержание костра."
    frame = project_frames(source).frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    folded = frame.projected_result.casefold()
    assert "разводит" in folded
    assert "поддерживает" in folded


def test_parenthetical_members_are_not_split() -> None:
    source = "Закаливание природными факторами (солнце, воздух, вода)."
    frame = next(
        item
        for item in project_frames(source).frames
        if item.status is ObjectStatus.PROVEN
    )
    folded = frame.projected_result.casefold()
    assert "солнце" in folded
    assert "воздух" in folded
    assert "вода" in folded


def test_incompatible_series_does_not_invent() -> None:
    for source in (
        "Развлечения, школьные праздники.",
        "Экскурсии в природу, наблюдения.",
    ):
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
