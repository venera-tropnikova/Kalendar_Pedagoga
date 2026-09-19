"""C6: SemanticFrameDispatcher infrastructure. No new language constructions."""

from __future__ import annotations

import ast
import hashlib
from itertools import permutations
from pathlib import Path

from calendar_pedagoga.content_engine_v2 import derive_fields_v2
from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.pipeline import _build_pipeline_lesson_content
from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.adapter import project_passthrough_graph
from calendar_pedagoga.semantic_atom.atom_adapter import atomize
from calendar_pedagoga.semantic_atom.dispatcher import (
    AMBIGUOUS_FRAME_CANDIDATES,
    BUILDER_ERROR,
    SemanticFrameDispatcher,
    UNSUPPORTED_ATOM_SHAPE,
    RegisteredBuilder,
)
from calendar_pedagoga.semantic_atom.frame_adapter import (
    C5_REGISTRY,
    LEXICAL_VIOLATION,
    default_dispatcher,
    project_frames,
    reset_frame_adapter_calls,
)
from calendar_pedagoga.semantic_atom.lexical import shadow_lexical_violations
from calendar_pedagoga.semantic_atom.models import (
    CandidateConfidence,
    FrameCandidate,
    FrameKind,
    LexicalCheckResult,
    ObjectStatus,
    SourceAtom,
    StructuralEvidence,
)
from calendar_pedagoga.semantic_atom.passthrough import (
    DiffKind,
    compare_identity,
    reset_shadow_invocation_count,
    run_passthrough_shadow,
    shadow_invocation_count,
)

ROOT = Path(__file__).resolve().parents[1]
C1_ORACLE = ROOT / "tests" / "oracles" / "semantic_atom_7a714d6.json"
C1_ORACLE_SHA256 = "432ff7b694dc2e235c5446769f4779c4b7f9498b9ebda3a57a931c9c7e059344"
PRODUCTION_DIR = ROOT / "src" / "calendar_pedagoga"
DISPATCHER_PATH = PRODUCTION_DIR / "semantic_atom" / "dispatcher.py"
MODELS_PATH = PRODUCTION_DIR / "semantic_atom" / "models.py"


