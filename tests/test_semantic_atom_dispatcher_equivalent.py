"""Exact equivalence and grounded superset for dispatcher candidates."""

from __future__ import annotations

from dataclasses import replace
from itertools import permutations

from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.atom_adapter import atomize
from calendar_pedagoga.semantic_atom.dispatcher import (
    AMBIGUOUS_FRAME_CANDIDATES,
    OPEN_TAIL_UNRESOLVED,
    SemanticFrameDispatcher,
    RegisteredBuilder,
)
from calendar_pedagoga.semantic_atom.frame_adapter import project_frames
from calendar_pedagoga.semantic_atom.models import (
    CandidateConfidence,
    FrameCandidate,
    FrameKind,
    LexicalCheckResult,
    ObjectStatus,
    SourceAtom,
    StructuralEvidence,
)
from tests.test_semantic_atom_action_catalog import (
    test_several_members_and_permutation,
    test_unseen_exercise_game_excursion_and_making,
)
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
from tests.test_semantic_atom_action_accusative_proof import (
    test_gymnastic_belay_action_uses_proven_accusative,
    test_goal_and_area_action_uses_proven_accusative,
    test_bare_model_without_context_stays_unresolved,
)


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def _atom(source: str) -> SourceAtom:
    return atomize(source).atoms[0]


def _candidate(
    atom: SourceAtom,
    builder_id: str,
    *,
    kind: FrameKind = FrameKind.ACTION,
    predicate: str = "выполняет",
    obj: str = "катание",
    complement: str = "",
    result: str = "Выполняет катание на санках.",
    span=None,
    valid: bool = True,
    reason: str = "",
    violations: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
    specificity: int = 0,
) -> FrameCandidate:
    confidence = (
        CandidateConfidence.VALID if valid else CandidateConfidence.REJECTED
    )
    return FrameCandidate(
        builder_id=builder_id,
        atom_id=atom.id,
        span=span or atom.span,
        source_fingerprint=atom.source_fingerprint,
        proposed_kind=kind,
        proposed_predicate=predicate,
        proposed_object=obj,
        proposed_complement=complement,
        proposed_result=result,
        proposed_control="",
        structural_evidence=StructuralEvidence(
            notes=notes or (builder_id,), specificity=specificity
        ),
        lexical_check=LexicalCheckResult(
            passed=not violations, violations=violations
        ),
        confidence=confidence,
        rejection_reason=reason,
    )


def _const(candidate: FrameCandidate) -> RegisteredBuilder:
    return RegisteredBuilder(
        builder_id=candidate.builder_id,
        propose=lambda _atom, saved=candidate: saved,
        specificity=candidate.structural_evidence.specificity,
    )


def _dispatch(atom: SourceAtom, *candidates: FrameCandidate, prove_segment=None):
    builders = tuple(_const(item) for item in candidates)
    return SemanticFrameDispatcher(
        builders, prove_segment=prove_segment
    ).dispatch_atom(atom)


def _prove_only(*allowed: str):
    accepted = {item.casefold().strip(" .") for item in allowed}

    def prove(segment: str, finite: str, kind: str) -> bool:
        return (
            segment.casefold().strip(" .") in accepted
            and finite == "выполняет"
            and kind == "ACTION"
        )

    return prove


def test_same_action_result_from_two_builders_is_one_frame() -> None:
    atom = _atom("Тренировка на выносливость.")
    left = _candidate(
        atom,
        "explicit_action",
        predicate="отрабатывает",
        obj="",
        result="Отрабатывает на выносливость.",
    )
    right = _candidate(
        atom,
        "nominal_activity",
        predicate="отработка",
        obj="",
        result="Отрабатывает на выносливость.",
    )
    decision = _dispatch(atom, right, left)
    assert decision.frame.status is ObjectStatus.PROVEN
    assert decision.frame.projected_result == "Отрабатывает на выносливость."
    assert "explicit_action" in decision.frame.provenance.note
    assert "nominal_activity" in decision.frame.provenance.note


