"""Shadow CONTROL composed only from proven SemanticFrame results."""

from __future__ import annotations

from calendar_pedagoga import content_engine_v2 as _ce2
from calendar_pedagoga.semantic_atom.canonicalize import canonicalize_text
from calendar_pedagoga.semantic_atom.frame_adapter import (
    _traces_to_source,
    project_frames,
)
from calendar_pedagoga.semantic_atom.lexical import shadow_lexical_violations
from calendar_pedagoga.semantic_atom.models import (
    ControlBinding,
    ControlKind,
    ControlPiece,
    FrameKind,
    FrameProjection,
    LexicalCheckResult,
    ObjectStatus,
    Provenance,
    SemanticFrame,
    ShadowControlReport,
    make_object_id,
)

ADAPTER_NAME = "control_c9"
MISSING_BINDING = "missing_control_binding"
NEW_INVENTS = "NEW_INVENTS"
UNMAPPED_CONTROL = "unmapped_control"
UNRESOLVED_FRAME = "unresolved_frame"
CITES_FINITE_RESULT = "cites_finite_result"
UNTRACED_OBJECT = "untraced_object_or_complement"

_APPROVED_ORAL = ("устный опрос",)
_APPROVED_PRODUCT = ("просмотр рисунков", "просмотр и оценка готовой работы")
_APPROVED_OBSERVATION = (
    "педагогическое наблюдение за выполнением задания",
    "педагогическое наблюдение за выполнением упражнений",
    "педагогическое наблюдение за участием",
    "педагогическое наблюдение на экскурсии",
    "педагогическое наблюдение за ",
)
_EXTRA_ALLOWED = ("его", "её", "ее", "их", "готовой", "работы", "оценка")

_KNOWLEDGE_KINDS = {
    FrameKind.KNOWLEDGE,
    FrameKind.DEFINITION,
    FrameKind.CLASSIFICATION,
    FrameKind.QUESTION,
}


def project_control(
    source: object,
    row=None,
    dispatcher=None,
) -> ShadowControlReport:
    return compose_control(project_frames(source, row, dispatcher))


def compose_control(projection: FrameProjection) -> ShadowControlReport:
    pieces: list[ControlPiece] = []
    bindings: list[ControlBinding] = []
    seen: set[tuple[str, str, str, str]] = set()
    uncovered: list[str] = []
    for frame in projection.frames:
        piece = _piece_for(projection.source, frame)
        if piece.status is ObjectStatus.PROVEN:
            key = (
                str(piece.kind),
                canonicalize_text(piece.label),
                canonicalize_text(piece.source_object),
                canonicalize_text(piece.source_complement),
            )
            if key in seen:
                continue
            seen.add(key)
        elif frame.status is ObjectStatus.PROVEN:
            uncovered.append(frame.id)
        pieces.append(piece)
        bindings.append(_binding_for(piece))
    pieces.sort(key=lambda item: (item.kind.value, item.label.casefold(), item.id))
    binding_by_piece = {item.piece_id: item for item in bindings}
    bindings = tuple(binding_by_piece[item.id] for item in pieces)
    proven = [item for item in pieces if item.status is ObjectStatus.PROVEN]
    status = (
        ObjectStatus.PROVEN
        if proven
        else ObjectStatus.UNRESOLVED
        if pieces
        else ObjectStatus.UNASSESSED
    )
    return ShadowControlReport(
        source=projection.source,
        pieces=tuple(pieces),
        bindings=bindings,
        composed_control=_compose_text(proven),
        uncovered_frame_ids=tuple(uncovered),
        status=status,
    )


