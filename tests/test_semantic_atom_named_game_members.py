"""Closed quoted game/form names stay in ACTION RESULT without Head:."""

from __future__ import annotations

from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.action_builders import ACTION_REGISTRY
from calendar_pedagoga.semantic_atom.atom_adapter import atomize
from calendar_pedagoga.semantic_atom.diff_adapter import (
    _frame_matches_clause,
    snapshot_shadow,
)
from calendar_pedagoga.semantic_atom.frame_adapter import project_frames
from calendar_pedagoga.semantic_atom.models import FrameKind, ObjectStatus
from calendar_pedagoga.semantic_atom.span_cover import action_cover_text
from tests.test_semantic_atom_action_event_tail import LIVE_EVENT_SOURCE
from tests.test_semantic_atom_semicolon_atoms import DIDACTIC_SOURCE


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_action_catalog_registry_unchanged() -> None:
    assert tuple(item.builder_id for item in ACTION_REGISTRY) == (
        "finite_produce",
        "explicit_action",
        "nominal_activity",
        "closed_form_activity",
        "unconjugated_practice",
        "care_and_repair",
        "paired_shared_object",
        "proven_finite",
        "walk_travel",
        "exercise",
        "action_catalog",
    )


def _proven_action(source: str):
    projection = project_frames(source)
    return next(
        item for item in projection.frames if item.status is ObjectStatus.PROVEN
    )


def test_one_named_game_keeps_source_title() -> None:
    source = "Игра-викторина «Вода»."
    frame = _proven_action(source)
    assert frame.kind is FrameKind.ACTION
    folded = frame.projected_result.casefold()
    assert folded.startswith("участвует")
    assert "игре-викторине" in folded
    assert "«вода»" in folded
    assert "вода" in folded
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert _frame_matches_clause("Игра-викторина", proven, shadow)
    assert _frame_matches_clause("Игра-викторина «Вода»", proven, shadow)
    covered = source[proven.span.start : proven.span.end]
    assert "Игра-викторина" in covered
    assert "Вода" in covered
    atom = next(item for item in shadow.atoms if item.id == proven.atom_id)
    assert action_cover_text(atom.text, proven)
    assert proven.span.start >= atom.span.start
    assert proven.span.end <= atom.span.end
    assert proven.source_fingerprint == atom.source_fingerprint


def test_several_quoted_members_without_colon_are_kept() -> None:
    source = "Интерактивные игры «Зеркало», «Мостик»."
    frame = _proven_action(source)
    assert frame.kind is FrameKind.ACTION
    result = frame.projected_result
    folded = result.casefold()
    assert folded.startswith("участвует")
    assert ":" not in source
    assert "«Зеркало»" in result
    assert "«Мостик»" in result
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert _frame_matches_clause("Интерактивные игры", proven, shadow)
    assert _frame_matches_clause("Зеркало", proven, shadow)
    assert _frame_matches_clause("Мостик", proven, shadow)
    covered = source[proven.span.start : proven.span.end]
    assert "Интерактивные игры" in covered
    assert "Зеркало" in covered
    assert "Мостик" in covered
    atom = next(item for item in shadow.atoms if item.id == proven.atom_id)
    binding = next(item for item in shadow.bindings if item.span.id == proven.span.id or (
        item.span.start == proven.span.start and item.span.end == proven.span.end
    ))
    assert binding.source_fingerprint == atom.source_fingerprint
    assert (binding.span.start, binding.span.end) == (proven.span.start, proven.span.end)
    assert "Зеркало" in action_cover_text(atom.text, proven)
    assert "Мостик" in action_cover_text(atom.text, proven)


def test_didactic_quoted_pair_keeps_exact_spans() -> None:
    source = "Дидактические игры «А», «Б»."
    frame = _proven_action(source)
    result = frame.projected_result
    assert "«А»" in result
    assert "«Б»" in result
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    atom = next(item for item in shadow.atoms if item.id == proven.atom_id)
    covered = source[proven.span.start : proven.span.end]
    assert covered.find("«А»") >= 0
    assert covered.find("«Б»") >= 0
    assert source.find("«А»") == covered.find("«А»")
    assert source[source.find("«А»") : source.find("«А»") + 3] == "«А»"
    assert source[source.find("«Б»") : source.find("«Б»") + 3] == "«Б»"
    assert proven.source_fingerprint == atom.source_fingerprint
    assert _frame_matches_clause("Дидактические игры «А»", proven, shadow)
    assert _frame_matches_clause("Дидактические игры «А», «Б»", proven, shadow)