def test_proposal_and_builder_order_is_deterministic() -> None:
    atom = _atom("Тренировка на выносливость.")
    left = _candidate(
        atom,
        "explicit_action",
        predicate="отрабатывает",
        result="Отрабатывает на выносливость.",
        obj="",
        specificity=0,
    )
    right = _candidate(
        atom,
        "nominal_activity",
        predicate="отработка",
        result="Отрабатывает на выносливость.",
        obj="",
        specificity=1,
    )
    seen = set()
    for order in permutations((left, right)):
        decision = _dispatch(atom, *order)
        seen.add(
            (
                decision.frame.status,
                decision.frame.projected_result,
                decision.frame.kind,
                decision.frame.id,
            )
        )
    assert len(seen) == 1


def test_category_title_after_dash_is_not_a_superset() -> None:
    atom = _atom("Любимые зимние развлечения – катание на санках, на коньках")
    short = _candidate(
        atom,
        "nominal_activity",
        predicate="выполняет",
        obj="",
        result="Выполняет катание на санках, на коньках.",
    )
    full = _candidate(
        atom,
        "explicit_action",
        predicate="выполняет",
        obj="– катание",
        complement="на санках, на коньках",
        result="Выполняет любимые зимние развлечения – катание на санках, на коньках.",
    )
    decision = _dispatch(atom, short, full, prove_segment=_prove_only())
    assert decision.frame.status is ObjectStatus.UNRESOLVED
    assert decision.frame.reason == AMBIGUOUS_FRAME_CANDIDATES
    assert decision.frame.projected_result == ""


def test_source_grounded_extra_tokens_are_not_enough() -> None:
    atom = _atom("Любимые зимние развлечения – катание на санках, на коньках")
    short = _candidate(
        atom,
        "nominal_activity",
        predicate="выполняет",
        result="Выполняет катание на санках, на коньках.",
    )
    full = _candidate(
        atom,
        "explicit_action",
        predicate="выполняет",
        result="Выполняет любимые зимние развлечения – катание на санках, на коньках.",
    )
    decision = _dispatch(atom, short, full)
    assert decision.frame.status is ObjectStatus.UNRESOLVED
    assert decision.frame.reason == AMBIGUOUS_FRAME_CANDIDATES


def test_added_conjunct_needs_independent_same_predicate() -> None:
    atom = _atom("Отрабатывание страховки и лазание по простым трассам (по 3 раза).")
    short = _candidate(
        atom,
        "nominal_activity",
        predicate="выполняет",
        obj="",
        result="Выполняет лазание по простым трассам (по 3 раза).",
    )
    full = _candidate(
        atom,
        "explicit_action",
        predicate="выполняет",
        obj="страховки",
        complement="по простым трассам (по 3 раза)",
        result="Выполняет отрабатывание страховки, лазание по простым трассам (по 3 раза).",
    )
    denied = _dispatch(atom, short, full, prove_segment=_prove_only())
    assert denied.frame.status is ObjectStatus.UNRESOLVED
    proven = _dispatch(
        atom,
        short,
        full,
        prove_segment=_prove_only("Отрабатывание страховки"),
    )
    assert proven.frame.status is ObjectStatus.PROVEN
    folded = proven.frame.projected_result.casefold()
    assert "страховк" in folded
    assert "лазание" in folded


def test_safe_homogeneous_superset_is_kept() -> None:
    atom = _atom("Отрабатывание страховки и лазание по простым трассам (по 3 раза).")
    short = _candidate(
        atom,
        "unconjugated_practice",
        predicate="выполняет",
        result="Выполняет лазание по простым трассам (по 3 раза).",
    )
    full = _candidate(
        atom,
        "explicit_action",
        predicate="выполняет",
        result="Выполняет отрабатывание страховки, лазание по простым трассам (по 3 раза).",
    )
    seen = set()
    prover = _prove_only("Отрабатывание страховки")
    for order in permutations((short, full)):
        decision = _dispatch(atom, *order, prove_segment=prover)
        seen.add((decision.frame.status, decision.frame.projected_result))
    assert seen == {
        (
            ObjectStatus.PROVEN,
            "Выполняет отрабатывание страховки, лазание по простым трассам (по 3 раза).",
        )
    }


