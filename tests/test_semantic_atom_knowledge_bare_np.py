"""Bare knowledge NP builder. No new predicates, no topic dictionaries."""

from __future__ import annotations

from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.diff_adapter import (
    OldSnapshot,
    _frame_matches_clause,
    classify_snapshots,
    snapshot_shadow,
)
from calendar_pedagoga.semantic_atom.frame_adapter import full_dispatcher, project_frames
from calendar_pedagoga.semantic_atom.knowledge_builders import KNOWLEDGE_REGISTRY
from calendar_pedagoga.semantic_atom.lexical import (
    TEMPLATE_FUNCTION_WORDS,
    pedagogical_predicates,
    tokenize,
)
from calendar_pedagoga.semantic_atom.models import FrameKind, ObjectStatus
from calendar_pedagoga.semantic_atom.passthrough import DiffKind
from tests.test_semantic_atom_c5_frame_adapter import test_key_w17_two_eligible_atoms_stay_independent
from tests.test_semantic_atom_c5_frame_adapter import test_key_w18_four_knowledge_builders
from tests.test_semantic_atom_c9_control import test_w17_drawings_and_sign_oral
from tests.test_semantic_atom_c9_control import test_w18_stable_oral_survey_of_four_knowledge_frames


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_dispatcher_registers_bare_np() -> None:
    assert "knowledge_bare_np" in full_dispatcher().builder_ids
    assert any(item.builder_id == "knowledge_bare_np" for item in KNOWLEDGE_REGISTRY)


def test_unseen_bare_np_is_theme_independent() -> None:
    for source in (
        "Гомфры квиллинга.",
        "Флумберы на гомфре.",
        "Гомфолина квиллинга.",
    ):
        frame = project_frames(source).frames[0]
        assert frame.status is ObjectStatus.PROVEN, (source, frame.reason)
        assert frame.kind is FrameKind.KNOWLEDGE
        folded = frame.projected_result.casefold()
        assert folded.startswith("характеризует")
        assert "гомфр" in folded or "флумбер" in folded or "гомфолин" in folded


def test_feminine_head_uses_proven_accusative() -> None:
    frame = project_frames("Гомфолина квиллинга.").frames[0]
    assert frame.status is ObjectStatus.PROVEN, frame.reason
    folded = frame.projected_result.casefold()
    assert "гомфолину" in folded
    assert "характеризует гомфолина " not in folded


def test_existing_theory_np_builder_is_unchanged() -> None:
    source = "Правила поведения флумбера на квиллинге."
    projection = project_frames(source)
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert frame.projected_result.casefold().startswith("характеризует")
    builders = {
        item.builder_id
        for item in projection.candidates
        if item.is_valid and item.builder_id.startswith("knowledge_")
    }
    assert "knowledge_noun_phrase" in builders


def test_action_finite_game_event_role_and_open_catalog_stay_out() -> None:
    sources = (
        "Изготовление гомфров.",
        "Рисуют флумберы у ручья.",
        "Игры на поляне: «Зигзаг», «Тихий шепот».",
        "Праздник: «День флумбера».",
        "«Ночь квиллинга».",
        "Ответственные: за флумберы, за квиллинги.",
        "Птицы края: флумберы, квиллинги и другие.",
        "Упражнения на гомфре.",
    )
    for source in sources:
        projection = project_frames(source)
        bare = [
            item
            for item in projection.candidates
            if item.builder_id == "knowledge_bare_np" and item.is_valid
        ]
        assert not bare, source
        if projection.frames[0].status is ObjectStatus.PROVEN:
            assert projection.frames[0].kind is not FrameKind.KNOWLEDGE or (
                "называет" in projection.frames[0].projected_result.casefold()
                and ":" in source
            )


def test_missing_conjunct_is_not_covered() -> None:
    source = "Гомфры квиллинга, изготовление флумберов."
    projection = project_frames(source)
    frame = next(
        item for item in projection.frames if item.status is ObjectStatus.PROVEN
    )
    folded = frame.projected_result.casefold()
    assert folded.startswith("характеризует")
    assert "гомфр" in folded
    assert "изготовлен" not in folded
    assert "флумбер" not in folded
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert _frame_matches_clause("Гомфры квиллинга", proven, shadow)
    assert not _frame_matches_clause("изготовление флумберов", proven, shadow)
    covered = source[proven.span.start : proven.span.end]
    assert "гомфр" in covered.casefold()
    assert "изготовлен" not in covered.casefold()
    assert (proven.span.start, proven.span.end) != (
        projection.atoms[0].span.start,
        projection.atoms[0].span.end,
    )


def test_coordinated_proven_conjuncts_keep_exact_spans() -> None:
    source = "Гомфры квиллинга и флумберы гомфра."
    projection = project_frames(source)
    proven = [item for item in projection.frames if item.status is ObjectStatus.PROVEN]
    assert proven
    shadow = snapshot_shadow(source)
    for frame in proven:
        assert frame.kind is FrameKind.KNOWLEDGE
        assert frame.projected_result.casefold().startswith("характеризует")
        covered = source[frame.span.start : frame.span.end]
        assert covered.strip(" .")
        assert _frame_matches_clause(covered.rstrip("."), frame, shadow)


