"""ACTION coverage binds only to attested SOURCE spans. No builder changes."""

from __future__ import annotations

from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.diff_adapter import (
    ShadowSnapshot,
    _frame_matches_clause,
    snapshot_shadow,
)
from calendar_pedagoga.semantic_atom.frame_adapter import project_frames
from calendar_pedagoga.semantic_atom.models import (
    CoverageBinding,
    FrameKind,
    ObjectStatus,
    Provenance,
    SemanticFrame,
    SourceAtom,
    SourceSpan,
)
from calendar_pedagoga.semantic_atom.span_cover import (
    action_cover_text,
    action_source_parts,
    narrow_action_frame,
    part_attested_in_frame,
)


def _atom(text: str, atom_id: str = "atom:1") -> SourceAtom:
    span = SourceSpan(
        id=f"span:{text}",
        start=0,
        end=len(text),
        source_fingerprint="fp",
        provenance=Provenance(adapter="t", role="span"),
        status=ObjectStatus.PROJECTED,
    )
    return SourceAtom(
        id=atom_id,
        span=span,
        source_fingerprint=span.source_fingerprint,
        provenance=Provenance(adapter="t", role="atom"),
        status=ObjectStatus.PROJECTED,
        text=text,
        clause_id="clause:1",
    )


def _action_frame(atom: SourceAtom, result: str) -> SemanticFrame:
    return SemanticFrame(
        id="frame:1",
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        provenance=Provenance(adapter="t", role="frame"),
        status=ObjectStatus.PROVEN,
        kind=FrameKind.ACTION,
        atom_id=atom.id,
        clause_id=atom.clause_id,
        projected_type="",
        projected_result=result,
        projected_control="",
        coverage_status="COVERED",
        predicate="",
        object="",
        complement="",
    )


def _shadow(atom: SourceAtom, frame: SemanticFrame) -> ShadowSnapshot:
    return ShadowSnapshot(
        result=frame.projected_result,
        control="",
        atoms=(atom,),
        frames=(frame,),
        bindings=(
            CoverageBinding(
                id="bind:1",
                span=frame.span,
                source_fingerprint=atom.source_fingerprint,
                provenance=Provenance(adapter="t", role="binding"),
                status=ObjectStatus.COVERED,
                atom_id=atom.id,
                frame_id=frame.id,
            ),
        ),
        control_pieces=(),
        control_bindings=(),
    )


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_conjunct_b_stays_uncovered_when_result_has_only_a() -> None:
    source = "Спуск с горы и способы поворота."
    projection = project_frames(source)
    frame = next(item for item in projection.frames if item.kind is FrameKind.ACTION)
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert proven.status is ObjectStatus.PROVEN
    assert "спуск" in proven.projected_result.casefold()
    assert "поворота" not in proven.projected_result.casefold()
    assert _frame_matches_clause("Спуск с горы", proven, shadow)
    assert not _frame_matches_clause("способы поворота", proven, shadow)
    covered = source[proven.span.start : proven.span.end]
    assert "спуск" in covered.casefold()
    assert "поворота" not in covered.casefold()


def test_game_names_stay_uncovered_when_result_has_only_category() -> None:
    source = (
        "Проведение дидактических и ролевых игр: «Давай поговорим», "
        "«Комплимент», игра-фантазия «Если бы я был взрослым…»; "
        "подвижных игр, праздников с участием родителей."
    )
    atom = _atom(source)
    frame = narrow_action_frame(
        atom,
        _action_frame(atom, "Проводит дидактические и ролевые игры."),
    )
    shadow = _shadow(atom, frame)
    assert "давай поговорим" not in frame.projected_result.casefold()
    assert _frame_matches_clause("Проведение дидактических и ролевых игр", frame, shadow)
    assert not _frame_matches_clause("Давай поговорим", frame, shadow)
    assert not _frame_matches_clause("Комплимент", frame, shadow)
    assert not _frame_matches_clause("Если бы я был взрослым", frame, shadow)
    covered = source[frame.span.start : frame.span.end]
    assert "проведение" in covered.casefold()
    assert "давай поговорим" not in covered.casefold()
    assert "комплимент" not in covered.casefold()


def test_second_comma_conjunct_stays_uncovered() -> None:
    source = "Спуск с горы, способы поворота."
    projection = project_frames(source)
    frame = next(item for item in projection.frames if item.kind is FrameKind.ACTION)
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert proven.status is ObjectStatus.PROVEN
    assert "спуск" in proven.projected_result.casefold()
    assert "поворота" not in proven.projected_result.casefold()
    assert _frame_matches_clause("Спуск с горы", proven, shadow)
    assert not _frame_matches_clause("способы поворота", proven, shadow)
    covered = source[proven.span.start : proven.span.end]
    assert "спуск" in covered.casefold()
    assert "поворота" not in covered.casefold()


def test_full_proven_list_stays_covered() -> None:
    source = "Подъем «лесенкой», «ёлочкой»."
    projection = project_frames(source)
    frame = next(item for item in projection.frames if item.kind is FrameKind.ACTION)
    atom = next(item for item in projection.atoms if item.id == frame.atom_id)
    shadow = snapshot_shadow(source)
    proven = next(item for item in shadow.frames if item.id == frame.id)
    assert proven.status is ObjectStatus.PROVEN
    assert "лесенкой" in proven.projected_result.casefold()
    assert "ёлочкой" in proven.projected_result.casefold()
    assert _frame_matches_clause("Подъем «лесенкой»", proven, shadow)
    assert _frame_matches_clause("ёлочкой", proven, shadow)
    assert _frame_matches_clause(source.rstrip("."), proven, shadow)
    assert (frame.span.start, frame.span.end) == (atom.span.start, atom.span.end)
    assert action_cover_text(atom.text, proven) == atom.text


def test_partial_action_uses_exact_span_binding_and_provenance() -> None:
    source = "Спуск с горы, способы поворота."
    projection = project_frames(source)
    atom = projection.atoms[0]
    frame = next(item for item in projection.frames if item.kind is FrameKind.ACTION)
    assert frame.status is ObjectStatus.PROVEN
    assert frame.atom_id == atom.id
    assert frame.span.start >= atom.span.start
    assert frame.span.end <= atom.span.end
    assert (frame.span.start, frame.span.end) != (atom.span.start, atom.span.end)
    covered = source[frame.span.start : frame.span.end]
    assert "спуск" in covered.casefold()
    assert "поворота" not in covered.casefold()
    bound = [
        item
        for item in projection.bindings
        if item.frame_id == frame.id and item.atom_id == frame.atom_id
    ]
    assert bound
    assert bound[0].span.start == frame.span.start
    assert bound[0].span.end == frame.span.end
    assert bound[0].provenance.role == "binding"
    assert frame.provenance.role
    assert frame.source_fingerprint == atom.source_fingerprint
    assert bound[0].source_fingerprint == atom.source_fingerprint


def test_source_inflection_still_attests_used_span() -> None:
    source = "Спуск с горы, способы поворота."
    frame = project_frames(source).frames[0]
    assert part_attested_in_frame("спуска", frame)
    assert part_attested_in_frame("Спуск с горы", frame)
    assert not part_attested_in_frame("способы поворота", frame)
    parts = action_source_parts(source)
    assert any("спуск" in part.casefold() for part in parts)
    assert any("поворота" in part.casefold() for part in parts)