def test_open_tail_quoted_members_stay_unresolved() -> None:
    for source in (
        "Интерактивные игры «Зеркало», «Мостик» и другие.",
        "Интерактивные игры «Зеркало», «Мостик» и т. д.",
        "Интерактивные игры «Зеркало», «Мостик»…",
    ):
        projection = project_frames(source)
        frame = next(
            item for item in projection.frames if item.status is ObjectStatus.PROVEN
        )
        folded = frame.projected_result.casefold()
        assert "мостик" not in folded
        assert "другие" not in folded
        assert "т. д" not in folded
        shadow = snapshot_shadow(source)
        proven = next(item for item in shadow.frames if item.id == frame.id)
        assert not _frame_matches_clause("Мостик", proven, shadow)
        assert not _frame_matches_clause("и другие", proven, shadow)
        covered = source[proven.span.start : proven.span.end]
        assert "мостик" not in covered.casefold()
        assert "другие" not in covered.casefold()


def test_quoted_only_and_theory_event_are_not_action() -> None:
    quoted = project_frames("«Зеркало», «Мостик».")
    assert quoted.frames
    assert all(frame.status is ObjectStatus.UNRESOLVED for frame in quoted.frames)
    assert "участвует" not in snapshot_shadow("«Зеркало», «Мостик».").result.casefold()

    theory = project_frames("Понятия: ритм, темп, динамика.").frames[0]
    assert theory.status is ObjectStatus.PROVEN
    assert theory.kind is FrameKind.KNOWLEDGE
    assert theory.projected_result.casefold().startswith("объясняет")

    event = project_frames("Туриада, «Зимние забавы», «Туристскими тропами».")
    assert all(
        frame.kind is not FrameKind.ACTION or frame.status is ObjectStatus.UNRESOLVED
        for frame in event.frames
    )
    folded = snapshot_shadow(
        "Туриада, «Зимние забавы», «Туристскими тропами»."
    ).result.casefold()
    assert "участвует" not in folded
    assert "выполняет туриада" not in folded


def test_source_typo_igrana_stays_unresolved() -> None:
    source = "Играна «Вода»."
    projection = project_frames(source)
    assert projection.frames
    assert all(frame.status is ObjectStatus.UNRESOLVED for frame in projection.frames)
    assert snapshot_shadow(source).result == ""


def test_neighbor_predicate_is_not_inherited() -> None:
    source = (
        "Подготовка и участие в туристско-краеведческих массовых мероприятиях. "
        "Игра-викторина «Вода»."
    )
    projection = project_frames(source)
    parent = next(
        frame
        for frame in projection.frames
        if frame.status is ObjectStatus.PROVEN
        and "мероприятиях" in frame.projected_result.casefold()
    )
    quiz = next(
        frame
        for atom, frame in zip(projection.atoms, projection.frames)
        if "игра-викторина" in atom.text.casefold()
    )
    assert quiz.status is ObjectStatus.PROVEN
    assert quiz.id != parent.id
    assert "вода" in quiz.projected_result.casefold()
    assert "туриада" not in quiz.projected_result.casefold()
    assert "подготавливает" not in quiz.projected_result.casefold()
    covered = source[parent.span.start : parent.span.end]
    assert "Вода" not in covered
    assert "Игра-викторина" not in covered


def test_colon_catalog_regression_keeps_quoted_members() -> None:
    source = "Игры на поляне: «Зигзаг», «Тихий шепот»."
    frame = _proven_action(source)
    assert "«Зигзаг»" in frame.projected_result
    assert "«Тихий шепот»" in frame.projected_result
    didactic = "Дидактические игры: «А», «Б»."
    catalog = _proven_action(didactic)
    assert "«А»" in catalog.projected_result
    assert "«Б»" in catalog.projected_result


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


def test_list_conjunct_regression_keeps_proven_members() -> None:
    source = "Викторины, кроссворды, ребусы."
    frame = _proven_action(source)
    folded = frame.projected_result.casefold()
    assert folded.startswith("участвует")
    assert "викторин" in folded
    assert "кроссворд" in folded
    assert "ребус" in folded
    leftover = "Спуск с горы, способы поворота."
    projection = project_frames(leftover)
    action = next(item for item in projection.frames if item.kind is FrameKind.ACTION)
    assert "поворота" not in action.projected_result.casefold()
