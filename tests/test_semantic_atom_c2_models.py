"""C2: shadow models and passthrough projection. No production writers."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from calendar_pedagoga.content_engine_v2 import derive_fields_v2
from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.adapter import project_passthrough_graph
from calendar_pedagoga.semantic_atom.models import (
    CoverageBinding,
    CoverageReport,
    FrameKind,
    ObjectStatus,
    Provenance,
    SemanticFrame,
    SourceAtom,
    SourceClause,
    SourceSpan,
    fingerprint_source,
    make_object_id,
    to_jsonable,
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
MODELS_PATH = PRODUCTION_DIR / "semantic_atom" / "models.py"


def test_c1_oracle_file_is_unchanged() -> None:
    digest = hashlib.sha256(C1_ORACLE.read_bytes()).hexdigest()
    assert digest == C1_ORACLE_SHA256


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_models_module_does_not_import_production() -> None:
    tree = ast.parse(MODELS_PATH.read_text(encoding="utf-8"))
    allowed = {
        "calendar_pedagoga.semantic_atom.canonicalize",
        "calendar_pedagoga.semantic_atom.models",
    }
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if name.startswith("calendar_pedagoga") and name not in allowed:
                raise AssertionError(f"models imported {name}")


def test_production_modules_do_not_import_shadow_models() -> None:
    needles = (
        "SourceAtom",
        "SemanticFrame",
        "CoverageReport",
        "CoverageBinding",
        "project_passthrough_graph",
    )
    for path in PRODUCTION_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{path.name} contains {needle}"


def _span(start: int = 0, end: int = 5, text: str = "рисован") -> SourceSpan:
    fingerprint = fingerprint_source(text, (start, end))
    provenance = Provenance(adapter="passthrough_c2", role="span", clause_index=0)
    return SourceSpan(
        id=make_object_id("span", fingerprint, str(start), str(end)),
        start=start,
        end=end,
        source_fingerprint=fingerprint,
        provenance=provenance,
        status=ObjectStatus.PROJECTED,
        document="",
    )


def test_model_ids_are_deterministic() -> None:
    left = _span()
    right = _span()
    assert left.id == right.id
    assert left.source_fingerprint == right.source_fingerprint
    assert left.id != _span(end=6).id


def test_all_shadow_objects_carry_required_fields() -> None:
    span = _span()
    clause = SourceClause(
        id=make_object_id("clause", span.source_fingerprint, "Рисование деревьев."),
        span=span,
        source_fingerprint=span.source_fingerprint,
        provenance=Provenance(adapter="passthrough_c2", role="clause", clause_index=0),
        status=ObjectStatus.PROJECTED,
        text="Рисование деревьев.",
    )
    atom = SourceAtom(
        id=make_object_id("atom", clause.id, clause.text),
        span=span,
        source_fingerprint=span.source_fingerprint,
        provenance=Provenance(adapter="passthrough_c2", role="atom", clause_index=0),
        status=ObjectStatus.PROJECTED,
        text=clause.text,
        clause_id=clause.id,
        transitional=True,
    )
    frame = SemanticFrame(
        id=make_object_id("frame", atom.id),
        span=span,
        source_fingerprint=span.source_fingerprint,
        provenance=Provenance(adapter="passthrough_c2", role="frame", clause_index=0),
        status=ObjectStatus.PROJECTED,
        kind=FrameKind.PROJECTED,
        atom_id=atom.id,
        clause_id=clause.id,
        projected_type="практическое занятие",
        projected_result="Рисует деревья.",
        projected_control="Просмотр рисунков.",
        coverage_status="COVERED",
    )
    binding = CoverageBinding(
        id=make_object_id("binding", atom.id, frame.id),
        span=span,
        source_fingerprint=span.source_fingerprint,
        provenance=Provenance(adapter="passthrough_c2", role="binding", clause_index=0),
        status=ObjectStatus.COVERED,
        atom_id=atom.id,
        frame_id=frame.id,
    )
    report = CoverageReport(
        id=make_object_id("report", frame.projected_result),
        span=span,
        source_fingerprint=span.source_fingerprint,
        provenance=Provenance(adapter="passthrough_c2", role="report"),
        status=ObjectStatus.PROJECTED,
        clauses=(clause,),
        atoms=(atom,),
        frames=(frame,),
        bindings=(binding,),
        projected_type=frame.projected_type,
        projected_result=frame.projected_result,
        projected_control=frame.projected_control,
    )
    for obj in (span, clause, atom, frame, binding, report):
        assert obj.id
        assert obj.span is span or obj is span
        assert obj.source_fingerprint
        assert obj.provenance.adapter == "passthrough_c2"
        assert obj.status
    assert atom.transitional is True


def test_serialization_roundtrip() -> None:
    produced = derive_fields_v2(
        topic_title="Синтетика",
        theory_text="Рисование деревьев.",
        practice_text="",
        program_content="Рисование деревьев.",
        theory_hours=2,
        practice_hours=0,
    )
    report = project_passthrough_graph(produced, source="Рисование деревьев.")
    payload = to_jsonable(report)
    dumped = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    restored = CoverageReport.from_dict(json.loads(dumped))
    assert to_jsonable(restored) == payload
    assert restored.atoms[0].id == report.atoms[0].id
    assert restored.frames[0].projected_result == report.projected_result


def test_passthrough_projects_one_temporary_atom_per_clause() -> None:
    source = "Рисование деревьев. Что такое компас?"
    produced = derive_fields_v2(
        topic_title="Смесь",
        theory_text="Что такое компас?",
        practice_text="Рисование деревьев.",
        program_content=source,
        theory_hours=1,
        practice_hours=1,
    )
    shadow = run_passthrough_shadow(produced, source=source)
    report = project_passthrough_graph(produced, source=source)
    assert len(report.clauses) == len(produced.clause_coverage)
    assert len(report.atoms) == len(produced.clause_coverage)
    assert len(report.frames) == len(produced.clause_coverage)
    assert len(report.bindings) == len(produced.clause_coverage)
    assert all(atom.transitional for atom in report.atoms)
    assert compare_identity(produced, shadow)[0].kind is DiffKind.EQUAL
    assert report.projected_result == produced.planned_result
    assert report.projected_control == produced.assessment_method
    assert report.projected_type == produced.lesson_type


def test_passthrough_graph_does_not_change_identity() -> None:
    source = "Понятия: ритм, темп, динамика."
    produced = derive_fields_v2(
        topic_title="Теория",
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=2,
        practice_hours=0,
    )
    shadow = run_passthrough_shadow(produced, source=source)
    report = project_passthrough_graph(produced, source=source)
    assert compare_identity(produced, shadow)[0].kind is DiffKind.EQUAL
    assert report.projected_result == shadow.planned_result


def test_production_does_not_call_passthrough_graph() -> None:
    from calendar_pedagoga.content_generation import CalendarContentRow
    from calendar_pedagoga.matching import MatchStatus
    from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2
    from calendar_pedagoga.pipeline import _build_pipeline_lesson_content

    reset_shadow_invocation_count()
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
    build_lesson_content_v2((row,))
    _build_pipeline_lesson_content((row,), use_content_engine_v2=True)
    assert shadow_invocation_count() == 0
