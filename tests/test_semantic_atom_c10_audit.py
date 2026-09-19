"""C10: full shadow differential audit. Measurement only."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.pipeline import _build_pipeline_lesson_content
from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.program_parsing import ProgramContentItem, ProgramData
from calendar_pedagoga.semantic_atom.audit import (
    BLOCKED_IMPORT,
    BLOCKED_PLAN,
    LANE_BLOCKED,
    LANE_DIAGNOSTIC,
    LANE_PRODUCTION,
    MISSING_UTP,
    CorpusPair,
    accounted_document_names,
    assess_pair,
    assert_report_safe,
    build_audit_report,
    discover_corpus_pairs,
    document_names,
    report_json,
    run_differential_audit,
    sanitize_text,
)
from calendar_pedagoga.semantic_atom.diff_adapter import (
    CONTROL_WITHOUT_BINDING,
    FRAME_WITHOUT_BINDING,
    OLD_COVERED_SHADOW_UNRESOLVED,
    SEVERITY_ORDER,
    TITLE_ONLY,
    OldSnapshot,
    ShadowSnapshot,
    classify_snapshots,
    severity,
    worst_kind,
)
from calendar_pedagoga.semantic_atom.models import (
    ControlBinding,
    ControlKind,
    ControlPiece,
    CoverageBinding,
    FrameKind,
    LexicalCheckResult,
    ObjectStatus,
    Provenance,
    SemanticFrame,
    SourceAtom,
    SourceSpan,
)
from calendar_pedagoga.semantic_atom.oracle import REQUIRED_FIXTURES
from calendar_pedagoga.semantic_atom.passthrough import (
    DiffKind,
    reset_shadow_invocation_count,
    shadow_invocation_count,
)

ROOT = Path(__file__).resolve().parents[1]
C1_ORACLE = ROOT / "tests" / "oracles" / "semantic_atom_7a714d6.json"
C1_ORACLE_SHA256 = "432ff7b694dc2e235c5446769f4779c4b7f9498b9ebda3a57a931c9c7e059344"
PRODUCTION_DIR = ROOT / "src" / "calendar_pedagoga"


def test_c1_oracle_file_is_unchanged() -> None:
    assert hashlib.sha256(C1_ORACLE.read_bytes()).hexdigest() == C1_ORACLE_SHA256


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_production_does_not_import_c10() -> None:
    reset_shadow_invocation_count()
    needles = (
        "run_differential_audit",
        "classify_snapshots",
        "diff_adapter",
        "semantic_atom.audit",
    )
    for path in PRODUCTION_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{path.name} contains {needle}"
    tree = ast.parse((PRODUCTION_DIR / "pipeline.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "semantic_atom" not in node.module
    row = CalendarContentRow(
        week_number=1,
        date_range="01–07.09",
        month="Сентябрь",
        section="Раздел",
        topic_number="1.1",
        topic_title="Тема",
        source_topic_title="Тема",
        theory_hours=2,
        practice_hours=0,
        total_hours=2,
        match_status=MatchStatus.EXACT,
        program_section="Раздел",
        program_topic="Тема",
        program_content_full="Основные сведения о крае.",
        program_content_preview="Основные сведения о крае.",
        source_program_name="Программа",
        source_utp_name="utp.docx",
        warnings=(),
    )
    _build_pipeline_lesson_content((row,), use_content_engine_v2=True)
    assert shadow_invocation_count() == 0
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_severity_priority_order() -> None:
    assert SEVERITY_ORDER[:5] == (
        DiffKind.NEW_INVENTS,
        DiffKind.NEW_LOSES,
        DiffKind.UNRESOLVED_DRIFT,
        DiffKind.NEW_COVERS_MORE,
        DiffKind.EQUAL,
    )
    assert DiffKind.BLOCKED in SEVERITY_ORDER
    assert severity(DiffKind.NEW_INVENTS) < severity(DiffKind.NEW_LOSES)
    assert severity(DiffKind.NEW_LOSES) < severity(DiffKind.UNRESOLVED_DRIFT)
    assert severity(DiffKind.UNRESOLVED_DRIFT) < severity(DiffKind.NEW_COVERS_MORE)
    assert severity(DiffKind.NEW_COVERS_MORE) < severity(DiffKind.EQUAL)


def test_worst_kind_prefers_higher_severity() -> None:
    assert worst_kind((DiffKind.NEW_COVERS_MORE, DiffKind.NEW_LOSES)) is DiffKind.NEW_LOSES
    assert worst_kind((DiffKind.NEW_INVENTS, DiffKind.NEW_LOSES)) is DiffKind.NEW_INVENTS
    assert worst_kind((DiffKind.EQUAL, DiffKind.UNRESOLVED_DRIFT)) is DiffKind.UNRESOLVED_DRIFT
    assert worst_kind((DiffKind.BLOCKED, DiffKind.NEW_COVERS_MORE)) is DiffKind.NEW_COVERS_MORE


def _span(text: str) -> SourceSpan:
    return SourceSpan(
        id=f"span:{text}",
        start=0,
        end=len(text),
        source_fingerprint="fp",
        provenance=Provenance(adapter="t", role="span"),
        status=ObjectStatus.PROJECTED,
    )


def _atom(text: str, atom_id: str = "atom:1") -> SourceAtom:
    span = _span(text)
    return SourceAtom(
        id=atom_id,
        span=span,
        source_fingerprint=span.source_fingerprint,
        provenance=Provenance(adapter="t", role="atom"),
        status=ObjectStatus.PROJECTED,
        text=text,
        clause_id="clause:1",
    )


def _frame(
    atom: SourceAtom,
    *,
    status: ObjectStatus,
    result: str = "",
    reason: str = "",
    atom_id: str | None = None,
    frame_id: str = "frame:1",
) -> SemanticFrame:
    return SemanticFrame(
        id=frame_id,
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        provenance=Provenance(adapter="t", role="frame"),
        status=status,
        kind=FrameKind.ACTION if status is ObjectStatus.PROVEN else FrameKind.PROJECTED,
        atom_id=atom.id if atom_id is None else atom_id,
        clause_id=atom.clause_id,
        projected_type="",
        projected_result=result,
        projected_control="",
        coverage_status="COVERED" if status is ObjectStatus.PROVEN else "UNRESOLVED",
        reason=reason,
    )


def _binding(atom: SourceAtom, frame: SemanticFrame) -> CoverageBinding:
    return CoverageBinding(
        id=f"bind:{frame.id}",
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        provenance=Provenance(adapter="t", role="binding"),
        status=ObjectStatus.COVERED if frame.status is ObjectStatus.PROVEN else ObjectStatus.UNRESOLVED,
        atom_id=atom.id,
        frame_id=frame.id,
    )


def _piece(frame: SemanticFrame, *, atom_id: str | None = None, piece_id: str = "piece:1") -> ControlPiece:
    return ControlPiece(
        id=piece_id,
        frame_id=frame.id,
        atom_id=frame.atom_id if atom_id is None else atom_id,
        span=frame.span,
        kind=ControlKind.PRODUCT_REVIEW,
        label="Просмотр рисунков.",
        source_object="",
        source_complement="",
        provenance=Provenance(adapter="t", role="piece"),
        lexical_check=LexicalCheckResult(passed=True),
        status=ObjectStatus.PROVEN,
    )


def _control_binding(piece: ControlPiece) -> ControlBinding:
    return ControlBinding(
        id=f"cbind:{piece.id}",
        piece_id=piece.id,
        frame_id=piece.frame_id,
        atom_id=piece.atom_id,
        span=piece.span,
        provenance=Provenance(adapter="t", role="binding"),
        status=piece.status,
    )


def _covered_shadow(text: str = "Рисование деревьев.", result: str = "Рисует деревья.") -> ShadowSnapshot:
    atom = _atom(text)
    frame = _frame(atom, status=ObjectStatus.PROVEN, result=result)
    piece = _piece(frame)
    return ShadowSnapshot(
        result=result,
        control="Просмотр рисунков.",
        atoms=(atom,),
        frames=(frame,),
        bindings=(_binding(atom, frame),),
        control_pieces=(piece,),
        control_bindings=(_control_binding(piece),),
    )


def test_all_diff_classes_are_emitted() -> None:
    clause = "Рисование деревьев."
    equal_old = OldSnapshot(
        result="Рисует деревья.",
        control="Просмотр рисунков.",
        coverage=((clause, "COVERED"),),
        source=clause,
        week=1,
    )
    kinds = {
        classify_snapshots(equal_old, _covered_shadow()).kind,
        classify_snapshots(
            OldSnapshot(result="", control="", coverage=((clause, "NEEDS_REVIEW"),), source=clause),
            _covered_shadow(),
        ).kind,
        classify_snapshots(
            OldSnapshot(
                result="Рисует деревья.",
                control="Просмотр рисунков.",
                coverage=((clause, "COVERED"),),
                source=clause,
            ),
            ShadowSnapshot(
                result="",
                control="",
                atoms=(_atom(clause),),
                frames=(
                    _frame(
                        _atom(clause),
                        status=ObjectStatus.UNRESOLVED,
                        reason="unsupported_atom_shape",
                    ),
                ),
            ),
        ).kind,
        classify_snapshots(
            equal_old,
            ShadowSnapshot(
                result="Объясняет квантовую тему.",
                control="Устный опрос.",
                atoms=(_atom(clause),),
                frames=(_frame(_atom(clause), status=ObjectStatus.PROVEN, result="Рисует деревья."),),
                bindings=(_binding(_atom(clause), _frame(_atom(clause), status=ObjectStatus.PROVEN, result="Рисует деревья.")),),
                lexical_violations=("квантовую",),
            ),
        ).kind,
        classify_snapshots(
            equal_old,
            _covered_shadow(result="Рисует осенние деревья."),
        ).kind,
        classify_snapshots(
            OldSnapshot(result="", control="", topic_title="Тема недели", source=""),
            ShadowSnapshot(result="", control=""),
        ).kind,
    }
    assert kinds == {
        DiffKind.EQUAL,
        DiffKind.NEW_COVERS_MORE,
        DiffKind.NEW_LOSES,
        DiffKind.NEW_INVENTS,
        DiffKind.UNRESOLVED_DRIFT,
        DiffKind.BLOCKED,
    }


def test_frame_without_binding_is_new_invents() -> None:
    atom = _atom("Рисование деревьев.")
    frame = _frame(atom, status=ObjectStatus.PROVEN, result="Рисует деревья.", atom_id="")
    diff = classify_snapshots(
        OldSnapshot(result="", control="", coverage=(("Рисование деревьев.", "NEEDS_REVIEW"),), source=atom.text),
        ShadowSnapshot(result="Рисует деревья.", control="", atoms=(atom,), frames=(frame,), bindings=()),
    )
    assert diff.kind is DiffKind.NEW_INVENTS
    assert FRAME_WITHOUT_BINDING in diff.reasons


def test_control_without_binding_is_new_invents() -> None:
    atom = _atom("Рисование деревьев.")
    frame = _frame(atom, status=ObjectStatus.PROVEN, result="Рисует деревья.")
    piece = _piece(frame)
    diff = classify_snapshots(
        OldSnapshot(
            result="Рисует деревья.",
            control="",
            coverage=(("Рисование деревьев.", "COVERED"),),
            source=atom.text,
        ),
        ShadowSnapshot(
            result="Рисует деревья.",
            control="Просмотр рисунков.",
            atoms=(atom,),
            frames=(frame,),
            bindings=(_binding(atom, frame),),
            control_pieces=(piece,),
            control_bindings=(),
        ),
    )
    assert diff.kind is DiffKind.NEW_INVENTS
    assert CONTROL_WITHOUT_BINDING in diff.reasons


def test_old_covered_shadow_unresolved_is_new_loses() -> None:
    atom = _atom("Рисование деревьев.")
    frame = _frame(atom, status=ObjectStatus.UNRESOLVED, reason="unsupported_atom_shape")
    diff = classify_snapshots(
        OldSnapshot(
            result="Рисует деревья.",
            control="Просмотр рисунков.",
            coverage=(("Рисование деревьев.", "COVERED"),),
            source=atom.text,
        ),
        ShadowSnapshot(result="", control="", atoms=(atom,), frames=(frame,), bindings=(_binding(atom, frame),)),
    )
    assert diff.kind is DiffKind.NEW_LOSES
    assert OLD_COVERED_SHADOW_UNRESOLVED in diff.reasons


def test_title_only_without_source_is_blocked() -> None:
    diff = classify_snapshots(
        OldSnapshot(result="", control="", topic_title="Ориентирование", source=""),
        ShadowSnapshot(result="Ориентируется на местности.", control="Устный опрос по теме."),
    )
    assert diff.kind is DiffKind.BLOCKED
    assert diff.reasons == (TITLE_ONLY,)


def test_unresolved_grouped_by_structural_reason() -> None:
    atom = _atom("Рисование деревьев.")
    frame = _frame(atom, status=ObjectStatus.UNRESOLVED, reason="unsupported_atom_shape")
    diff = classify_snapshots(
        OldSnapshot(
            result="Рисует деревья.",
            control="",
            coverage=(("Рисование деревьев.", "COVERED"),),
            source=atom.text,
            week=17,
        ),
        ShadowSnapshot(result="", control="", atoms=(atom,), frames=(frame,)),
    )
    assert "unsupported_atom_shape" in diff.unresolved_reasons
    assert "17" not in diff.unresolved_reasons


def test_report_is_deterministic() -> None:
    pair = CorpusPair(id="synth", program="p.doc", utp="u.docx", study_year=1, run_status="RUNNABLE")
    atom = _atom("Рисование деревьев.")
    frame = _frame(atom, status=ObjectStatus.UNRESOLVED, reason="unsupported_atom_shape")
    week = classify_snapshots(
        OldSnapshot(
            result="Рисует деревья.",
            control="",
            coverage=(("Рисование деревьев.", "COVERED"),),
            source=atom.text,
            week=1,
        ),
        ShadowSnapshot(result="", control="", atoms=(atom,), frames=(frame,)),
    )
    left = build_audit_report((pair,), {"synth": (week,)})
    right = build_audit_report((pair,), {"synth": (week,)})
    assert report_json(left) == report_json(right)
    assert json.loads(report_json(left)) == json.loads(report_json(right))


def test_public_report_omits_source_fio_and_paths() -> None:
    dirty = sanitize_text(r"ошибка в C:\Users\tropn\secret.docx ФИО педагога")
    assert "<path>" in dirty
    assert "C:\\Users" not in dirty
    pair = CorpusPair(
        id="synth",
        program="p.doc",
        utp="u.docx",
        study_year=1,
        run_status="BLOCKED",
        block_reason="missing_fixture",
        error_type="FileNotFoundError",
        error=r"C:\Users\tropn\hidden.docx",
    )
    report = build_audit_report((pair,), {})
    blob = report_json(report)
    assert "SOURCE" not in blob
    assert "C:\\Users" not in blob
    assert "фио" not in blob.casefold()
    assert_report_safe(report)


def test_corpus_manifest_has_no_silent_skips(tmp_path: Path) -> None:
    (tmp_path / "Программа Alpha.doc").write_bytes(b"alpha")
    (tmp_path / "УТП Alpha 1 г.docx").write_bytes(b"utp")
    (tmp_path / "Программа Orphan.doc").write_bytes(b"orphan")
    (tmp_path / "Календарный план.docx").write_bytes(b"cal")
    pairs = discover_corpus_pairs(roots=(tmp_path,), include_required=False)
    names = set(document_names(roots=(tmp_path,)))
    assert "Календарный план.docx" not in names
    assert names <= accounted_document_names(pairs)
    assert any(pair.program == "Программа Alpha.doc" and pair.utp.startswith("УТП Alpha") for pair in pairs)
    orphans = [pair for pair in pairs if pair.program and not pair.utp]
    assert orphans
    assert all(not pair.block_reason for pair in orphans)
    assessed_orphans = [assess_pair(pair, roots=(tmp_path,)) for pair in orphans]
    assert all(item.run_status == BLOCKED_IMPORT for item in assessed_orphans)
    assert all(item.block_reason != MISSING_UTP for item in assessed_orphans)
    (tmp_path / "Юные туристы-спелеологи.docx").write_bytes(b"speleo")
    (tmp_path / "УТП ТП 3г. 2ч.docx").write_bytes(b"tour")
    (tmp_path / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx").write_bytes(b"guide")
    pairs = discover_corpus_pairs(roots=(tmp_path,), include_required=False)
    tour_pairs = [pair for pair in pairs if pair.utp.startswith("УТП ТП")]
    assert all("проводн" in pair.program.casefold() for pair in tour_pairs)
    assert any(not pair.utp and "спелеоло" in pair.program.casefold() for pair in pairs)


def test_c11_blocked_when_new_loses_or_invents() -> None:
    pair = CorpusPair(id="synth", program="p.doc", utp="u.docx", study_year=1, run_status="RUNNABLE")
    atom = _atom("Рисование деревьев.")
    week = classify_snapshots(
        OldSnapshot(
            result="Рисует деревья.",
            control="",
            coverage=(("Рисование деревьев.", "COVERED"),),
            source=atom.text,
            week=1,
        ),
        ShadowSnapshot(
            result="",
            control="",
            atoms=(atom,),
            frames=(_frame(atom, status=ObjectStatus.UNRESOLVED, reason="unsupported_atom_shape"),),
        ),
    )
    report = build_audit_report((pair,), {"synth": (week,)})
    assert report["c11_status"] == "BLOCKED"
    assert "NEW_LOSES" in report["c11_blockers"]
    assert report["aggregates"][LANE_PRODUCTION]["diff"]["NEW_LOSES"] == 1


def test_live_corpus_manifest_lists_every_document() -> None:
    pairs = discover_corpus_pairs()
    names = set(document_names())
    missing = sorted(names - accounted_document_names(pairs))
    assert missing == []
    required = {item["id"] for item in REQUIRED_FIXTURES}
    assert required <= {pair.id for pair in pairs}
    assert all(pair.id for pair in pairs)


def _recognized_program_data() -> ProgramData:
    return ProgramData(
        title="Программа",
        duration=None,
        student_age=None,
        goal="цель",
        tasks=(),
        lesson_forms=(),
        teaching_methods=(),
        expected_results=(),
        knowledge_outcomes=(),
        skill_outcomes=(),
        content_items=(ProgramContentItem(None, "Тема", "Содержание темы."),),
    )


def test_missing_utp_only_after_recognized_program(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "Программа Orphan.docx").write_bytes(b"program")
    pair = CorpusPair(id="orphan", program="Программа Orphan.docx", utp="", study_year=1)
    monkeypatch.setattr(
        "calendar_pedagoga.semantic_atom.audit.parse_program",
        lambda *args, **kwargs: _recognized_program_data(),
    )
    assessed = assess_pair(pair, roots=(tmp_path,))
    assert assessed.run_status == "BLOCKED"
    assert assessed.block_reason == MISSING_UTP
    assert assessed.analysis_lane == LANE_BLOCKED


def test_unrecognized_document_is_blocked_import(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "Программа Orphan.docx").write_bytes(b"garbage")
    pair = CorpusPair(id="bad", program="Программа Orphan.docx", utp="", study_year=1)
    monkeypatch.setattr(
        "calendar_pedagoga.semantic_atom.audit.parse_program",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("нет оглавления программы")),
    )
    assessed = assess_pair(pair, roots=(tmp_path,))
    assert assessed.run_status == BLOCKED_IMPORT
    assert assessed.error_type == "ValueError"
    assert "оглавления" in assessed.block_reason
    assert assessed.block_reason != MISSING_UTP
    assert assessed.analysis_lane == LANE_BLOCKED


def test_empty_parse_is_not_missing_utp(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "Программа Orphan.docx").write_bytes(b"empty")
    pair = CorpusPair(id="empty", program="Программа Orphan.docx", utp="", study_year=1)
    empty = _recognized_program_data()
    empty = ProgramData(
        title=empty.title,
        duration=None,
        student_age=None,
        goal=empty.goal,
        tasks=(),
        lesson_forms=(),
        teaching_methods=(),
        expected_results=(),
        knowledge_outcomes=(),
        skill_outcomes=(),
        content_items=(),
    )
    monkeypatch.setattr(
        "calendar_pedagoga.semantic_atom.audit.parse_program",
        lambda *args, **kwargs: empty,
    )
    assessed = assess_pair(pair, roots=(tmp_path,))
    assert assessed.run_status == BLOCKED_IMPORT
    assert assessed.block_reason != MISSING_UTP


def test_confirmed_plan_error_is_blocked_plan_diagnostic(tmp_path: Path, monkeypatch) -> None:
    from calendar_pedagoga.confirmed_study_plan import ConfirmedStudyPlanError
    from calendar_pedagoga.parsing import Hours, Topic, UtpMetadata, UtpParseResult
    from calendar_pedagoga.semantic_atom import audit as audit_mod

    (tmp_path / "p.docx").write_bytes(b"p")
    (tmp_path / "u.docx").write_bytes(b"u")
    pair = CorpusPair(id="plan", program="p.docx", utp="u.docx", study_year=3)
    parsed = UtpParseResult(
        metadata=UtpMetadata(study_year="3"),
        sections=(),
        topics=(Topic("1", "Тема", Hours(2, 1, 1)),),
        table_totals=Hours(2, 1, 1),
    )
    monkeypatch.setattr(audit_mod, "parse_utp", lambda path: parsed)
    monkeypatch.setattr(audit_mod, "parse_program", lambda *args, **kwargs: _recognized_program_data())
    monkeypatch.setattr(
        audit_mod,
        "confirmed_plan_from_external_utp",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ConfirmedStudyPlanError(
                "Годовой итог внешнего УТП не совпадает с итогом таблицы тем и часов."
            )
        ),
    )
    monkeypatch.setattr(audit_mod, "build_schedule", lambda *args, **kwargs: object())
    monkeypatch.setattr(audit_mod, "build_content_model", lambda *args, **kwargs: object())
    monkeypatch.setattr(audit_mod, "build_lesson_content_v2", lambda content: (object(),))
    assessed = assess_pair(pair, roots=(tmp_path,))
    assert assessed.run_status == BLOCKED_PLAN
    assert assessed.analysis_lane == LANE_DIAGNOSTIC
    assert assessed.error_type == "ConfirmedStudyPlanError"
    assert "итог" in assessed.block_reason.casefold()


def test_cutover_metrics_ignore_diagnostic_only() -> None:
    clause = "Рисование деревьев."
    equal = classify_snapshots(
        OldSnapshot(
            result="Рисует деревья.",
            control="Просмотр рисунков.",
            coverage=((clause, "COVERED"),),
            source=clause,
            week=1,
        ),
        _covered_shadow(),
    )
    invent = classify_snapshots(
        OldSnapshot(result="", control="", coverage=((clause, "NEEDS_REVIEW"),), source=clause, week=2),
        ShadowSnapshot(
            result="Объясняет квантовую тему.",
            control="",
            atoms=(_atom(clause),),
            frames=(_frame(_atom(clause), status=ObjectStatus.PROVEN, result="Рисует деревья."),),
            bindings=(
                _binding(
                    _atom(clause),
                    _frame(_atom(clause), status=ObjectStatus.PROVEN, result="Рисует деревья."),
                ),
            ),
            lexical_violations=("квантовую",),
        ),
    )
    production = CorpusPair(
        id="prod",
        program="p.doc",
        utp="u.docx",
        study_year=1,
        run_status="RUNNABLE",
        analysis_lane=LANE_PRODUCTION,
    )
    diagnostic = CorpusPair(
        id="diag",
        program="p2.doc",
        utp="u2.docx",
        study_year=3,
        run_status=BLOCKED_PLAN,
        analysis_lane=LANE_DIAGNOSTIC,
        error_type="ConfirmedStudyPlanError",
        block_reason="Годовой итог внешнего УТП не совпадает с итогом таблицы тем и часов.",
    )
    report = build_audit_report(
        (production, diagnostic),
        {"prod": (equal,), "diag": (invent,)},
    )
    assert report["c11_status"] == "READY"
    assert report["c11_blockers"] == []
    assert report["aggregates"][LANE_PRODUCTION]["diff"]["NEW_INVENTS"] == 0
    assert report["aggregates"][LANE_DIAGNOSTIC]["diff"]["NEW_INVENTS"] == 1
    assert report["aggregates"][LANE_PRODUCTION]["pairs"] == 1
    assert report["aggregates"][LANE_DIAGNOSTIC]["pairs"] == 1


def test_live_audit_classifies_every_pair() -> None:
    report = run_differential_audit()
    assert report["flag"] is False
    assert USE_SEMANTIC_ATOM_ENGINE is False
    assert_report_safe(report)
    statuses = {item["run_status"] for item in report["manifest"]}
    assert statuses <= {"RUNNABLE", "BLOCKED", BLOCKED_IMPORT, BLOCKED_PLAN}
    lanes = {item["analysis_lane"] for item in report["manifest"]}
    assert lanes <= {LANE_PRODUCTION, LANE_DIAGNOSTIC, LANE_BLOCKED}
    for item in report["manifest"]:
        if item["run_status"] == "RUNNABLE":
            assert item["analysis_lane"] == LANE_PRODUCTION
            corpus = next(row for row in report["corpora"] if row["id"] == item["id"])
            assert corpus["weeks"], item["id"]
        elif item["run_status"] == BLOCKED_PLAN:
            assert item["error_type"] == "ConfirmedStudyPlanError"
            assert item["block_reason"]
            if item["analysis_lane"] == LANE_DIAGNOSTIC:
                corpus = next(row for row in report["corpora"] if row["id"] == item["id"])
                assert corpus["weeks"]
        else:
            assert item["block_reason"], item["id"]
            if item["block_reason"] == MISSING_UTP:
                assert item["run_status"] == "BLOCKED"
    grouped = report["aggregates"][LANE_PRODUCTION]["unresolved_by_reason"]
    assert all(not reason.isdigit() for reason in grouped)
    prod = report["aggregates"][LANE_PRODUCTION]["diff"]
    assert report["c11_status"] in {"READY", "BLOCKED"}
    if prod["NEW_LOSES"] or prod["NEW_INVENTS"]:
        assert report["c11_status"] == "BLOCKED"
    else:
        assert report["c11_status"] == "READY"
