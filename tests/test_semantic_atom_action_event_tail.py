"""Event-tail after a proven activity-head stays one semantic unit."""

from __future__ import annotations

from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.action_builders import ACTION_REGISTRY
from calendar_pedagoga.semantic_atom.atom_adapter import atomize
from calendar_pedagoga.semantic_atom.diff_adapter import snapshot_shadow
from calendar_pedagoga.semantic_atom.frame_adapter import project_frames
from calendar_pedagoga.semantic_atom.models import FrameKind, ObjectStatus

LIVE_EVENT_SOURCE = (
    "Подготовка и участие в туристско-краеведческих массовых мероприятиях. "
    "Туриада, «Зимние забавы», «Туристскими тропами»."
)
COLON_EVENT_SOURCE = (
    "Подготовка и участие в туристско-краеведческих массовых мероприятиях: "
    "Туриада, «Зимние забавы», «Туристскими тропами»."
)
PARENT_PREDICATES = ("подготавливает", "участвует")


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


def _assert_event_names_kept(source: str) -> None:
    projection = project_frames(source)
    shadow = snapshot_shadow(source)
    folded = shadow.result.casefold()
    assert "туриада" in folded
    assert "зимние забавы" in folded
    assert "туристскими тропами" in folded
    assert any(folded.startswith(item) or f". {item}" in folded for item in PARENT_PREDICATES)
    assert "выполняет туриада" not in folded
    parent = next(
        frame
        for frame in projection.frames
        if frame.status is ObjectStatus.PROVEN
        and "туриада" in frame.projected_result.casefold()
    )
    assert parent.kind is FrameKind.ACTION
    assert parent.predicate.casefold() in PARENT_PREDICATES
    assert any(item in parent.projected_result.casefold() for item in PARENT_PREDICATES)
    covered = source[parent.span.start : parent.span.end]
    assert "Туриада" in covered
    assert "Зимние забавы" in covered
    assert "Туристскими тропами" in covered
    assert not shadow.lexical_violations


def test_period_separated_event_names_keep_parent_predicate() -> None:
    _assert_event_names_kept(LIVE_EVENT_SOURCE)
    parent = next(
        frame
        for frame in project_frames(LIVE_EVENT_SOURCE).frames
        if frame.status is ObjectStatus.PROVEN
        and "туриада" in frame.projected_result.casefold()
    )
    assert parent.object == (
        "Подготовка и участие в туристско-краеведческих массовых мероприятиях: "
        "Туриада, «Зимние забавы», «Туристскими тропами»"
    )


def test_colon_event_names_keep_parent_predicate() -> None:
    _assert_event_names_kept(COLON_EVENT_SOURCE)


def test_four_source_event_tails_keep_names_without_week_ids() -> None:
    source = " ".join((LIVE_EVENT_SOURCE, LIVE_EVENT_SOURCE, LIVE_EVENT_SOURCE, LIVE_EVENT_SOURCE))
    shadow = snapshot_shadow(source)
    folded = shadow.result.casefold()
    assert folded.count("туриада") >= 4
    assert "зимние забавы" in folded
    assert "туристскими тропами" in folded
    assert "week" not in folded
    assert "ключ" not in folded


def test_independent_next_sentence_is_not_glued() -> None:
    source = (
        "Подготовка и участие в туристско-краеведческих массовых мероприятиях. "
        "Изучает карту и компас."
    )
    projection = project_frames(source)
    shadow = snapshot_shadow(source)
    head = next(
        frame
        for frame in projection.frames
        if frame.status is ObjectStatus.PROVEN
        and "мероприятиях" in frame.projected_result.casefold()
    )
    assert "изучает" not in head.projected_result.casefold()
    assert "карту" not in head.projected_result.casefold()
    study = next(
        frame
        for frame in projection.frames
        if "изучает" in frame.projected_result.casefold()
    )
    assert study.status is ObjectStatus.PROVEN
    assert study.span.start >= head.span.end
    assert "туриада" not in shadow.result.casefold()


def test_quoted_only_atom_without_activity_parent_stays_unresolved() -> None:
    source = "«Зимние забавы», «Туристскими тропами»."
    projection = project_frames(source)
    assert projection.frames
    assert all(frame.status is ObjectStatus.UNRESOLVED for frame in projection.frames)
    assert "подготавливает" not in snapshot_shadow(source).result.casefold()
    assert "участвует" not in snapshot_shadow(source).result.casefold()


def test_theory_catalog_is_not_an_event() -> None:
    source = "Понятия: ритм, темп, динамика."
    frame = project_frames(source).frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    folded = frame.projected_result.casefold()
    assert folded.startswith("объясняет")
    assert "ритм" in folded
    assert not folded.startswith("подготавливает")
    assert not folded.startswith("участвует")
    assert not folded.startswith("выполняет понятия")