def _piece_for(source: str, frame: SemanticFrame) -> ControlPiece:
    if not frame.atom_id:
        return _rejected(frame, MISSING_BINDING)
    if frame.status is not ObjectStatus.PROVEN:
        return _rejected(frame, UNRESOLVED_FRAME)
    try:
        mapped = _map_frame(source, frame)
    except Exception:
        mapped = None
    if mapped is None:
        return _rejected(frame, UNMAPPED_CONTROL)
    kind, label = mapped
    if _cites_finite_result(label, frame.projected_result):
        return _rejected(frame, CITES_FINITE_RESULT, label=label, kind=kind)
    obj = frame.object.strip()
    complement = frame.complement.strip()
    if not _traces_to_source(obj, source) or not _traces_to_source(complement, source):
        return _rejected(
            frame, UNTRACED_OBJECT, label=label, kind=kind, obj=obj, complement=complement
        )
    violations = shadow_lexical_violations(
        source=source,
        result="",
        control=label,
        extra_allowed=_EXTRA_ALLOWED,
    )
    if violations:
        return _rejected(
            frame,
            NEW_INVENTS,
            label=label,
            kind=kind,
            obj=obj,
            complement=complement,
            violations=violations,
        )
    return ControlPiece(
        id=make_object_id("cpiece", frame.id, label, str(kind)),
        frame_id=frame.id,
        atom_id=frame.atom_id,
        span=frame.span,
        kind=kind,
        label=label,
        source_object=obj,
        source_complement=complement,
        provenance=Provenance(adapter=ADAPTER_NAME, role="piece", note=str(kind)),
        lexical_check=LexicalCheckResult(passed=True),
        status=ObjectStatus.PROVEN,
    )


def _map_frame(source: str, frame: SemanticFrame) -> tuple[ControlKind, str] | None:
    for raw in _helper_labels(frame):
        classified = _classify_approved(raw)
        if classified is not None:
            return classified
    return _template_for(source, frame)


def _helper_labels(frame: SemanticFrame) -> list[str]:
    labels: list[str] = []
    result = frame.projected_result
    if frame.kind in _KNOWLEDGE_KINDS:
        labels.append(_safe_call(_ce2._declared_knowledge_control, result))
        labels.append(_safe_call(_ce2._declared_product_control, result))
    elif frame.kind is FrameKind.CREATIVE_PRODUCT:
        labels.append(_safe_call(_ce2._declared_product_control, result))
    elif frame.kind is FrameKind.ACTION:
        labels.append(_safe_call(_ce2._declared_product_control, result))
        labels.append(_safe_call(_ce2._process_control, result, ""))
    return [item for item in labels if item]


def _safe_call(helper, *args) -> str:
    try:
        built = helper(*args)
    except Exception:
        return ""
    return _ce2._normalize_spaces(str(built or "")).strip()


def _template_for(source: str, frame: SemanticFrame) -> tuple[ControlKind, str] | None:
    if frame.kind in _KNOWLEDGE_KINDS:
        focus = (frame.object or frame.complement).strip(" .")
        if not focus or not _traces_to_source(focus, source):
            return None
        label = _ce2._cap_sentence(
            f"устный опрос: {_ce2._decap_phrase(focus)}"
        )
        return ControlKind.ORAL_SURVEY, label
    if frame.kind is FrameKind.CREATIVE_PRODUCT:
        verb = _ce2._leading_finite_verb(frame.projected_result).casefold()
        if verb == "рисует":
            return ControlKind.PRODUCT_REVIEW, _ce2._cap_sentence("просмотр рисунков")
        return ControlKind.PRODUCT_REVIEW, _ce2._cap_sentence(
            "просмотр и оценка готовой работы"
        )
    if frame.kind is FrameKind.ACTION:
        result = frame.projected_result.casefold()
        note = frame.provenance.note.casefold()
        if "экскурси" in result or "прогул" in result:
            return ControlKind.PEDAGOGICAL_OBSERVATION, _ce2._cap_sentence(
                "педагогическое наблюдение на экскурсии"
            )
        if "упражнен" in result or "exercise" in note:
            remainder = _ce2._normalize_spaces(
                frame.object + " " + frame.complement
            ).strip(" .")
            body = "педагогическое наблюдение за выполнением упражнений"
            if remainder and _traces_to_source(remainder, source):
                body = f"{body} {remainder}"
            return ControlKind.PEDAGOGICAL_OBSERVATION, _ce2._cap_sentence(body)
        if result.startswith("участвует") or "particip" in note:
            return ControlKind.PEDAGOGICAL_OBSERVATION, _ce2._cap_sentence(
                "педагогическое наблюдение за выполнением задания"
            )
        return ControlKind.PEDAGOGICAL_OBSERVATION, _ce2._cap_sentence(
            "педагогическое наблюдение за выполнением задания"
        )
    return None