def test_unproven_form_stays_unresolved() -> None:
    frame = project_frames("Флумбер.").frames[0]
    assert frame.status is ObjectStatus.UNRESOLVED


def test_ambiguous_number_stays_unresolved() -> None:
    frame = project_frames("Города Башкортостана.").frames[0]
    assert frame.status is ObjectStatus.UNRESOLVED
    assert "городу" not in frame.projected_result.casefold()


def test_ambiguous_morphology_without_context_stays_unresolved() -> None:
    """Negative: competing number/case readings must not guess nom/dat."""

    for source in (
        "Города Башкортостана.",
        "Города флумбера.",
        "Города квиллинга.",
        "Городу флумбера.",
    ):
        frame = project_frames(source).frames[0]
        folded = frame.projected_result.casefold()
        assert frame.status is ObjectStatus.UNRESOLVED, source
        assert folded == "", source
        assert "городу" not in folded
        bare = [
            item
            for item in project_frames(source).candidates
            if item.builder_id == "knowledge_bare_np" and item.is_valid
        ]
        assert not bare, source


def test_animate_plural_nom_is_not_kept_as_acc() -> None:
    frame = project_frames("Домашние животные.").frames[0]
    folded = frame.projected_result.casefold()
    if frame.status is ObjectStatus.PROVEN:
        assert "животных" in folded
        assert "характеризует домашние животные" not in folded
    else:
        assert frame.status is ObjectStatus.UNRESOLVED
        assert folded == ""


def test_inanimate_plural_and_feminine_acc_hold() -> None:
    cases = (
        ("Улицы-границы города.", "улицы-границы"),
        ("Памятники прославленным людям.", "памятники"),
        ("Тема весны в литературе.", "тему"),
        ("Профилактика заболеваний.", "профилактику"),
    )
    for source, needle in cases:
        frame = project_frames(source).frames[0]
        assert frame.status is ObjectStatus.PROVEN, source
        folded = frame.projected_result.casefold()
        assert folded.startswith("характеризует")
        assert needle in folded


def test_synthetic_unknown_np_keeps_proven_case() -> None:
    proven_cases = (
        ("Гомфолина квиллинга.", "гомфолину"),
        ("Гомфры квиллинга.", "гомфры"),
        ("Флумберы на гомфре.", "флумберы"),
    )
    for source, needle in proven_cases:
        frame = project_frames(source).frames[0]
        assert frame.status is ObjectStatus.PROVEN, (source, frame.reason)
        folded = frame.projected_result.casefold()
        assert folded.startswith("характеризует")
        assert needle in folded
    unresolved = (
        "Города флумбера.",
        "Города квиллинга.",
    )
    for source in unresolved:
        frame = project_frames(source).frames[0]
        assert frame.status is ObjectStatus.UNRESOLVED, source


def test_neuter_plural_conjunct_is_not_feminized() -> None:
    source = "Реки, озёра, их обитатели."
    projection = project_frames(source)
    folded = " ".join(frame.projected_result.casefold() for frame in projection.frames)
    assert "озёру" not in folded
    assert "озеру" not in folded
    proven = [item for item in projection.frames if item.status is ObjectStatus.PROVEN]
    for frame in proven:
        covered = source[frame.span.start : frame.span.end]
        assert "обитател" not in covered.casefold()


def test_section_and_hours_are_not_knowledge() -> None:
    for source in ("Практика.", "Теория.", "2 часа.", "Содержание."):
        projection = project_frames(source)
        bare = [
            item
            for item in projection.candidates
            if item.builder_id == "knowledge_bare_np" and item.is_valid
        ]
        assert not bare, source
        assert projection.frames[0].status is ObjectStatus.UNRESOLVED, source


def test_w17_and_w18_existing_builders_hold() -> None:
    test_key_w17_two_eligible_atoms_stay_independent()
    test_key_w18_four_knowledge_builders()
    test_w17_drawings_and_sign_oral()
    test_w18_stable_oral_survey_of_four_knowledge_frames()


def test_bare_np_does_not_invent() -> None:
    source = "Гомфры квиллинга."
    shadow = snapshot_shadow(source)
    assert not shadow.lexical_violations
    allowed = {token.casefold() for token in tokenize(source)}
    allowed.update(item.casefold() for item in pedagogical_predicates())
    allowed.update(TEMPLATE_FUNCTION_WORDS)
    for token in tokenize(shadow.result):
        assert token.casefold() in allowed, token
    old = OldSnapshot(
        result=shadow.result,
        control=shadow.control,
        coverage=((source.rstrip("."), "COVERED"),),
        source=source,
    )
    diff = classify_snapshots(old, shadow)
    assert diff.kind is not DiffKind.NEW_INVENTS