def test_c1_oracle_file_is_unchanged() -> None:
    assert hashlib.sha256(C1_ORACLE.read_bytes()).hexdigest() == C1_ORACLE_SHA256


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_dispatcher_avoids_production_imports() -> None:
    allowed = {
        "calendar_pedagoga.semantic_atom.canonicalize",
        "calendar_pedagoga.semantic_atom.models",
        "calendar_pedagoga.semantic_atom.dispatcher",
    }
    for path in (DISPATCHER_PATH, MODELS_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith("calendar_pedagoga") and name not in allowed:
                    raise AssertionError(f"{path.name} imported {name}")


def test_production_does_not_import_dispatcher() -> None:
    reset_frame_adapter_calls()
    reset_shadow_invocation_count()
    needles = (
        "SemanticFrameDispatcher",
        "FrameCandidate",
        "dispatch_atom",
        "decide_frame",
    )
    for path in PRODUCTION_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{path.name} contains {needle}"
    row = CalendarContentRow(
        week_number=1,
        date_range="01–07.09",
        month="Сентябрь",
        section="Раздел",
        topic_number="1.1",
        topic_title="Тема",
        source_topic_title="Тема",
        theory_hours=0,
        practice_hours=2,
        total_hours=2,
        match_status=MatchStatus.EXACT,
        program_section="Раздел",
        program_topic="Тема",
        program_content_full="Рисование деревьев.",
        program_content_preview="Рисование деревьев.",
        source_program_name="Программа",
        source_utp_name="utp.docx",
        warnings=(),
    )
    _build_pipeline_lesson_content((row,), use_content_engine_v2=True)
    assert shadow_invocation_count() == 0


def _atom(source: str = "Животные и птицы в рисунках детей.") -> SourceAtom:
    return atomize(source).atoms[0]


def _candidate(
    atom: SourceAtom,
    builder_id: str,
    *,
    kind: FrameKind = FrameKind.ACTION,
    predicate: str = "рисование",
    obj: str = "животных и птиц",
    complement: str = "",
    result: str = "Рисует животных и птиц",
    valid: bool = True,
    reason: str = "",
    violations: tuple[str, ...] = (),
    specificity: int = 0,
    error: bool = False,
) -> FrameCandidate:
    if error:
        confidence = CandidateConfidence.ERROR
        reason = reason or BUILDER_ERROR
    elif valid:
        confidence = CandidateConfidence.VALID
    else:
        confidence = CandidateConfidence.REJECTED
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
        proposed_control="",
        structural_evidence=StructuralEvidence(
            notes=(builder_id,), specificity=specificity
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


def test_zero_valid_candidates_are_unsupported() -> None:
    atom = _atom()
    decision = SemanticFrameDispatcher(()).dispatch_atom(atom)
    assert decision.frame.status is ObjectStatus.UNRESOLVED
    assert decision.frame.reason == UNSUPPORTED_ATOM_SHAPE
    assert decision.candidates == ()


def test_one_valid_candidate_is_proven() -> None:
    atom = _atom()
    cand = _candidate(atom, "only")
    decision = SemanticFrameDispatcher((_const(cand),)).dispatch_atom(atom)
    assert decision.frame.status is ObjectStatus.PROVEN
    assert decision.frame.projected_result == cand.proposed_result
    assert decision.frame.kind is FrameKind.ACTION
    assert decision.frame.span.start == atom.span.start
    assert decision.frame.span.end == atom.span.end


def test_equivalent_candidates_merge_evidence() -> None:
    atom = _atom()
    left = _candidate(atom, "alpha", specificity=1)
    right = _candidate(atom, "beta", specificity=0)
    decision = SemanticFrameDispatcher(
        (_const(right), _const(left))
    ).dispatch_atom(atom)
    assert decision.frame.status is ObjectStatus.PROVEN
    assert decision.frame.projected_result == left.proposed_result
    assert "alpha" in decision.frame.provenance.note
    assert "beta" in decision.frame.provenance.note


def test_conflicting_candidates_stay_unresolved() -> None:
    atom = _atom()
    action = _candidate(atom, "draw", specificity=9)
    knowledge = _candidate(
        atom,
        "name",
        kind=FrameKind.KNOWLEDGE,
        predicate="называет",
        obj="флаг символом государства",
        result="Называет флаг символом государства.",
        specificity=0,
    )
    decision = SemanticFrameDispatcher(
        (_const(action), _const(knowledge))
    ).dispatch_atom(atom)
    assert decision.frame.status is ObjectStatus.UNRESOLVED
    assert decision.frame.reason == AMBIGUOUS_FRAME_CANDIDATES
    assert decision.frame.projected_result == ""


def test_registry_order_does_not_change_decision() -> None:
    source = "Понятия: ритм, темп, динамика."
    results = []
    ids = []
    for order in permutations(C5_REGISTRY):
        projection = project_frames(
            source, dispatcher=SemanticFrameDispatcher(order)
        )
        frame = projection.frames[0]
        results.append((frame.status, frame.projected_result, frame.kind))
        ids.append(frame.id)
    assert len(set(results)) == 1
    assert len(set(ids)) == 1
    assert results[0][0] is ObjectStatus.PROVEN


def test_false_first_candidate_does_not_suppress_proven() -> None:
    atom = _atom()
    false = _candidate(
        atom,
        "false_first",
        valid=False,
        reason=LEXICAL_VIOLATION,
        violations=("квантовый",),
        obj="квантовый лес",
        result="Рисует квантовый лес",
    )
    proven = _candidate(atom, "true_locative")
    decision = SemanticFrameDispatcher(
        (_const(false), _const(proven))
    ).dispatch_atom(atom)
    assert decision.frame.status is ObjectStatus.PROVEN
    assert decision.frame.projected_result == proven.proposed_result
    assert [item.builder_id for item in decision.candidates] == [
        "false_first",
        "true_locative",
    ]


def test_lexical_violation_rejects_only_its_candidate() -> None:
    atom = _atom()
    bad = _candidate(
        atom,
        "leaky",
        valid=False,
        reason=LEXICAL_VIOLATION,
        violations=("квантовый",),
    )
    good = _candidate(atom, "sound")
    decision = SemanticFrameDispatcher((_const(bad), _const(good))).dispatch_atom(atom)
    rejected = next(item for item in decision.candidates if item.builder_id == "leaky")
    kept = next(item for item in decision.candidates if item.builder_id == "sound")
    assert rejected.confidence is CandidateConfidence.REJECTED
    assert rejected.rejection_reason == LEXICAL_VIOLATION
    assert kept.is_valid
    assert decision.frame.status is ObjectStatus.PROVEN


def test_builder_exception_does_not_break_the_row() -> None:
    atom = _atom()
    good = _candidate(atom, "sound")

    def boom(_atom: SourceAtom) -> FrameCandidate:
        raise RuntimeError("broken builder")

    decision = SemanticFrameDispatcher(
        (
            RegisteredBuilder("boom", boom),
            _const(good),
        )
    ).dispatch_atom(atom)
    assert decision.frame.status is ObjectStatus.PROVEN
    error = next(item for item in decision.candidates if item.builder_id == "boom")
    assert error.confidence is CandidateConfidence.ERROR
    assert "RuntimeError" in error.structural_evidence.notes[0]

    only_error = SemanticFrameDispatcher(
        (RegisteredBuilder("boom", boom),)
    ).dispatch_atom(atom)
    assert only_error.frame.status is ObjectStatus.UNRESOLVED
    assert only_error.frame.reason == BUILDER_ERROR


def test_neighbor_atoms_are_independent() -> None:
    source = "Памятники города. Животные и птицы в рисунках детей."
    projection = project_frames(source)
    assert [frame.status for frame in projection.frames] == [
        ObjectStatus.UNRESOLVED,
        ObjectStatus.PROVEN,
    ]
    assert projection.frames[0].reason == UNSUPPORTED_ATOM_SHAPE
    assert projection.frames[1].projected_result.startswith("Рисует животных и птиц")
    lone = project_frames("Животные и птицы в рисунках детей.").frames[0]
    assert projection.frames[1].projected_result == lone.projected_result


def test_c5_registry_results_are_unchanged() -> None:
    cases = (
        ("Животные и птицы в рисунках детей.", "Рисует животных и птиц"),
        ("Понятия: ритм, темп, динамика.", "Объясняет значения понятий"),
        ("Для чего нужны карта, компас и фонарь?", "Объясняет функции"),
        ("Инструменты: духовые и ударные.", "Различает духовые и ударные"),
        ("Флаг — символ государства.", "Называет флаг символом государства."),
        ("Запрещающие знаки «Берегите природу».", "Распознаёт запрещающие знаки"),
    )
    for source, prefix in cases:
        frame = project_frames(source).frames[0]
        assert frame.status is ObjectStatus.PROVEN, source
        assert frame.projected_result.startswith(prefix), source


def test_identity_old_stays_equal() -> None:
    source = "Понятия: ритм, темп, динамика."
    row = derive_fields_v2(
        topic_title="Теория",
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=2,
        practice_hours=0,
    )
    shadow = run_passthrough_shadow(row, source=source)
    report = project_passthrough_graph(row, source=source)
    projection = project_frames(source, row)
    assert compare_identity(row, shadow)[0].kind is DiffKind.EQUAL
    assert report.projected_result == row.planned_result
    assert projection.identity_result == row.planned_result
    assert projection.candidate_result
    assert projection.candidate_result != "" or projection.frames[0].status is ObjectStatus.PROVEN


def test_default_dispatcher_uses_c5_registry() -> None:
    assert default_dispatcher().builder_ids == tuple(
        item.builder_id for item in C5_REGISTRY
    )


def test_lexical_helper_still_reports_invented_lexeme() -> None:
    violations = shadow_lexical_violations(
        source="Животные и птицы в рисунках детей.",
        result="Рисует квантовый лес",
        control="",
    )
    assert "квантовый" in violations
