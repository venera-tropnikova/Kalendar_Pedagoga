"""C3: import/match shadow adapters. No new matching or plan confirmation."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from calendar_pedagoga.confirmed_study_plan import (
    ConfirmedStudyPlanError,
    confirmed_plan_from_external_utp,
)
from calendar_pedagoga.content_engine_v2 import derive_fields_v2
from calendar_pedagoga.content_generation import CalendarContentRow, build_content_model
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.parsing import Hours, Topic, parse_utp
from calendar_pedagoga.pipeline import _build_pipeline_lesson_content
from calendar_pedagoga.program_parsing import parse_program
from calendar_pedagoga.scheduling import build_schedule
from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.import_adapter import (
    import_projection_gaps,
    project_import,
    reset_import_adapter_calls,
    import_adapter_calls,
)
from calendar_pedagoga.semantic_atom.match_adapter import (
    MatchConfidence,
    match_projection_gaps,
    project_match_bindings,
    project_schedule_rows,
    reset_match_adapter_calls,
    match_adapter_calls,
)
from calendar_pedagoga.semantic_atom.models import ImportStatus, to_jsonable
from calendar_pedagoga.semantic_atom.oracle import find_named
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
    assert hashlib.sha256(C1_ORACLE.read_bytes()).hexdigest() == C1_ORACLE_SHA256


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_models_still_avoid_production_imports() -> None:
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


def test_production_does_not_import_c3_adapters() -> None:
    needles = (
        "ProgramSource",
        "MatchBinding",
        "ImportAdapter",
        "project_import",
        "project_match_bindings",
        "import_adapter",
        "match_adapter",
    )
    for path in PRODUCTION_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{path.name} contains {needle}"


def _content_row(**kwargs) -> CalendarContentRow:
    base = dict(
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
    base.update(kwargs)
    return CalendarContentRow(**base)


def test_production_does_not_call_import_or_match_adapters() -> None:
    reset_import_adapter_calls()
    reset_match_adapter_calls()
    reset_shadow_invocation_count()
    row = _content_row()
    _build_pipeline_lesson_content((row,), use_content_engine_v2=True)
    assert import_adapter_calls() == 0
    assert match_adapter_calls() == 0
    assert shadow_invocation_count() == 0


def test_match_adapter_projects_status_without_recompute() -> None:
    rows = (
        _content_row(match_status=MatchStatus.EXACT),
        _content_row(
            week_number=2,
            match_status=MatchStatus.NOT_MATCHED,
            program_content_full="",
            warnings=("нет содержания программы",),
        ),
        _content_row(
            week_number=3,
            match_status=MatchStatus.UNCONFIRMED,
            program_content_full="Альфа",
        ),
        _content_row(
            week_number=4,
            match_status=MatchStatus.USER_CONFIRMED,
            program_content_full="Бета",
        ),
    )
    bindings = project_match_bindings(rows)
    assert [item.match_status for item in bindings] == [
        "EXACT",
        "NOT_MATCHED",
        "UNCONFIRMED",
        "USER_CONFIRMED",
    ]
    assert [item.confidence for item in bindings] == [
        MatchConfidence.EXACT,
        MatchConfidence.NONE,
        MatchConfidence.AMBIGUOUS,
        MatchConfidence.CONFIRMED,
    ]
    assert match_projection_gaps(rows, bindings) == []


def test_lost_match_field_fails() -> None:
    row = _content_row()
    binding = project_match_bindings((row,))[0]
    broken = type(binding)(**{**binding.__dict__, "source_hash": ""})
    gaps = match_projection_gaps((row,), (broken,))
    assert gaps, "потеря hash SOURCE должна давать FAIL"
    assert any("source_hash" in item for item in gaps)


def test_schedule_and_match_keep_hours_topics_and_source() -> None:
    rows = (
        _content_row(theory_hours=1, practice_hours=1, total_hours=2),
        _content_row(
            week_number=2,
            topic_title="Другая",
            theory_hours=2,
            practice_hours=0,
            total_hours=2,
            program_content_full="Что такое компас?",
        ),
    )
    schedule = project_schedule_rows(rows)
    bindings = project_match_bindings(rows)
    assert len(schedule) == len(rows)
    assert [item.topic_title for item in schedule] == [row.topic_title for row in rows]
    assert [(item.theory_hours, item.practice_hours) for item in schedule] == [
        (str(row.theory_hours), str(row.practice_hours)) for row in rows
    ]
    assert [item.source for item in bindings] == [row.program_content_full for row in rows]
    assert match_projection_gaps(rows, bindings) == []


def test_key_y2_import_preserves_plan_fields() -> None:
    utp_path = find_named("УТП КЛЮЧ 2 г. 2ч.docx")
    program_path = find_named("Программа КЛЮЧ.DOC")
    assert utp_path and program_path
    parsed = parse_utp(utp_path)
    plan = confirmed_plan_from_external_utp(
        parsed, study_year=2, source_name=utp_path.name
    )
    program = parse_program(program_path.read_bytes(), program_path.name, study_year=2)
    projected = project_import(
        plan=plan,
        program=program,
        source_utp_name=utp_path.name,
        source_program_name=program_path.name,
    )
    assert projected.import_status is ImportStatus.PLAN_CONFIRMED
    assert import_projection_gaps(plan, projected, program=program) == []
    content = build_content_model(
        build_schedule(plan.as_utp_parse_result(), "2026–2027"),
        plan.as_utp_parse_result(),
        program,
        utp_path.name,
    )
    bindings = project_match_bindings(content)
    assert match_projection_gaps(content, bindings) == []
    assert len(project_schedule_rows(content)) == len(content)


def test_tour_confirmed_error_projects_same_block() -> None:
    utp_path = find_named("УТП ТП 3г. 2ч.docx")
    assert utp_path
    parsed = parse_utp(utp_path)
    try:
        confirmed_plan_from_external_utp(
            parsed, study_year=3, source_name=utp_path.name
        )
    except ConfirmedStudyPlanError as error:
        projected = project_import(error=error, source_utp_name=utp_path.name)
        assert projected.import_status is ImportStatus.BLOCK
        assert projected.error_type == "ConfirmedStudyPlanError"
        assert projected.error == str(error)
        return
    raise AssertionError("tour Y3 должен давать ConfirmedStudyPlanError")


def test_identity_differential_stays_equal() -> None:
    source = "Понятия: ритм, темп, динамика."
    produced = derive_fields_v2(
        topic_title="Теория",
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=2,
        practice_hours=0,
    )
    assert compare_identity(
        produced, run_passthrough_shadow(produced, source=source)
    )[0].kind is DiffKind.EQUAL


def test_import_notice_and_serialization_keep_fields() -> None:
    topic = Topic("1", "Тема", Hours(2, 1, 1), "Раздел")
    from calendar_pedagoga.confirmed_study_plan import ConfirmedStudyPlan
    from calendar_pedagoga.parsing import UtpMetadata

    plan = ConfirmedStudyPlan(
        study_year=1,
        topics=(topic,),
        total_hours=2,
        theory_hours=1,
        practice_hours=1,
        study_weeks=1,
        hours_per_week=2,
        source="manual",
        _reference_warnings=("NOTICE: расхождение часов",),
    )
    projected = project_import(plan=plan, source_utp_name="manual")
    assert ImportStatus.NOTICE in projected.statuses
    payload = to_jsonable(projected)
    assert payload["study_year"] == 1
    assert payload["topics"][0]["title"] == "Тема"
    assert import_projection_gaps(plan, projected) == []
