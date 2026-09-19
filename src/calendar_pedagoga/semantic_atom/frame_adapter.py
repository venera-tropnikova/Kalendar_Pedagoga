"""Shadow FrameAdapter: proven builders from be1b745 / 7a714d6 only."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from calendar_pedagoga import content_engine_v2 as _ce2
from calendar_pedagoga.semantic_atom.atom_adapter import atomize, identity_fields
from calendar_pedagoga.semantic_atom.lexical import (
    TEMPLATE_FUNCTION_WORDS,
    _allowed_lemmas,
    _lemmas,
    pedagogical_predicates,
    shadow_lexical_violations,
    tokenize,
)
from calendar_pedagoga.semantic_atom.models import (
    CoverageBinding,
    FrameKind,
    FrameProjection,
    ObjectStatus,
    Provenance,
    SemanticFrame,
    SourceAtom,
    make_object_id,
)

ADAPTER_NAME = "frame_c5"
UNSUPPORTED_ATOM_SHAPE = "unsupported_atom_shape"
AMBIGUOUS_ATOM_SHAPE = "ambiguous_atom_shape"
LEXICAL_VIOLATION = "lexical_violation"
UNTRACED_COMPLEMENT = "untraced_object_or_complement"
UNKNOWN_PREDICATE = "predicate_not_in_registry"

_FRAME_ADAPTER_CALLS = 0

_Builder = Callable[[str], tuple[str, str, str, str] | None]


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


_BUILDERS: tuple[tuple[str, FrameKind, _Builder], ...] = (
    ("locative_drawing", FrameKind.ACTION, _locative_builder),
    ("semiotic_object", FrameKind.KNOWLEDGE, _semiotic_builder),
    ("concept_values", FrameKind.KNOWLEDGE, _concept_builder),
    ("purpose_functions", FrameKind.KNOWLEDGE, _purpose_builder),
    ("closed_classification", FrameKind.CLASSIFICATION, _classification_builder),
    ("dash_symbol", FrameKind.DEFINITION, _symbol_builder),
)


def project_frames(
    source: object,
    row: _IdentityRow | None = None,
) -> FrameProjection:
    """Build candidate shadow frames. Never writes TYPE/RESULT/CONTROL."""

    global _FRAME_ADAPTER_CALLS
    _FRAME_ADAPTER_CALLS += 1
    atoms = atomize(source)
    frames: list[SemanticFrame] = []
    bindings: list[CoverageBinding] = []
    proven: list[str] = []
    for index, atom in enumerate(atoms.atoms):
        frame = _frame_for_atom(atom, index)
        binding = _binding_for(atom, frame, index)
        frames.append(frame)
        bindings.append(binding)
        if frame.status is ObjectStatus.PROVEN and frame.projected_result:
            proven.append(frame.projected_result.rstrip("."))
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
    )


def _frame_for_atom(atom: SourceAtom, index: int) -> SemanticFrame:
    hits: list[tuple[str, FrameKind, tuple[str, str, str, str]]] = []
    for name, kind, builder in _BUILDERS:
        built = builder(atom.text)
        if built:
            hits.append((name, kind, built))
    if len(hits) != 1:
        reason = UNSUPPORTED_ATOM_SHAPE if not hits else AMBIGUOUS_ATOM_SHAPE
        return _unresolved(atom, index, reason)
    name, kind, (phrase, action, obj, complement) = hits[0]
    predicate = _closed_predicate(action, phrase)
    if not predicate:
        return _unresolved(atom, index, UNKNOWN_PREDICATE)
    if not _traces_to_source(obj, atom.text) or not _traces_to_source(
        complement, atom.text
    ):
        return _unresolved(atom, index, UNTRACED_COMPLEMENT)
    result = _ce2._cap_sentence(phrase)
    control = _ce2._declared_knowledge_control(result) or _ce2._declared_product_control(
        result
    )
    violations = shadow_lexical_violations(
        source=atom.text,
        result=result,
        control=control,
        extra_allowed=("его", "её", "ее", "их"),
    )
    if violations:
        return _unresolved(atom, index, LEXICAL_VIOLATION)
    return _proven(
        atom,
        index,
        kind=kind,
        builder=name,
        result=result,
        control=control,
        predicate=predicate,
        obj=obj,
        complement=complement,
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


def _proven(
    atom: SourceAtom,
    index: int,
    *,
    kind: FrameKind,
    builder: str,
    result: str,
    control: str,
    predicate: str,
    obj: str,
    complement: str,
) -> SemanticFrame:
    return SemanticFrame(
        id=make_object_id("frame", atom.id, result, "proven", builder),
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        provenance=Provenance(
            adapter=ADAPTER_NAME,
            role="frame",
            clause_index=index,
            note=builder,
        ),
        status=ObjectStatus.PROVEN,
        kind=kind,
        atom_id=atom.id,
        clause_id=atom.clause_id,
        projected_type="",
        projected_result=result,
        projected_control=control,
        coverage_status="COVERED",
        predicate=predicate,
        object=obj,
        complement=complement,
        reason="",
    )


def _unresolved(atom: SourceAtom, index: int, reason: str) -> SemanticFrame:
    return SemanticFrame(
        id=make_object_id("frame", atom.id, reason, "unresolved"),
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        provenance=Provenance(
            adapter=ADAPTER_NAME,
            role="frame",
            clause_index=index,
            note=reason,
        ),
        status=ObjectStatus.UNRESOLVED,
        kind=FrameKind.PROJECTED,
        atom_id=atom.id,
        clause_id=atom.clause_id,
        projected_type="",
        projected_result="",
        projected_control="",
        coverage_status="UNRESOLVED",
        reason=reason,
    )


def _binding_for(atom: SourceAtom, frame: SemanticFrame, index: int) -> CoverageBinding:
    covered = frame.status is ObjectStatus.PROVEN
    return CoverageBinding(
        id=make_object_id("binding", atom.id, frame.id),
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        provenance=Provenance(
            adapter=ADAPTER_NAME,
            role="binding",
            clause_index=index,
        ),
        status=ObjectStatus.COVERED if covered else ObjectStatus.UNRESOLVED,
        atom_id=atom.id,
        frame_id=frame.id,
    )