def test_ofp_action_catalog_unchanged() -> None:
    source = "Круговое ОФП: планка, выпрыгивание, вис на турнике."
    frame = project_frames(source).frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    folded = frame.projected_result.casefold()
    assert folded.startswith("выполняет")
    assert "планка" in folded
    assert "выпрыгивание" in folded
    assert "турнике" in folded


def test_semicolon_tail_is_not_event_glued() -> None:
    source = (
        "Подготовка и участие в туристско-краеведческих массовых мероприятиях; "
        "Туриада, «Зимние забавы», «Туристскими тропами»."
    )
    atoms = atomize(source).atoms
    assert any(";" not in atom.text for atom in atoms)
    projection = project_frames(source)
    head = next(
        (
            frame
            for frame in projection.frames
            if frame.status is ObjectStatus.PROVEN
            and "мероприятиях" in (frame.projected_result or "").casefold()
        ),
        None,
    )
    if head is not None:
        folded = head.projected_result.casefold()
        assert "туриада" not in folded
        assert "зимние забавы" not in folded


def test_open_event_tail_stays_unresolved() -> None:
    source = (
        "Подготовка и участие в туристско-краеведческих массовых мероприятиях. "
        "Туриада, «Зимние забавы» и другие."
    )
    projection = project_frames(source)
    shadow = snapshot_shadow(source)
    folded = shadow.result.casefold()
    assert "и другие" not in folded
    assert "туриада" not in folded
    tail = next(
        frame
        for atom, frame in zip(projection.atoms, projection.frames)
        if "туриада" in atom.text.casefold()
    )
    assert tail.status is ObjectStatus.UNRESOLVED


def test_neighbor_atom_is_not_covered() -> None:
    source = (
        "Подготовка и участие в туристско-краеведческих массовых мероприятиях. "
        "Туриада, «Зимние забавы», «Туристскими тропами». "
        "Викторина «Вода»."
    )
    projection = project_frames(source)
    parent = next(
        frame
        for frame in projection.frames
        if frame.status is ObjectStatus.PROVEN
        and "туриада" in frame.projected_result.casefold()
    )
    covered = source[parent.span.start : parent.span.end]
    assert "Викторина" not in covered
    assert "Вода" not in parent.projected_result
    quiz = next(
        frame
        for atom, frame in zip(projection.atoms, projection.frames)
        if "викторина" in atom.text.casefold()
    )
    assert quiz.id != parent.id
    if quiz.status is ObjectStatus.PROVEN:
        assert "туриада" not in quiz.projected_result.casefold()


def test_quoted_product_inside_creative_np_is_not_event_tail() -> None:
    source = (
        "Экскурсии в природу, наблюдения. "
        "Творческая работа детей из природного материала «Осенний букет»."
    )
    projection = project_frames(source)
    shadow = snapshot_shadow(source)
    head = next(
        frame
        for frame in projection.frames
        if frame.status is ObjectStatus.PROVEN
        and "экскурси" in frame.projected_result.casefold()
    )
    folded = head.projected_result.casefold()
    assert "осенний букет" not in folded
    assert "творческая работа" not in folded
    assert "осенний букет" not in shadow.result.casefold()
    bouquet = next(
        frame
        for atom, frame in zip(projection.atoms, projection.frames)
        if "осенний букет" in atom.text.casefold()
    )
    assert bouquet.id != head.id
    assert bouquet.status is ObjectStatus.UNRESOLVED
    projection = project_frames(source)
    shadow = snapshot_shadow(source)
    head = next(
        frame
        for frame in projection.frames
        if frame.status is ObjectStatus.PROVEN
        and "экскурси" in frame.projected_result.casefold()
    )
    folded = head.projected_result.casefold()
    assert "осенний букет" not in folded
    assert "творческая работа" not in folded
    assert "осенний букет" not in shadow.result.casefold()
    bouquet = next(
        frame
        for atom, frame in zip(projection.atoms, projection.frames)
        if "осенний букет" in atom.text.casefold()
    )
    assert bouquet.id != head.id
    assert bouquet.status is ObjectStatus.UNRESOLVED


def test_no_new_predicate_is_created() -> None:
    projection = project_frames(LIVE_EVENT_SOURCE)
    parent = next(
        frame
        for frame in projection.frames
        if frame.status is ObjectStatus.PROVEN
        and "туриада" in frame.projected_result.casefold()
    )
    assert parent.predicate.casefold() in PARENT_PREDICATES
    assert "выполняет" not in parent.predicate.casefold()
    assert ACTION_REGISTRY[-1].builder_id == "action_catalog"