def test_different_predicates_stay_ambiguous() -> None:
    atom = _atom("Анализ ситуаций, игры – фантазии.")
    analysis = _candidate(
        atom,
        "explicit_action",
        predicate="анализ",
        obj="ситуаций",
        result="Анализирует ситуации.",
    )
    games = _candidate(
        atom,
        "closed_form_activity",
        predicate="участвует",
        obj="играх – фантазии",
        result="Участвует в играх – фантазии.",
    )
    decision = _dispatch(atom, analysis, games)
    assert decision.frame.status is ObjectStatus.UNRESOLVED
    assert decision.frame.reason == AMBIGUOUS_FRAME_CANDIDATES
    assert decision.frame.projected_result == ""


def test_different_kinds_stay_ambiguous() -> None:
    atom = _atom("Как город строился – первые улицы города.")
    question = _candidate(
        atom,
        "knowledge_question",
        kind=FrameKind.KNOWLEDGE,
        predicate="объясняет",
        obj="город строился – первые улицы города",
        result="Объясняет, как город строился – первые улицы города.",
    )
    definition = _candidate(
        atom,
        "knowledge_definition",
        kind=FrameKind.DEFINITION,
        predicate="объясняет",
        obj="Как город строился",
        result="Объясняет как город строился – первые улицы города.",
    )
    decision = _dispatch(atom, question, definition)
    assert decision.frame.status is ObjectStatus.UNRESOLVED
    assert decision.frame.reason == AMBIGUOUS_FRAME_CANDIDATES


def test_wrapper_versus_conjugated_stays_ambiguous() -> None:
    atom = _atom("Тренировка переноса веса на ноги.")
    wrapper = _candidate(
        atom,
        "unconjugated_practice",
        predicate="выполняет",
        obj="переноса веса",
        result="Выполняет тренировку переноса веса на ноги.",
    )
    conjugated = _candidate(
        atom,
        "explicit_action",
        predicate="отрабатывает",
        obj="переноса веса",
        result="Отрабатывает перенос веса на ноги.",
    )
    decision = _dispatch(atom, wrapper, conjugated)
    assert decision.frame.status is ObjectStatus.UNRESOLVED
    assert decision.frame.reason == AMBIGUOUS_FRAME_CANDIDATES


def test_incomparable_objects_stay_ambiguous() -> None:
    atom = _atom("Животные и птицы в рисунках детей.")
    animals = _candidate(
        atom,
        "left",
        predicate="рисует",
        obj="животных",
        result="Рисует животных.",
    )
    birds = _candidate(
        atom,
        "right",
        predicate="рисует",
        obj="птиц",
        result="Рисует птиц.",
    )
    decision = _dispatch(atom, animals, birds)
    assert decision.frame.status is ObjectStatus.UNRESOLVED
    assert decision.frame.reason == AMBIGUOUS_FRAME_CANDIDATES


def test_different_span_is_not_merged() -> None:
    atom = _atom("Любимые зимние развлечения – катание на санках, на коньках")
    full = _candidate(
        atom,
        "explicit_action",
        predicate="выполняет",
        result="Выполняет любимые зимние развлечения – катание на санках, на коньках.",
    )
    narrowed = replace(atom.span, end=max(atom.span.start + 8, atom.span.start + 1))
    short = _candidate(
        atom,
        "nominal_activity",
        predicate="выполняет",
        obj="",
        result="Выполняет катание на санках, на коньках.",
        span=narrowed,
    )
    decision = _dispatch(atom, full, short)
    assert decision.frame.status is ObjectStatus.UNRESOLVED
    assert decision.frame.reason == AMBIGUOUS_FRAME_CANDIDATES


