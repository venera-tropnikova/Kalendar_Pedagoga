"""Proven ACTION-object accusative without allow-list expansion."""

from __future__ import annotations

from calendar_pedagoga.content_engine_v2 import _inflect_object_phrase
from calendar_pedagoga.semantic_atom.action_morphology import (
    lemma_bound_to_source,
    proven_action_object_acc,
    proven_noun_acc,
)
from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.action_builders import ACTION_REGISTRY
from calendar_pedagoga.semantic_atom.diff_adapter import snapshot_shadow
from calendar_pedagoga.semantic_atom.frame_adapter import project_frames
from calendar_pedagoga.semantic_atom.lexical import _lemmas
from calendar_pedagoga.semantic_atom.models import FrameKind, ObjectStatus
from calendar_pedagoga.semantic_atom.span_cover import action_cover_text
from tests.test_semantic_atom_named_game_members import (
    test_one_named_game_keeps_source_title,
    test_several_quoted_members_without_colon_are_kept,
)
from tests.test_semantic_atom_list_conjunct import (
    test_three_homogeneous_activity_objects_keep_all_proven_members,
)
from tests.test_semantic_atom_grounded_process_np import (
    test_process_np_with_governed_object_is_action,
    test_vyazka_accusative_is_proven_source_form,
)


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_registry_unchanged() -> None:
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


def _proven_action(source: str):
    projection = project_frames(source)
    return next(
        item for item in projection.frames if item.status is ObjectStatus.PROVEN
    )


def test_velar_needs_proven_sing_gen_context() -> None:
    assert proven_noun_acc("книги") is None
    assert proven_noun_acc("страховки") is None
    assert proven_action_object_acc("книги") is None
    assert proven_action_object_acc("страховки") is None
    assert proven_noun_acc("книги", governed_genitive=True) == "книгу"
    assert proven_noun_acc("страховки", governed_genitive=True) == "страховку"
    assert proven_action_object_acc("книги", governed_genitive=True) == "книгу"
    assert proven_action_object_acc("страховки", governed_genitive=True) == "страховку"
    assert proven_noun_acc("страховки", fem_gen_sg_modifier=True) == "страховку"
    assert "книга" in _lemmas("книгу")
    assert lemma_bound_to_source("книгу", "книги")
    assert lemma_bound_to_source("страховку", "страховки")


def test_third_declension_needs_proven_context() -> None:
    assert proven_noun_acc("модели") is None
    assert proven_action_object_acc("модели") is None
    assert proven_noun_acc("модели", fem_gen_sg_modifier=True) == "модель"
    assert proven_action_object_acc("учебной модели") == "учебную модель"
    assert lemma_bound_to_source("модель", "модели")


def test_parallel_conjunct_proves_singular_genitive() -> None:
    assert proven_noun_acc("цели") is None
    assert proven_action_object_acc("цели и района") == "цель и район"
    assert lemma_bound_to_source("цель", "цели")
    assert lemma_bound_to_source("район", "района")


def test_governor_proves_genitive_for_third_declension() -> None:
    assert proven_noun_acc("цели", governed_genitive=True) == "цель"
    assert proven_action_object_acc("цели", governed_genitive=True) == "цель"


def test_velar_is_not_overwritten_by_soft_sign_heuristic() -> None:
    broken = _inflect_object_phrase("гимнастической страховки", case="acc")
    assert "страховкь" in broken
    proven = proven_action_object_acc("гимнастической страховки")
    assert proven == "гимнастическую страховку"
    assert "страховкь" not in proven
    assert lemma_bound_to_source("страховку", "страховки")


def test_gymnastic_belay_action_uses_proven_accusative() -> None:
    source = "Отработка гимнастической страховки напарника."
    frame = _proven_action(source)
    folded = frame.projected_result.casefold()
    assert frame.kind is FrameKind.ACTION
    assert folded.startswith("отрабатывает")
    assert "гимнастическую страховку напарника" in folded
    assert "страховкь" not in folded
    assert "целу" not in folded
    assert lemma_bound_to_source("страховку", "страховки")
    shadow = snapshot_shadow(source)
    assert shadow.lexical_violations == ()
    assert "страховкь" not in shadow.result.casefold()
    proven = next(item for item in shadow.frames if item.id == frame.id)
    atom = next(item for item in shadow.atoms if item.id == proven.atom_id)
    covered = source[proven.span.start : proven.span.end]
    assert covered == source
    assert proven.span.start == atom.span.start
    assert proven.span.end == atom.span.end
    assert proven.source_fingerprint == atom.source_fingerprint
    binding = next(
        item
        for item in shadow.bindings
        if item.span.start == proven.span.start and item.span.end == proven.span.end
    )
    assert binding.atom_id == atom.id
    assert binding.frame_id == proven.id
    assert action_cover_text(atom.text, proven)
    builders = {
        item.builder_id
        for item in project_frames(source).candidates
        if item.is_valid
    }
    assert "explicit_action" in builders


