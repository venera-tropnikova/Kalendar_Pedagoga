"""Shadow FrameAdapter: C5 builders registered on the C6 dispatcher."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import replace
from typing import Protocol

from calendar_pedagoga import content_engine_v2 as _ce2
from calendar_pedagoga.semantic_atom.atom_adapter import atomize, identity_fields
from calendar_pedagoga.semantic_atom.dispatcher import (
    BUILDER_ERROR,
    NO_STRUCTURAL_MATCH,
    SemanticFrameDispatcher,
    UNSUPPORTED_ATOM_SHAPE,
    RegisteredBuilder,
)
from calendar_pedagoga.semantic_atom.span_cover import narrow_action_frame
from calendar_pedagoga.semantic_atom.lexical import (
    TEMPLATE_FUNCTION_WORDS,
    _allowed_lemmas,
    _lemmas,
    pedagogical_predicates,
    shadow_lexical_violations,
    tokenize,
)
from calendar_pedagoga.semantic_atom.models import (
    CandidateConfidence,
    FrameCandidate,
    FrameKind,
    FrameProjection,
    LexicalCheckResult,
    ObjectStatus,
    SourceAtom,
    StructuralEvidence,
)

ADAPTER_NAME = "frame_c5"
AMBIGUOUS_ATOM_SHAPE = "ambiguous_atom_shape"
AMBIGUOUS_FRAME_CANDIDATES = "ambiguous_frame_candidates"
LEXICAL_VIOLATION = "lexical_violation"
UNTRACED_COMPLEMENT = "untraced_object_or_complement"
UNKNOWN_PREDICATE = "predicate_not_in_registry"

_FRAME_ADAPTER_CALLS = 0
_CATALOG_SELECTOR: ContextVar[str] = ContextVar(
    "ce2_action_catalog_selector", default=""
)

_Helper = Callable[[str], tuple[str, str, str, str] | None]


def catalog_selector() -> str:
    """ScheduleRow title used only as a SOURCE-span member selector."""

    return _CATALOG_SELECTOR.get()


def _row_catalog_selector(row: object | None) -> str:
    if row is None:
        return ""
    origin = getattr(row, "source", None)
    if origin is not None:
        return str(getattr(origin, "topic_title", "") or "")
    return str(getattr(row, "topic_title", "") or "")


class _IdentityRow(Protocol):
    lesson_type: str
    planned_result: str
    assessment_method: str


def frame_adapter_calls() -> int:
    return _FRAME_ADAPTER_CALLS


def reset_frame_adapter_calls() -> None:
    global _FRAME_ADAPTER_CALLS
    _FRAME_ADAPTER_CALLS = 0


def _locative_builder(text: str) -> tuple[str, str, str, str] | None:
    return _ce2._locative_drawing_result(text)


def _semiotic_builder(text: str) -> tuple[str, str, str, str] | None:
    return _ce2._semiotic_object_result(text)


def _concept_builder(text: str) -> tuple[str, str, str, str] | None:
    return _ce2._concept_values_clause_result(text)


def _purpose_builder(text: str) -> tuple[str, str, str, str] | None:
    return _ce2._purpose_clause_result(text)


def _classification_builder(text: str) -> tuple[str, str, str, str] | None:
    return _ce2._classification_clause_result(text, theory_only=True)


def _symbol_builder(text: str) -> tuple[str, str, str, str] | None:
    return _ce2._symbol_clause_result(text)


def _c5_candidate(
    builder_id: str,
    kind: FrameKind,
    helper: _Helper,
    atom: SourceAtom,
) -> FrameCandidate:
    built = helper(atom.text)
    if not built:
        return FrameCandidate(
            builder_id=builder_id,
            atom_id=atom.id,
            span=atom.span,
            source_fingerprint=atom.source_fingerprint,
            proposed_kind=kind,
            proposed_predicate="",
            proposed_object="",
            proposed_complement="",
            proposed_result="",
            proposed_control="",
            structural_evidence=StructuralEvidence(notes=(NO_STRUCTURAL_MATCH,)),
            lexical_check=LexicalCheckResult(passed=True),
            confidence=CandidateConfidence.REJECTED,
            rejection_reason=NO_STRUCTURAL_MATCH,
        )
    phrase, action, obj, complement = built
    predicate = _closed_predicate(action, phrase)
    if not predicate:
        return _rejected(
            builder_id,
            kind,
            atom,
            phrase=phrase,
            predicate=action,
            obj=obj,
            complement=complement,
            reason=UNKNOWN_PREDICATE,
        )
    if not _traces_to_source(obj, atom.text) or not _traces_to_source(
        complement, atom.text
    ):
        return _rejected(
            builder_id,
            kind,
            atom,
            phrase=phrase,
            predicate=predicate,
            obj=obj,
            complement=complement,
            reason=UNTRACED_COMPLEMENT,
        )
    result = _ce2._cap_sentence(phrase)
    control = _ce2._declared_knowledge_control(result) or _ce2._declared_product_control(
        result
    )
    violations = shadow_lexical_violations(
        source=atom.text,
        result=result,
        control=control,
        extra_allowed=("его", "её", "ее", "их", "готовой", "работы"),
    )
    if violations:
        return _rejected(
            builder_id,
            kind,
            atom,
            phrase=result,
            predicate=predicate,
            obj=obj,
            complement=complement,
            reason=LEXICAL_VIOLATION,
            control=control,
            violations=violations,
        )
    return FrameCandidate(
        builder_id=builder_id,
        atom_id=atom.id,
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        proposed_kind=kind,
        proposed_predicate=predicate,
        proposed_object=obj,
        proposed_complement=complement,
        proposed_result=result,
        proposed_control=control,
        structural_evidence=StructuralEvidence(notes=(builder_id,)),
        lexical_check=LexicalCheckResult(passed=True),
        confidence=CandidateConfidence.VALID,
    )


def _rejected(
    builder_id: str,
    kind: FrameKind,
    atom: SourceAtom,
    *,
    phrase: str,
    predicate: str,
    obj: str,
    complement: str,
    reason: str,
    control: str = "",
    violations: tuple[str, ...] = (),
) -> FrameCandidate:
    return FrameCandidate(
        builder_id=builder_id,
        atom_id=atom.id,
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        proposed_kind=kind,
        proposed_predicate=predicate,
        proposed_object=obj,
        proposed_complement=complement,
        proposed_result=phrase,
        proposed_control=control,
        structural_evidence=StructuralEvidence(notes=(reason,)),
        lexical_check=LexicalCheckResult(
            passed=not violations, violations=violations
        ),
        confidence=CandidateConfidence.REJECTED,
        rejection_reason=reason,
    )


def _closed_predicate(action: str, phrase: str) -> str:
    registry = {item.casefold() for item in pedagogical_predicates()}
    if action and action.casefold() in registry:
        return action
    finite = _ce2._leading_finite_verb(phrase)
    if finite and finite.casefold() in registry:
        return finite
    return ""


def _traces_to_source(fragment: str, source: str) -> bool:
    if not fragment.strip():
        return True
    allowed = set(_allowed_lemmas(source))
    allowed.update(word.casefold() for word in pedagogical_predicates())
    allowed.update(TEMPLATE_FUNCTION_WORDS)
    for token in tokenize(fragment):
        folded = token.casefold()
        if folded in allowed or _lemmas(token) & allowed:
            continue
        return False
    return True


def _register(builder_id: str, kind: FrameKind, helper: _Helper) -> RegisteredBuilder:
    def propose(atom: SourceAtom) -> FrameCandidate:
        return _c5_candidate(builder_id, kind, helper, atom)

    return RegisteredBuilder(builder_id=builder_id, propose=propose)


C5_REGISTRY: tuple[RegisteredBuilder, ...] = (
    _register("locative_drawing", FrameKind.ACTION, _locative_builder),
    _register("semiotic_object", FrameKind.KNOWLEDGE, _semiotic_builder),
    _register("concept_values", FrameKind.KNOWLEDGE, _concept_builder),
    _register("purpose_functions", FrameKind.KNOWLEDGE, _purpose_builder),
    _register("closed_classification", FrameKind.CLASSIFICATION, _classification_builder),
    _register("dash_symbol", FrameKind.DEFINITION, _symbol_builder),
)


def default_dispatcher() -> SemanticFrameDispatcher:
    return SemanticFrameDispatcher(C5_REGISTRY)


def full_dispatcher() -> SemanticFrameDispatcher:
    from calendar_pedagoga.semantic_atom.action_builders import ACTION_REGISTRY
    from calendar_pedagoga.semantic_atom.knowledge_builders import KNOWLEDGE_REGISTRY

    return SemanticFrameDispatcher(
        (*C5_REGISTRY, *ACTION_REGISTRY, *KNOWLEDGE_REGISTRY)
    )


def project_frames(
    source: object,
    row: _IdentityRow | None = None,
    dispatcher: SemanticFrameDispatcher | None = None,
) -> FrameProjection:
    """Build candidate shadow frames. Never writes TYPE/RESULT/CONTROL."""

    global _FRAME_ADAPTER_CALLS
    _FRAME_ADAPTER_CALLS += 1
    atoms = atomize(source)
    active = dispatcher or full_dispatcher()
    frames = []
    bindings = []
    candidates = []
    proven: list[str] = []
    selector_token = _CATALOG_SELECTOR.set(_row_catalog_selector(row))
    try:
        for index, atom in enumerate(atoms.atoms):
            decision = active.dispatch_atom(atom, index)
            frame = narrow_action_frame(atom, decision.frame)
            binding = (
                replace(decision.binding, span=frame.span)
                if frame.span != decision.frame.span
                else decision.binding
            )
            frames.append(frame)
            bindings.append(binding)
            candidates.extend(decision.candidates)
            if (
                decision.frame.status is ObjectStatus.PROVEN
                and decision.frame.projected_result
            ):
                proven.append(decision.frame.projected_result.rstrip("."))
    finally:
        _CATALOG_SELECTOR.reset(selector_token)
    identity_type = identity_result = identity_control = ""
    if row is not None:
        identity_type, identity_result, identity_control = identity_fields(row)
    status = (
        ObjectStatus.PROVEN
        if any(frame.status is ObjectStatus.PROVEN for frame in frames)
        else ObjectStatus.UNRESOLVED
        if frames or atoms.status is ObjectStatus.UNRESOLVED
        else ObjectStatus.UNASSESSED
    )
    return FrameProjection(
        source=atoms.source,
        atoms=atoms.atoms,
        frames=tuple(frames),
        bindings=tuple(bindings),
        candidate_result=". ".join(proven),
        identity_type=identity_type,
        identity_result=identity_result,
        identity_control=identity_control,
        status=status,
        candidates=tuple(candidates),
    )