def test_open_tail_does_not_dominate() -> None:
    atom = _atom("Любимые зимние развлечения – катание на санках, на коньках")
    short = _candidate(
        atom,
        "nominal_activity",
        predicate="выполняет",
        result="Выполняет катание на санках, на коньках.",
    )
    opened = _candidate(
        atom,
        "explicit_action",
        predicate="выполняет",
        result="Выполняет любимые зимние развлечения – катание на санках, на коньках и другие.",
        notes=(OPEN_TAIL_UNRESOLVED,),
    )
    decision = _dispatch(atom, short, opened)
    assert decision.frame.status is ObjectStatus.UNRESOLVED
    assert decision.frame.reason == AMBIGUOUS_FRAME_CANDIDATES


def test_lexical_violation_does_not_dominate() -> None:
    atom = _atom("Любимые зимние развлечения – катание на санках, на коньках")
    short = _candidate(
        atom,
        "nominal_activity",
        predicate="выполняет",
        result="Выполняет катание на санках, на коньках.",
    )
    leaky = _candidate(
        atom,
        "explicit_action",
        predicate="выполняет",
        result="Выполняет любимые зимние развлечения – катание на санках, на коньках.",
        violations=("квантовый",),
    )
    decision = _dispatch(atom, short, leaky)
    assert decision.frame.status is ObjectStatus.PROVEN
    assert decision.frame.projected_result == short.proposed_result


def test_live_endurance_training_is_proven() -> None:
    frame = project_frames("Тренировка на выносливость.").frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert "отрабатывает на выносливость" in frame.projected_result.casefold()


def test_live_winter_fun_stays_ambiguous() -> None:
    source = "Любимые зимние развлечения – катание на санках, на коньках."
    frame = project_frames(source).frames[0]
    assert frame.status is ObjectStatus.UNRESOLVED
    assert frame.reason == AMBIGUOUS_FRAME_CANDIDATES
    assert frame.projected_result == ""
    assert "любимые зимние развлечения" not in frame.projected_result.casefold()


def test_live_belay_and_climbing_keeps_both_members() -> None:
    source = "Отрабатывание страховки и лазание по простым трассам (по 3 раза)."
    frame = project_frames(source).frames[0]
    folded = frame.projected_result.casefold()
    assert frame.status is ObjectStatus.PROVEN
    assert "страховк" in folded
    assert "лазание" in folded


def test_live_analysis_and_fantasy_games_stay_ambiguous() -> None:
    frame = project_frames("Анализ ситуаций, игры – фантазии.").frames[0]
    assert frame.status is ObjectStatus.UNRESOLVED
    assert frame.reason == AMBIGUOUS_FRAME_CANDIDATES
    hyphen = project_frames("Анализ ситуаций, игры-фантазии.")
    valids = [item for item in hyphen.candidates if item.is_valid]
    if len(valids) > 1:
        assert hyphen.frames[0].status is ObjectStatus.UNRESOLVED
        assert hyphen.frames[0].reason == AMBIGUOUS_FRAME_CANDIDATES
    else:
        assert hyphen.frames[0].status is ObjectStatus.PROVEN
        assert hyphen.frames[0].projected_result.startswith("Анализирует")


def test_live_city_question_versus_definition_stays_ambiguous() -> None:
    frame = project_frames("Как город строился – первые улицы города.").frames[0]
    assert frame.status is ObjectStatus.UNRESOLVED
    assert frame.reason == AMBIGUOUS_FRAME_CANDIDATES


def test_action_catalog_regression() -> None:
    test_unseen_exercise_game_excursion_and_making()
    test_several_members_and_permutation()


def test_named_game_regression() -> None:
    test_one_named_game_keeps_source_title()
    test_several_quoted_members_without_colon_are_kept()


def test_list_conjunct_regression() -> None:
    test_three_homogeneous_activity_objects_keep_all_proven_members()


def test_process_np_regression() -> None:
    test_process_np_with_governed_object_is_action()
    test_vyazka_accusative_is_proven_source_form()


def test_accusative_proof_regression() -> None:
    test_gymnastic_belay_action_uses_proven_accusative()
    test_goal_and_area_action_uses_proven_accusative()
    test_bare_model_without_context_stays_unresolved()
