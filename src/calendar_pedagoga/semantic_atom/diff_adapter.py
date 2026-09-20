"""C10 DiffAdapter: measure production vs shadow C1–C9. Does not write."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from calendar_pedagoga.semantic_atom.canonicalize import canonicalize_text
from calendar_pedagoga.semantic_atom.control_adapter import compose_control
from calendar_pedagoga.semantic_atom.frame_adapter import project_frames
from calendar_pedagoga.semantic_atom.lexical import (
    APPROVED_CONTROL_TEMPLATES,
    ApprovedControlTemplate,
    lookup_approved_control_template,
    shadow_lexical_violations,
    tokenize,
)
from calendar_pedagoga.semantic_atom.models import FrameKind, ObjectStatus
from calendar_pedagoga.semantic_atom.span_cover import action_cover_text
from calendar_pedagoga.semantic_atom.passthrough import DiffKind

ADAPTER_NAME = "diff_c10"
TITLE_ONLY = "title_only_without_source"
FRAME_WITHOUT_BINDING = "frame_without_binding"
CONTROL_WITHOUT_BINDING = "control_without_binding"
LEXICAL_VIOLATION = "lexical_violation"
OLD_COVERED_SHADOW_UNRESOLVED = "old_covered_shadow_unresolved"
SHADOW_COVERS_MORE = "shadow_covers_more"
TEXT_DIFFERS = "text_differs"

# Worst first. BLOCKED is a measurement stop, not a C11 cutover blocker.
SEVERITY_ORDER: tuple[DiffKind, ...] = (
    DiffKind.NEW_INVENTS,
    DiffKind.NEW_LOSES,
    DiffKind.UNRESOLVED_DRIFT,
    DiffKind.NEW_COVERS_MORE,
    DiffKind.EQUAL,
    DiffKind.BLOCKED,
)

_SEVERITY_INDEX = {kind: index for index, kind in enumerate(SEVERITY_ORDER)}
_DASH_RE = re.compile(r"[\u002D\u2010-\u2015\u2212\uFE58\uFE63\uFF0D]+")
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
        "между",
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
        "это",
        "что",
        "чтобы",
    }
)
_MIN_TOKEN_LEN = 3
_MIN_CONTENT_TOKENS = 2
_MIN_SINGLE_TOKEN_LEN = 5


@dataclass(frozen=True)
class OldSnapshot:
    result: str
    control: str
    coverage: tuple[tuple[str, str], ...] = ()
    topic_title: str = ""
    source: str = ""
    week: int = 0


@dataclass(frozen=True)
class ShadowSnapshot:
    result: str
    control: str
    atoms: tuple[object, ...] = ()
    frames: tuple[object, ...] = ()
    bindings: tuple[object, ...] = ()
    control_pieces: tuple[object, ...] = ()
    control_bindings: tuple[object, ...] = ()
    lexical_violations: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeekDiff:
    kind: DiffKind
    week: int
    old_empty_result: bool
    old_empty_control: bool
    old_covered: int
    old_uncovered: int
    shadow_empty_result: bool
    shadow_empty_control: bool
    shadow_atoms: int
    shadow_proven: int
    shadow_unresolved: int
    unresolved_reasons: tuple[str, ...]
    result_equal: bool
    control_equal: bool
    reasons: tuple[str, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "kind": str(self.kind),
            "week": self.week,
            "old_empty_result": self.old_empty_result,
            "old_empty_control": self.old_empty_control,
            "old_covered": self.old_covered,
            "old_uncovered": self.old_uncovered,
            "shadow_empty_result": self.shadow_empty_result,
            "shadow_empty_control": self.shadow_empty_control,
            "shadow_atoms": self.shadow_atoms,
            "shadow_proven": self.shadow_proven,
            "shadow_unresolved": self.shadow_unresolved,
            "unresolved_reasons": list(self.unresolved_reasons),
            "result_equal": self.result_equal,
            "control_equal": self.control_equal,
            "reasons": list(self.reasons),
        }


def severity(kind: DiffKind) -> int:
    """Lower is worse. BLOCKED is last: it is not a cutover class."""

    return _SEVERITY_INDEX[kind]


def worst_kind(kinds: Iterable[DiffKind]) -> DiffKind:
    items = tuple(kinds)
    if not items:
        return DiffKind.EQUAL
    comparable = tuple(item for item in items if item is not DiffKind.BLOCKED)
    pool = comparable or items
    return min(pool, key=severity)


def snapshot_from_row(row: object, source: str = "") -> OldSnapshot:
    title = _topic_title(row)
    body = source or _source_text(row)
    coverage = tuple(getattr(row, "clause_coverage", ()) or ())
    week = 0
    origin = getattr(row, "source", None)
    if origin is not None:
        week = int(getattr(origin, "week_number", 0) or 0)
    else:
        week = int(getattr(row, "week_number", 0) or 0)
    return OldSnapshot(
        result=str(getattr(row, "planned_result", "") or ""),
        control=str(getattr(row, "assessment_method", "") or ""),
        coverage=coverage,
        topic_title=title,
        source=body,
        week=week,
    )


def snapshot_shadow(source: str, row: object | None = None) -> ShadowSnapshot:
    projection = project_frames(source, row)
    control = compose_control(projection)
    violations = shadow_lexical_violations(
        source=source,
        result=projection.candidate_result,
        control=control.composed_control,
        approved_templates=_approved_templates_from_control(control),
    )
    return ShadowSnapshot(
        result=projection.candidate_result,
        control=control.composed_control,
        atoms=projection.atoms,
        frames=projection.frames,
        bindings=projection.bindings,
        control_pieces=control.pieces,
        control_bindings=control.bindings,
        lexical_violations=violations,
    )


def _approved_templates_from_control(control: object) -> tuple[ApprovedControlTemplate, ...]:
    bound = {
        str(getattr(item, "piece_id", "") or "")
        for item in getattr(control, "bindings", ())
        if getattr(item, "piece_id", "") and getattr(item, "atom_id", "")
    }
    found: list[ApprovedControlTemplate] = []
    for piece in getattr(control, "pieces", ()):
        if getattr(piece, "status", None) is not ObjectStatus.PROVEN:
            continue
        piece_id = str(getattr(piece, "id", "") or "")
        if piece_id not in bound or not getattr(piece, "atom_id", ""):
            continue
        provenance = getattr(piece, "provenance", None)
        adapter = getattr(provenance, "adapter", "") if provenance is not None else ""
        if not adapter:
            continue
        template_id = lookup_approved_control_template(getattr(piece, "label", ""))
        if template_id is None:
            continue
        found.append(
            ApprovedControlTemplate(
                template_id=template_id,
                text=APPROVED_CONTROL_TEMPLATES[template_id],
                provenance=adapter,
                bound=True,
                proven=True,
            )
        )
    return tuple(found)


def classify_week(row: object, source: str = "") -> WeekDiff:
    old = snapshot_from_row(row, source)
    if not canonicalize_text(old.source) and canonicalize_text(old.topic_title):
        return classify_snapshots(old, ShadowSnapshot(result="", control=""))
    return classify_snapshots(old, snapshot_shadow(old.source, row))


def classify_snapshots(old: OldSnapshot, shadow: ShadowSnapshot) -> WeekDiff:
    source = canonicalize_text(old.source)
    title = canonicalize_text(old.topic_title)
    if not source and title:
        return _week_diff(old, shadow, DiffKind.BLOCKED, (TITLE_ONLY,))

    kinds: list[DiffKind] = []
    reasons: list[str] = []

    if shadow.lexical_violations:
        kinds.append(DiffKind.NEW_INVENTS)
        reasons.append(LEXICAL_VIOLATION)

    if _unbound_frame(shadow):
        kinds.append(DiffKind.NEW_INVENTS)
        reasons.append(FRAME_WITHOUT_BINDING)
    if _unbound_control(shadow):
        kinds.append(DiffKind.NEW_INVENTS)
        reasons.append(CONTROL_WITHOUT_BINDING)

    lost, covered_more = _coverage_signals(old, shadow)
    if lost:
        kinds.append(DiffKind.NEW_LOSES)
        reasons.append(OLD_COVERED_SHADOW_UNRESOLVED)
    elif covered_more:
        kinds.append(DiffKind.NEW_COVERS_MORE)
        reasons.append(SHADOW_COVERS_MORE)

    result_equal = canonicalize_text(old.result) == canonicalize_text(shadow.result)
    control_equal = canonicalize_text(old.control) == canonicalize_text(shadow.control)
    if not lost and DiffKind.NEW_INVENTS not in kinds and not covered_more:
        if not result_equal or not control_equal:
            kinds.append(DiffKind.UNRESOLVED_DRIFT)
            reasons.append(TEXT_DIFFERS)

    if not kinds:
        kinds.append(DiffKind.EQUAL)

    return _week_diff(old, shadow, worst_kind(kinds), tuple(dict.fromkeys(reasons)))


def _unbound_frame(shadow: ShadowSnapshot) -> bool:
    bound = {getattr(item, "frame_id", "") for item in shadow.bindings}
    for frame in shadow.frames:
        output = canonicalize_text(getattr(frame, "projected_result", "")) or canonicalize_text(
            getattr(frame, "projected_control", "")
        )
        proven = getattr(frame, "status", None) is ObjectStatus.PROVEN
        if not (proven or output):
            continue
        atom_id = str(getattr(frame, "atom_id", "") or "")
        frame_id = str(getattr(frame, "id", "") or "")
        if not atom_id or frame_id not in bound:
            return True
    return False


def _unbound_control(shadow: ShadowSnapshot) -> bool:
    bound = {getattr(item, "piece_id", "") for item in shadow.control_bindings}
    for piece in shadow.control_pieces:
        proven = getattr(piece, "status", None) is ObjectStatus.PROVEN
        label = canonicalize_text(getattr(piece, "label", ""))
        if not (proven or label):
            continue
        atom_id = str(getattr(piece, "atom_id", "") or "")
        piece_id = str(getattr(piece, "id", "") or "")
        if not atom_id or piece_id not in bound:
            return True
    return False


def _coverage_signals(old: OldSnapshot, shadow: ShadowSnapshot) -> tuple[bool, bool]:
    old_covered = [clause for clause, status in old.coverage if canonicalize_text(status) == "COVERED"]
    old_holes = [clause for clause, status in old.coverage if canonicalize_text(status) != "COVERED"]
    lost = False
    for clause in old_covered:
        matched = [frame for frame in shadow.frames if _frame_matches_clause(clause, frame, shadow)]
        if not matched or any(_frame_blocks_coverage(frame, shadow) for frame in matched):
            lost = True
            break
    covered_more = False
    if not lost:
        if any(
            not _frame_blocks_coverage(frame, shadow)
            and _frame_matches_clause(clause, frame, shadow)
            for clause in old_holes
            for frame in shadow.frames
        ):
            covered_more = True
        if not canonicalize_text(old.result) and canonicalize_text(shadow.result):
            covered_more = True
        if not canonicalize_text(old.control) and canonicalize_text(shadow.control):
            covered_more = True
    return lost, covered_more


def _frame_blocks_coverage(frame: object, shadow: ShadowSnapshot) -> bool:
    if getattr(frame, "status", None) is not ObjectStatus.PROVEN:
        return True
    if canonicalize_text(getattr(frame, "reason", "")) == LEXICAL_VIOLATION:
        return True
    bound = {str(getattr(item, "frame_id", "") or "") for item in shadow.bindings}
    atom_id = str(getattr(frame, "atom_id", "") or "")
    frame_id = str(getattr(frame, "id", "") or "")
    return not atom_id or frame_id not in bound


def coverage_text_match(clause: str, candidate: str) -> bool:
    """True when a covered clause is the same text, a dash variant, or a list member."""

    left = _fold_match_text(clause)
    right = _fold_match_text(candidate)
    if not left or not right:
        return False
    if left.casefold() == right.casefold():
        return True
    if _safe_substring(left, right):
        return True
    return _clause_in_catalog(left, right)


def _fold_match_text(value: str) -> str:
    return canonicalize_text(_DASH_RE.sub(" ", value or ""))


def _content_token_set(value: str) -> frozenset[str]:
    tokens: set[str] = set()
    for token in tokenize(_fold_match_text(value).casefold()):
        if len(token) < _MIN_TOKEN_LEN or token in _FUNCTION_WORDS:
            continue
        tokens.add(token)
    return frozenset(tokens)


def _safe_substring(clause: str, candidate: str) -> bool:
    left_cf = clause.casefold()
    right_cf = candidate.casefold()
    if left_cf not in right_cf:
        return False
    tokens = _content_token_set(clause)
    if len(tokens) >= _MIN_CONTENT_TOKENS:
        return True
    if len(tokens) != 1:
        return False
    token = next(iter(tokens))
    if len(token) < _MIN_SINGLE_TOKEN_LEN:
        return False
    return token in {item.casefold() for item in tokenize(candidate)}


def _clause_in_catalog(clause: str, candidate: str) -> bool:
    if not any(mark in candidate for mark in (",", ":", " и ")):
        return False
    clause_toks = _content_token_set(clause)
    candidate_toks = _content_token_set(candidate)
    if len(clause_toks) < _MIN_CONTENT_TOKENS:
        return False
    return clause_toks <= candidate_toks and len(candidate_toks) > len(clause_toks)


def _frame_cover_text(frame: object, shadow: ShadowSnapshot) -> str:
    atom_by_id = {getattr(atom, "id", ""): atom for atom in shadow.atoms}
    atom = atom_by_id.get(getattr(frame, "atom_id", ""))
    if atom is None:
        return ""
    atom_text = str(getattr(atom, "text", "") or "")
    if getattr(frame, "kind", None) is FrameKind.ACTION:
        return action_cover_text(atom_text, frame)
    frame_span = getattr(frame, "span", None)
    atom_span = getattr(atom, "span", None)
    if frame_span is None or atom_span is None:
        return atom_text
    start = int(getattr(frame_span, "start", 0) or 0)
    end = int(getattr(frame_span, "end", 0) or 0)
    atom_start = int(getattr(atom_span, "start", 0) or 0)
    atom_end = int(getattr(atom_span, "end", 0) or 0)
    if start == atom_start and end == atom_end:
        return atom_text
    rel_start = start - atom_start
    rel_end = end - atom_start
    if 0 <= rel_start < rel_end <= len(atom_text):
        return atom_text[rel_start:rel_end]
    return atom_text


def _frame_matches_clause(clause: str, frame: object, shadow: ShadowSnapshot) -> bool:
    if not _fold_match_text(clause):
        return False
    texts = [
        getattr(frame, "projected_result", ""),
        getattr(frame, "object", ""),
        getattr(frame, "complement", ""),
        _frame_cover_text(frame, shadow),
    ]
    return any(coverage_text_match(clause, str(raw or "")) for raw in texts)


def _week_diff(
    old: OldSnapshot,
    shadow: ShadowSnapshot,
    kind: DiffKind,
    reasons: tuple[str, ...],
) -> WeekDiff:
    old_covered = sum(
        1 for _clause, status in old.coverage if canonicalize_text(status) == "COVERED"
    )
    proven = sum(
        1 for frame in shadow.frames if getattr(frame, "status", None) is ObjectStatus.PROVEN
    )
    unresolved = sum(
        1 for frame in shadow.frames if getattr(frame, "status", None) is ObjectStatus.UNRESOLVED
    )
    return WeekDiff(
        kind=kind,
        week=old.week,
        old_empty_result=not canonicalize_text(old.result),
        old_empty_control=not canonicalize_text(old.control),
        old_covered=old_covered,
        old_uncovered=max(0, len(old.coverage) - old_covered),
        shadow_empty_result=not canonicalize_text(shadow.result),
        shadow_empty_control=not canonicalize_text(shadow.control),
        shadow_atoms=len(shadow.atoms),
        shadow_proven=proven,
        shadow_unresolved=unresolved,
        unresolved_reasons=_unresolved_reasons(shadow),
        result_equal=canonicalize_text(old.result) == canonicalize_text(shadow.result),
        control_equal=canonicalize_text(old.control) == canonicalize_text(shadow.control),
        reasons=reasons,
    )


def _unresolved_reasons(shadow: ShadowSnapshot) -> tuple[str, ...]:
    reasons: list[str] = []
    seen: set[str] = set()
    for item in (*shadow.frames, *shadow.control_pieces):
        if getattr(item, "status", None) is not ObjectStatus.UNRESOLVED:
            continue
        reason = canonicalize_text(getattr(item, "reason", "") or "unresolved")
        if reason not in seen:
            seen.add(reason)
            reasons.append(reason)
    return tuple(sorted(reasons))


def _topic_title(row: object) -> str:
    origin = getattr(row, "source", None)
    if origin is not None:
        return str(getattr(origin, "topic_title", "") or "")
    return str(getattr(row, "topic_title", "") or "")


def _source_text(row: object) -> str:
    origin = getattr(row, "source", None)
    parts = (
        getattr(row, "theory_text", ""),
        getattr(row, "practice_text", ""),
        getattr(origin, "program_content_full", "") if origin is not None else "",
        getattr(row, "program_content_full", ""),
    )
    return " ".join(str(part) for part in parts if part)
