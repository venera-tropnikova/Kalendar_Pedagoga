"""Top-level semicolon tails become independent shadow atoms."""

from __future__ import annotations

from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.atom_adapter import atomize, reconstruct_source
from calendar_pedagoga.semantic_atom.diff_adapter import snapshot_shadow
from calendar_pedagoga.semantic_atom.frame_adapter import project_frames
from calendar_pedagoga.semantic_atom.models import FrameKind, ObjectStatus

LIVE_EVENT_SOURCE = (
    "Подготовка и участие в туристско-краеведческих массовых мероприятиях. "
    "Туриада, «Зимние забавы», «Туристскими тропами»."
)
DIDACTIC_SOURCE = (
    "Проведение дидактических и ролевых игр: «Давай поговорим», «Комплимент», "
    "игра-фантазия «Если бы я был взрослым…»; подвижных игр, праздников с "
    "участием родителей. Рисунки."
)


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_independent_activity_after_semicolon_keeps_own_predicate() -> None:
    source = "Выполняет упражнение; участие в походе."
    result = atomize(source)
    assert reconstruct_source(result) == source
    assert [atom.text for atom in result.atoms] == [
        "Выполняет упражнение",
        "участие в походе.",
    ]
    assert ";" not in result.atoms[0].text
    assert not result.atoms[1].text.startswith(";")
    assert any(";" in item.text for item in result.delimiters)
    projection = project_frames(source)
    left, right = projection.frames
    assert left.status is ObjectStatus.PROVEN
    assert right.status is ObjectStatus.PROVEN
    assert left.predicate.casefold() != right.predicate.casefold()
    assert "участвует" in right.projected_result.casefold()
    assert "выполняет" not in right.projected_result.casefold()
    assert "походе" not in left.projected_result.casefold()


def test_risunki_after_finished_unit_is_own_atom_and_frame() -> None:
    result = atomize(DIDACTIC_SOURCE)
    assert reconstruct_source(result) == DIDACTIC_SOURCE
    drawing = next(atom for atom in result.atoms if atom.text.startswith("Рисунки"))
    assert drawing.text == "Рисунки."
    assert not drawing.text.startswith(";")
    games = next(
        atom
        for atom in result.atoms
        if "подвижных игр" in atom.text.casefold()
    )
    assert games.id != drawing.id
    assert "Рисунки" not in games.text
    assert games.span.end <= drawing.span.start
    projection = project_frames(DIDACTIC_SOURCE)
    drawing_frame = next(
        frame
        for atom, frame in zip(projection.atoms, projection.frames)
        if atom.id == drawing.id
    )
    left_frame = next(
        frame
        for atom, frame in zip(projection.atoms, projection.frames)
        if "дидактических" in atom.text.casefold()
    )
    assert drawing_frame.predicate != left_frame.predicate
    assert "проводит" not in (drawing_frame.projected_result or "").casefold()
    assert "проведение" not in (drawing_frame.predicate or "").casefold()


def test_colon_catalog_with_semicolons_stays_one_atom() -> None:
    sources = (
        "Понятия: ритм; темп; динамика.",
        "Круговое ОФП: планка; выпрыгивание; вис на турнике.",
        "Государственные символы: герб, флаг, гимн; их смысл.",
    )
    for source in sources:
        result = atomize(source)
        assert reconstruct_source(result) == source
        assert len(result.atoms) == 1
        assert ";" in result.atoms[0].text
        frame = project_frames(source).frames[0]
        assert frame.status is ObjectStatus.PROVEN, source


def test_quotes_and_brackets_are_not_split() -> None:
    quoted = atomize("Знаки «Берегите природу; не сори».")
    assert reconstruct_source(quoted) == "Знаки «Берегите природу; не сори»."
    assert len(quoted.atoms) == 1
    brackets = atomize("Список (один; два).")
    assert reconstruct_source(brackets) == "Список (один; два)."
    assert len(brackets.atoms) == 1


def test_several_semicolons_keep_exact_spans_and_round_trip() -> None:
    source = "Рисование деревьев; изучает карту; участие в походе."
    result = atomize(source)
    assert reconstruct_source(result) == source
    texts = [atom.text for atom in result.atoms]
    assert texts == [
        "Рисование деревьев",
        "изучает карту",
        "участие в походе.",
    ]
    assert all(not atom.text.startswith(";") for atom in result.atoms)
    assert [atom.span.start for atom in result.atoms] == [
        source.find(text) for text in texts
    ]
    assert [source[atom.span.start : atom.span.end] for atom in result.atoms] == texts
    pieces = [
        (item.span.start, item.text)
        for item in (*result.atoms, *result.delimiters)
    ]
    pieces.sort(key=lambda item: item[0])
    assert "".join(text for _start, text in pieces) == source


def test_neighbor_atom_does_not_inherit_predicate() -> None:
    source = (
        "Любимые зимние развлечения – катание на санках, на коньках; "
        "лыжные прогулки."
    )
    result = atomize(source)
    assert reconstruct_source(result) == source
    left, right = result.atoms
    assert "лыжные прогулки" not in left.text
    assert right.text == "лыжные прогулки."
    projection = project_frames(source)
    left_frame, right_frame = projection.frames
    assert right_frame.predicate != left_frame.predicate or not right_frame.predicate
    assert "катание" not in (right_frame.projected_result or "").casefold()
    if right_frame.status is ObjectStatus.UNRESOLVED:
        assert right_frame.projected_result == ""


def test_event_tail_period_form_is_unchanged() -> None:
    shadow = snapshot_shadow(LIVE_EVENT_SOURCE)
    folded = shadow.result.casefold()
    assert "туриада" in folded
    assert "зимние забавы" in folded
    parent = next(
        frame
        for frame in project_frames(LIVE_EVENT_SOURCE).frames
        if frame.status is ObjectStatus.PROVEN
        and "туриада" in frame.projected_result.casefold()
    )
    assert parent.kind is FrameKind.ACTION
    assert ";" not in LIVE_EVENT_SOURCE


def test_ambiguous_semicolon_gloss_stays_unresolved() -> None:
    source = "Домашние животные; их значение в жизни человека."
    result = atomize(source)
    assert reconstruct_source(result) == source
    assert [atom.text for atom in result.atoms] == [
        "Домашние животные",
        "их значение в жизни человека.",
    ]
    right = project_frames(source).frames[1]
    assert right.status is ObjectStatus.UNRESOLVED
    assert right.projected_result == ""
