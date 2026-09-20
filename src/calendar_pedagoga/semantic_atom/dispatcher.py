"""Deterministic SemanticFrameDispatcher. No production imports."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from calendar_pedagoga.semantic_atom.canonicalize import canonicalize_text
from calendar_pedagoga.semantic_atom.models import (
    CandidateConfidence,
    CoverageBinding,
    FrameCandidate,
    FrameKind,
    LexicalCheckResult,
    ObjectStatus,
    Provenance,
    SemanticFrame,
    SourceAtom,
    StructuralEvidence,
    make_object_id,
)

ADAPTER_NAME = "frame_dispatcher_c6"
NO_STRUCTURAL_MATCH = "no_structural_match"
UNSUPPORTED_ATOM_SHAPE = "unsupported_atom_shape"
AMBIGUOUS_FRAME_CANDIDATES = "ambiguous_frame_candidates"
BUILDER_ERROR = "builder_error"

BuilderFn = Callable[[SourceAtom], FrameCandidate]


@dataclass(frozen=True)
class RegisteredBuilder:
    builder_id: str
    propose: BuilderFn
    specificity: int = 0


@dataclass(frozen=True)
class DispatchDecision:
    frame: SemanticFrame
    candidates: tuple[FrameCandidate, ...]
    binding: CoverageBinding


class SemanticFrameDispatcher:
    """Run every registered builder; decide without first-match cutoff."""

    def __init__(self, builders: Sequence[RegisteredBuilder]):
        self._builders = tuple(builders)

    @property
    def builder_ids(self) -> tuple[str, ...]:
        return tuple(item.builder_id for item in self._builders)

    def dispatch_atom(self, atom: SourceAtom, index: int = 0) -> DispatchDecision:
        candidates = [_run_builder(item, atom) for item in self._builders]
        frame = decide_frame(atom, index, candidates)
        return DispatchDecision(
            frame=frame,
            candidates=tuple(candidates),
            binding=_binding_for(atom, frame, index),
        )


def decide_frame(
    atom: SourceAtom,
    index: int,
    candidates: Sequence[FrameCandidate],
) -> SemanticFrame:
    valid = tuple(item for item in candidates if item.is_valid)
    errors = tuple(
        item for item in candidates if item.confidence is CandidateConfidence.ERROR
    )
    if valid:
        groups = _group_equivalent(valid)
        if len(groups) > 1:
            return _unresolved(atom, index, AMBIGUOUS_FRAME_CANDIDATES)
        return _proven(atom, index, groups[0])
    if errors:
        return _unresolved(atom, index, BUILDER_ERROR)
    fired = tuple(
        item
        for item in candidates
        if item.rejection_reason and item.rejection_reason != NO_STRUCTURAL_MATCH
    )
    reasons = {item.rejection_reason for item in fired}
    if len(reasons) == 1:
        return _unresolved(atom, index, next(iter(reasons)))
    return _unresolved(atom, index, UNSUPPORTED_ATOM_SHAPE)


def semantic_key(candidate: FrameCandidate) -> tuple[str, str, str, str]:
    return (
        str(candidate.proposed_kind),
        canonicalize_text(candidate.proposed_predicate),
        canonicalize_text(candidate.proposed_object),
        canonicalize_text(candidate.proposed_complement),
    )


def _group_equivalent(
    valid: Sequence[FrameCandidate],
) -> tuple[tuple[FrameCandidate, ...], ...]:
    buckets: dict[tuple[str, str, str, str], list[FrameCandidate]] = {}
    for item in valid:
        buckets.setdefault(semantic_key(item), []).append(item)
    return tuple(tuple(group) for _key, group in sorted(buckets.items()))


def _run_builder(item: RegisteredBuilder, atom: SourceAtom) -> FrameCandidate:
    try:
        proposed = item.propose(atom)
    except Exception as exc:
        return FrameCandidate(
            builder_id=item.builder_id,
            atom_id=atom.id,
            span=atom.span,
            source_fingerprint=atom.source_fingerprint,
            proposed_kind=FrameKind.PROJECTED,
            proposed_predicate="",
            proposed_object="",
            proposed_complement="",
            proposed_result="",
            proposed_control="",
            structural_evidence=StructuralEvidence(
                notes=(f"{type(exc).__name__}: {exc}",),
                specificity=item.specificity,
            ),
            lexical_check=LexicalCheckResult(passed=False),
            confidence=CandidateConfidence.ERROR,
            rejection_reason=BUILDER_ERROR,
        )
    evidence = proposed.structural_evidence
    if evidence.specificity != item.specificity:
        proposed = replace(
            proposed,
            structural_evidence=StructuralEvidence(
                notes=evidence.notes,
                specificity=item.specificity,
            ),
        )
    return proposed


def _merged_evidence(group: Sequence[FrameCandidate]) -> StructuralEvidence:
    notes: list[str] = []
    seen: set[str] = set()
    specificity = 0
    for item in group:
        specificity = max(specificity, item.structural_evidence.specificity)
        for note in (*item.structural_evidence.notes, item.builder_id):
            if note not in seen:
                seen.add(note)
                notes.append(note)
    return StructuralEvidence(notes=tuple(notes), specificity=specificity)


def _chosen(group: Sequence[FrameCandidate]) -> FrameCandidate:
    return sorted(
        group,
        key=lambda item: (-item.structural_evidence.specificity, item.builder_id),
    )[0]


def _proven(
    atom: SourceAtom,
    index: int,
    group: Sequence[FrameCandidate],
) -> SemanticFrame:
    chosen = _chosen(group)
    builder_ids = ",".join(sorted(item.builder_id for item in group))
    evidence = _merged_evidence(group)
    return SemanticFrame(
        id=make_object_id(
            "frame", atom.id, chosen.proposed_result, "proven", builder_ids
        ),
        span=chosen.span,
        source_fingerprint=atom.source_fingerprint,
        provenance=Provenance(
            adapter=ADAPTER_NAME,
            role="frame",
            clause_index=index,
            note=builder_ids if len(group) == 1 else f"merged:{';'.join(evidence.notes)}",
        ),
        status=ObjectStatus.PROVEN,
        kind=chosen.proposed_kind,
        atom_id=atom.id,
        clause_id=atom.clause_id,
        projected_type="",
        projected_result=chosen.proposed_result,
        projected_control=chosen.proposed_control,
        coverage_status="COVERED",
        predicate=chosen.proposed_predicate,
        object=chosen.proposed_object,
        complement=chosen.proposed_complement,
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
        span=frame.span,
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
