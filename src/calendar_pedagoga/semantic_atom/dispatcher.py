"""Deterministic SemanticFrameDispatcher. No production imports."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
import re

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
OPEN_TAIL_UNRESOLVED = "open_tail_unresolved"

_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+", re.UNICODE)
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+", re.UNICODE)
_OPEN_TAIL_RE = re.compile(
    r"(?i)(?:\b(?:и\s+другие|и\s+прочее|и\s+прочие|и\s+др\.?|"
    r"и\s+т\.?\s*д\.?|и\s+т\.?\s*п\.?|и\s+пр\.?)\b|\.\.\.|…)"
)
_FUNCTION_WORDS = frozenset(
    {
        "а",
        "без",
        "в",
        "во",
        "да",
        "для",
        "до",
        "за",
        "и",
        "из",
        "или",
        "их",
        "к",
        "ко",
        "как",
        "на",
        "над",
        "не",
        "ни",
        "о",
        "об",
        "обо",
        "от",
        "по",
        "под",
        "при",
        "про",
        "с",
        "со",
        "у",
        "через",
    }
)

BuilderFn = Callable[[SourceAtom], FrameCandidate]
SegmentProver = Callable[[str, str, str], bool]

_TITLE_SPLIT_RE = re.compile(r"\s*[\u2013\u2014\u2015:]\s*|\s+-\s*|\s*;\s*")
_AND_SPLIT_RE = re.compile(r"\s+и\s+", re.IGNORECASE)


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

    def __init__(
        self,
        builders: Sequence[RegisteredBuilder],
        prove_segment: SegmentProver | None = None,
    ):
        self._builders = tuple(builders)
        self._prove_segment = prove_segment

    @property
    def builder_ids(self) -> tuple[str, ...]:
        return tuple(item.builder_id for item in self._builders)

    def dispatch_atom(self, atom: SourceAtom, index: int = 0) -> DispatchDecision:
        candidates = [_run_builder(item, atom) for item in self._builders]
        frame = decide_frame(
            atom, index, candidates, prove_segment=self._prove_segment
        )
        return DispatchDecision(
            frame=frame,
            candidates=tuple(candidates),
            binding=_binding_for(atom, frame, index),
        )


def decide_frame(
    atom: SourceAtom,
    index: int,
    candidates: Sequence[FrameCandidate],
    prove_segment: SegmentProver | None = None,
) -> SemanticFrame:
    valid = tuple(item for item in candidates if item.is_valid)
    errors = tuple(
        item for item in candidates if item.confidence is CandidateConfidence.ERROR
    )
    if valid:
        groups = _reduce_comparable(atom, valid, prove_segment)
        if not groups:
            return _unresolved(atom, index, UNSUPPORTED_ATOM_SHAPE)
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


def _canonical_result(text: str) -> str:
    return canonicalize_text(text).casefold().strip(" .,:;")


def _content_tokens(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(canonicalize_text(text).casefold()):
        if len(raw) < 2 or raw in _FUNCTION_WORDS:
            continue
        tokens.append(raw)
    return tuple(tokens)


def _leading_finite(text: str) -> str:
    match = _WORD_RE.search(canonicalize_text(text))
    return match.group(0).casefold() if match else ""


def _same_span(left, right) -> bool:
    return int(left.start) == int(right.start) and int(left.end) == int(right.end)


def _eligible(atom: SourceAtom, candidate: FrameCandidate) -> bool:
    if not candidate.is_valid or not candidate.lexical_check.passed:
        return False
    if candidate.atom_id != atom.id:
        return False
    if candidate.source_fingerprint and candidate.source_fingerprint != atom.source_fingerprint:
        return False
    return _same_span(candidate.span, atom.span)


def _has_open_tail(candidate: FrameCandidate) -> bool:
    notes = " ".join(candidate.structural_evidence.notes).casefold()
    if OPEN_TAIL_UNRESOLVED in notes or "open_tail" in notes:
        return True
    result = candidate.proposed_result or ""
    if _OPEN_TAIL_RE.search(result) or _OPEN_TAIL_RE.search(candidate.proposed_object or ""):
        return True
    return ":" in result or ";" in result


def _ordered_subsequence(short: Sequence[str], long: Sequence[str]) -> bool:
    if not short or len(short) >= len(long):
        return False
    index = 0
    for token in long:
        if index < len(short) and token == short[index]:
            index += 1
    return index == len(short)


def _source_holds(tokens: Sequence[str], source: str) -> bool:
    allowed = set(_content_tokens(source))
    return all(token in allowed for token in tokens)


def _split_source_segments(text: str) -> tuple[str, ...]:
    cleaned = canonicalize_text(text).strip(" .")
    if not cleaned:
        return ()
    parts: list[str] = []
    for chunk in _TITLE_SPLIT_RE.split(cleaned):
        piece = chunk.strip(" .")
        if not piece:
            continue
        coordinated = [
            item.strip(" .") for item in _AND_SPLIT_RE.split(piece) if item.strip(" .")
        ]
        parts.extend(coordinated)
    return tuple(parts)


def _added_source_segments(
    source: str,
    extra: Sequence[str],
    short_tokens: Sequence[str],
) -> tuple[str, ...] | None:
    extra_set = set(extra)
    short_set = set(short_tokens)
    added: list[str] = []
    covered: set[str] = set()
    for segment in _split_source_segments(source):
        tokens = set(_content_tokens(segment))
        if not tokens:
            continue
        if tokens <= extra_set:
            added.append(segment)
            covered.update(tokens)
            continue
        if tokens <= short_set:
            continue
        if tokens & extra_set:
            return None
    if not added or extra_set - covered:
        return None
    return tuple(added)


def _exact_result_key(candidate: FrameCandidate) -> tuple[str, str]:
    return (str(candidate.proposed_kind), _canonical_result(candidate.proposed_result))


def _cluster_exact_results(
    eligible: Sequence[FrameCandidate],
) -> list[list[FrameCandidate]]:
    buckets: dict[tuple[str, str], list[FrameCandidate]] = {}
    for item in eligible:
        buckets.setdefault(_exact_result_key(item), []).append(item)
    return [buckets[key] for key in sorted(buckets)]


def _strict_superset(
    atom: SourceAtom,
    longer: Sequence[FrameCandidate],
    shorter: Sequence[FrameCandidate],
    prove_segment: SegmentProver | None,
) -> bool:
    left = longer[0]
    right = shorter[0]
    if left.proposed_kind != right.proposed_kind:
        return False
    if not _same_span(left.span, right.span) or not _same_span(left.span, atom.span):
        return False
    finite = _leading_finite(left.proposed_result)
    if not finite or finite != _leading_finite(right.proposed_result):
        return False
    if canonicalize_text(left.proposed_predicate) != canonicalize_text(
        right.proposed_predicate
    ):
        return False
    if _has_open_tail(left) or _has_open_tail(right):
        return False
    if not left.lexical_check.passed or not right.lexical_check.passed:
        return False
    long_tokens = _content_tokens(left.proposed_result)
    short_tokens = _content_tokens(right.proposed_result)
    if not _ordered_subsequence(short_tokens, long_tokens):
        return False
    extra = [token for token in long_tokens if token not in set(short_tokens)]
    if not extra or not _source_holds(extra, atom.text):
        return False
    segments = _added_source_segments(atom.text, extra, short_tokens)
    if not segments or prove_segment is None:
        return False
    expected_kind = str(left.proposed_kind)
    for segment in segments:
        if not prove_segment(segment, finite, expected_kind):
            return False
    return True


def _collapse_supersets(
    atom: SourceAtom,
    clusters: list[list[FrameCandidate]],
    prove_segment: SegmentProver | None,
) -> list[list[FrameCandidate]]:
    pending = list(clusters)
    changed = True
    while changed:
        changed = False
        index = 0
        while index < len(pending):
            other = index + 1
            merged = False
            while other < len(pending):
                if _strict_superset(
                    atom, pending[index], pending[other], prove_segment
                ):
                    pending[index] = pending[index] + pending[other]
                    del pending[other]
                    changed = True
                    merged = True
                    continue
                if _strict_superset(
                    atom, pending[other], pending[index], prove_segment
                ):
                    pending[other] = pending[other] + pending[index]
                    del pending[index]
                    changed = True
                    merged = True
                    break
                other += 1
            if not merged:
                index += 1
    return pending


def _reduce_comparable(
    atom: SourceAtom,
    valid: Sequence[FrameCandidate],
    prove_segment: SegmentProver | None,
) -> tuple[tuple[FrameCandidate, ...], ...]:
    eligible = [item for item in valid if _eligible(atom, item)]
    eligible_ids = {id(item) for item in eligible}
    leftover = [
        item
        for item in valid
        if id(item) not in eligible_ids and item.lexical_check.passed
    ]
    clusters = _cluster_exact_results(eligible)
    clusters.extend([item] for item in leftover)
    reduced = _collapse_supersets(atom, clusters, prove_segment)
    return tuple(tuple(group) for group in reduced if group)


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
        key=lambda item: (
            -len(_content_tokens(item.proposed_result)),
            -item.structural_evidence.specificity,
            item.builder_id,
        ),
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