def _classify_approved(label: str) -> tuple[ControlKind, str] | None:
    cleaned = _ce2._cap_sentence(_ce2._normalize_spaces(label).strip(" ."))
    low = cleaned.casefold()
    if low.startswith(_APPROVED_ORAL):
        if "по теме" in low:
            return None
        return ControlKind.ORAL_SURVEY, cleaned
    for prefix in _APPROVED_PRODUCT:
        if low.startswith(prefix):
            return ControlKind.PRODUCT_REVIEW, cleaned
    if low.startswith("педагогическое наблюдение"):
        if any(low.startswith(prefix) for prefix in _APPROVED_OBSERVATION):
            if "практическ" in low:
                return ControlKind.PRACTICAL_CHECK, cleaned
            return ControlKind.PEDAGOGICAL_OBSERVATION, cleaned
    return None


def _cites_finite_result(label: str, result: str) -> bool:
    if not label.strip() or not result.strip():
        return False
    if canonicalize_text(label) == canonicalize_text(result):
        return True
    verb = _ce2._leading_finite_verb(result).casefold()
    return bool(verb) and label.casefold().startswith(verb)


def _compose_text(pieces: list[ControlPiece]) -> str:
    if not pieces:
        return ""
    orals = [item for item in pieces if item.kind is ControlKind.ORAL_SURVEY]
    others = [item for item in pieces if item.kind is not ControlKind.ORAL_SURVEY]
    parts: list[str] = []
    if orals:
        payloads = [_oral_payload(item.label) for item in orals]
        if all(payloads):
            parts.append(_ce2._cap_sentence("устный опрос: " + "; ".join(payloads)))
        else:
            parts.append(_ce2._cap_sentence(orals[0].label))
    for item in others:
        parts.append(item.label.rstrip("."))
    return _ce2._normalize_spaces("; ".join(dict.fromkeys(parts)))


def _oral_payload(label: str) -> str:
    text = _ce2._normalize_spaces(label).strip(" .")
    low = text.casefold()
    for prefix in ("устный опрос: ", "устный опрос по "):
        if low.startswith(prefix):
            return text[len(prefix) :].strip(" .")
    return ""


def _binding_for(piece: ControlPiece) -> ControlBinding:
    return ControlBinding(
        id=make_object_id("cbind", piece.id, piece.frame_id, piece.atom_id),
        piece_id=piece.id,
        frame_id=piece.frame_id,
        atom_id=piece.atom_id,
        span=piece.span,
        provenance=Provenance(adapter=ADAPTER_NAME, role="binding"),
        status=piece.status,
    )


def _rejected(
    frame: SemanticFrame,
    reason: str,
    *,
    label: str = "",
    kind: ControlKind = ControlKind.ORAL_SURVEY,
    obj: str = "",
    complement: str = "",
    violations: tuple[str, ...] = (),
) -> ControlPiece:
    return ControlPiece(
        id=make_object_id("cpiece", frame.id, reason),
        frame_id=frame.id,
        atom_id=frame.atom_id,
        span=frame.span,
        kind=kind,
        label="",
        source_object=obj or frame.object,
        source_complement=complement or frame.complement,
        provenance=Provenance(adapter=ADAPTER_NAME, role="piece", note=reason),
        lexical_check=LexicalCheckResult(passed=not violations, violations=violations),
        status=ObjectStatus.UNRESOLVED,
        reason=reason,
    )