def test_goal_and_area_action_uses_proven_accusative() -> None:
    source = "Определение цели и района похода."
    frame = _proven_action(source)
    folded = frame.projected_result.casefold()
    assert frame.kind is FrameKind.ACTION
    assert folded.startswith("определяет")
    assert "цель и район похода" in folded
    assert "целу" not in folded
    assert "страховкь" not in folded
    assert lemma_bound_to_source("цель", "цели")
    shadow = snapshot_shadow(source)
    assert shadow.lexical_violations == ()
    assert "целу" not in shadow.result.casefold()
    proven = next(item for item in shadow.frames if item.id == frame.id)
    atom = next(item for item in shadow.atoms if item.id == proven.atom_id)
    assert source[proven.span.start : proven.span.end] == source
    assert proven.span.start == atom.span.start
    assert proven.span.end == atom.span.end
    assert proven.source_fingerprint == atom.source_fingerprint
    binding = next(
        item
        for item in shadow.bindings
        if item.span.start == proven.span.start and item.span.end == proven.span.end
    )
    assert binding.atom_id == atom.id
    assert binding.frame_id == proven.id


def test_book_object_stays_first_declension_accusative() -> None:
    source = "Изучение книги."
    frame = _proven_action(source)
    assert "книгу" in frame.projected_result.casefold()
    assert "книги" not in frame.projected_result.casefold().split()
    assert lemma_bound_to_source("книгу", "книги")


def test_agreed_model_object_is_third_declension_accusative() -> None:
    source = "Сборка учебной модели."
    frame = _proven_action(source)
    folded = frame.projected_result.casefold()
    assert "учебную модель" in folded
    assert "моделу" not in folded
    assert lemma_bound_to_source("модель", "модели")


def test_bare_model_without_context_stays_unresolved() -> None:
    source = "Модели."
    projection = project_frames(source)
    action = [
        item
        for item in projection.candidates
        if item.builder_id == "explicit_action" and item.is_valid
    ]
    assert not action
    assert proven_action_object_acc("модели") is None
    results = " ".join(item.projected_result or "" for item in projection.frames)
    assert "моделу" not in results.casefold()
    assert "целу" not in results.casefold()


def test_nominative_plural_subject_is_not_action() -> None:
    source = "Страховки работают."
    projection = project_frames(source)
    action = [
        item
        for item in projection.candidates
        if item.builder_id == "explicit_action" and item.is_valid
    ]
    assert not action
    results = " ".join(item.projected_result or "" for item in projection.frames)
    assert "страховку" not in results.casefold().split()


def test_plural_object_is_not_collapsed_to_singular() -> None:
    assert proven_noun_acc("страховки") is None
    assert proven_action_object_acc("страховки") is None
    source = "Проверяет страховки."
    from calendar_pedagoga.semantic_atom.action_builders import _explicit_action

    built = _explicit_action(source)
    if built is not None:
        assert "страховку" not in built[0].casefold().split()
    projection = project_frames(source)
    explicit = [
        item
        for item in projection.candidates
        if item.builder_id == "explicit_action" and item.is_valid
    ]
    assert not explicit
    results = " ".join(item.projected_result or "" for item in explicit)
    assert "страховку" not in results.casefold().split()


def test_malformed_forms_are_absent_from_shadow() -> None:
    for source in (
        "Отработка гимнастической страховки напарника.",
        "Определение цели и района похода.",
    ):
        shadow = snapshot_shadow(source)
        folded = shadow.result.casefold()
        assert "страховкь" not in folded
        assert "целу" not in folded
        assert shadow.lexical_violations == ()


def test_process_np_regression() -> None:
    test_process_np_with_governed_object_is_action()
    test_vyazka_accusative_is_proven_source_form()


def test_list_conjunct_regression() -> None:
    test_three_homogeneous_activity_objects_keep_all_proven_members()


def test_named_game_regression() -> None:
    test_one_named_game_keeps_source_title()
    test_several_quoted_members_without_colon_are_kept()


def test_lexical_gate_still_rejects_unbound_lexeme() -> None:
    source = "Отработка гимнастической страховки напарника."
    shadow = snapshot_shadow(source)
    assert shadow.lexical_violations == ()
    from calendar_pedagoga.semantic_atom.lexical import shadow_lexical_violations

    invented = shadow_lexical_violations(
        source=source,
        result="Отрабатывает гимнастическую страховкь напарника.",
        control="",
    )
    assert "страховкь" in invented


def test_process_wrapper_and_coordinated_heads_are_not_stripped() -> None:
    wrapped = project_frames("Комплектование аптечки.").frames[0]
    assert wrapped.status is ObjectStatus.PROVEN
    assert "комплектование" in wrapped.projected_result.casefold()
    assert "аптечку" not in wrapped.projected_result.casefold() or "комплектован" in wrapped.projected_result.casefold()
    coordinated = project_frames(
        "Изучение и отработка основных технических приемов: диагональный шаг."
    )
    results = " ".join(item.projected_result for item in coordinated.frames).casefold()
    assert "отрабатывает" in results or "изучает и отрабатывает" in results
