from datetime import date
from pathlib import Path

from calendar_pedagoga.normative_engine import (
    NormativeLayer,
    NormativeLessonView,
    NormativeVerdict,
    academic_year_period,
    evaluate_normative_mvp,
    normalize_academic_year,
)
from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.parsing import Hours, Topic, UtpMetadata, UtpParseResult, parse_utp
from calendar_pedagoga.resolve_utp import resolve_utp
from calendar_pedagoga.scheduling import AcademicWeek, ScheduleResult, ScheduledElement, build_schedule
from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload
from calendar_pedagoga.program_parsing import (
    ProgramData,
    parse_age_range,
    parse_duration_years,
    parse_program,
)


REFERENCES = Path(__file__).resolve().parents[1] / "references"


def _program(
    *,
    duration: str | None = "2 года",
    duration_years: int | None = 2,
    student_age: str | None = "11-12 лет",
    expected_results: tuple[str, ...] = ("знает правила безопасности",),
    knowledge_outcomes: tuple[str, ...] = (),
    skill_outcomes: tuple[str, ...] = (),
) -> ProgramData:
    return ProgramData(
        title="Тест",
        duration=duration,
        student_age=student_age,
        goal="цель",
        tasks=("задача",),
        lesson_forms=(),
        teaching_methods=(),
        expected_results=expected_results,
        knowledge_outcomes=knowledge_outcomes,
        skill_outcomes=skill_outcomes,
        content_items=(),
        duration_years=duration_years,
        age_min=11 if student_age else None,
        age_max=12 if student_age else None,
    )


def _utp(
    *,
    study_year: str | None = "1 год обучения",
    student_age: str | None = None,
    hours_per_week: int | None = 2,
    hours_per_year: int | None = 72,
    study_weeks: int | None = 36,
    academic_year: str | None = None,
    workload_provenance: str | None = "document",
    table_total: int | None = 72,
    topics: tuple[Topic, ...] = (),
    table_theory: int = 27,
    table_practice: int = 45,
) -> UtpParseResult:
    totals = Hours(table_total, table_theory, table_practice) if table_total is not None else None
    return UtpParseResult(
        metadata=UtpMetadata(
            study_year=study_year,
            student_age=student_age,
            hours_per_week=hours_per_week,
            hours_per_year=hours_per_year,
            study_weeks=study_weeks,
            academic_year=academic_year,
            workload_provenance=workload_provenance,
        ),
        sections=(),
        topics=topics,
        table_totals=totals,
    )


def _verdicts(report) -> dict[str, str]:
    return {item.check_id: item.verdict.value for item in report.checks}


def test_duration_years_only_from_explicit_number() -> None:
    assert parse_duration_years("2 года") == 2
    assert parse_duration_years("3 года обучения") == 3
    assert parse_duration_years("1 год") == 1
    assert parse_duration_years("несколько лет") is None
    assert parse_duration_years("2-3 года") is None
    assert parse_duration_years("на три года") is None


def test_age_range_only_from_explicit_span() -> None:
    assert parse_age_range("11-12 лет") == (11, 12)
    assert parse_age_range("8 – 11 лет") == (8, 11)
    assert parse_age_range("от 10 до 14 лет") == (10, 14)
    assert parse_age_range("11 лет") == (None, None)
    assert parse_age_range("старший школьный возраст") == (None, None)
    assert parse_age_range("2011-2012") == (None, None)


def test_academic_year_helpers() -> None:
    assert normalize_academic_year("план на 2026 - 2027 учебный") == "2026–2027"
    assert normalize_academic_year("2026/27") == "2026–2027"
    assert normalize_academic_year("2026–2028") is None
    assert academic_year_period("2026–2027") == (date(2026, 9, 1), date(2027, 8, 31))
    assert academic_year_period("2026") is None


def test_mvp_pass_path_for_complete_documents() -> None:
    report = evaluate_normative_mvp(
        _utp(academic_year="2026–2027 учебный год"),
        _program(),
        academic_year="2026–2027",
        study_year_hints=("Программа ТЕСТ 1 г.docx",),
    )
    verdicts = _verdicts(report)
    assert verdicts["registry_in_force"] == "pass"
    assert verdicts["academic_year_match"] == "pass"
    assert verdicts["age_found"] == "pass"
    assert verdicts["duration_found"] == "pass"
    assert verdicts["study_year_found"] == "pass"
    assert verdicts["year_within_duration"] == "pass"
    assert verdicts["yearly_hours"] == "pass"
    assert verdicts["weekly_load"] == "pass"
    assert verdicts["expected_results"] == "pass"
    assert verdicts["topic_hours"] == "not_checked"
    assert verdicts["theory_practice_hours"] == "not_checked"
    assert verdicts["plan_matches_utp"] == "not_checked"
    assert verdicts["type_vs_hours"] == "not_checked"
    assert verdicts["academic_grid"] == "not_checked"
    assert verdicts["vacation_gap"] == "not_checked"
    assert verdicts["short_week_full_load"] == "not_checked"
    assert verdicts["attestation"] == "not_checked"
    assert {item.check_id: item.layer for item in report.checks}["registry_in_force"] is (
        NormativeLayer.FEDERAL
    )
    assert all("PASS" not in item.teacher_text for item in report.checks)


def test_mvp_warnings_and_not_checked() -> None:
    report = evaluate_normative_mvp(
        _utp(
            study_year=None,
            hours_per_week=2,
            hours_per_year=72,
            study_weeks=36,
            table_total=90,
            workload_provenance="derived_36x2",
            academic_year="2025–2026",
        ),
        _program(
            duration=None,
            duration_years=None,
            student_age=None,
            expected_results=(),
        ),
        academic_year="2026–2027",
        study_year_hints=(),
    )
    verdicts = _verdicts(report)
    assert verdicts["age_found"] == "warning"
    assert verdicts["duration_found"] == "warning"
    assert verdicts["study_year_found"] == "warning"
    assert verdicts["year_within_duration"] == "not_checked"
    assert verdicts["yearly_hours"] == "warning"
    assert verdicts["weekly_load"] == "warning"
    assert verdicts["expected_results"] == "warning"
    assert verdicts["academic_year_match"] == "warning"
    assert verdicts["registry_in_force"] == "pass"


def test_year_greater_than_duration_is_warning() -> None:
    report = evaluate_normative_mvp(
        _utp(study_year="3 год обучения"),
        _program(duration="2 года", duration_years=2),
        academic_year="2026–2027",
    )
    assert _verdicts(report)["year_within_duration"] == "warning"


def test_missing_program_does_not_block_and_skips_program_checks() -> None:
    report = evaluate_normative_mvp(
        _utp(),
        None,
        academic_year="2026–2027",
        study_year_hints=("УТП КЛЮЧ 2 г. 2ч.docx",),
    )
    verdicts = _verdicts(report)
    assert verdicts["duration_found"] == "not_checked"
    assert verdicts["expected_results"] == "not_checked"
    assert verdicts["study_year_found"] == "pass"
    assert verdicts["year_within_duration"] == "not_checked"


def test_registry_warns_when_year_is_outside_sp_term() -> None:
    report = evaluate_normative_mvp(
        _utp(),
        _program(),
        academic_year="2020–2021",
    )
    assert _verdicts(report)["registry_in_force"] == "warning"


def test_engine_does_not_use_technical_codes_in_teacher_text() -> None:
    report = evaluate_normative_mvp(_utp(), _program(), academic_year="2026–2027")
    blob = " ".join(item.teacher_text for item in report.checks)
    assert "NOT CHECKED" not in blob
    assert "WARNING" not in blob
    assert "PASS" not in blob


def test_topic_hours_warns_when_theory_practice_mismatch() -> None:
    topics = (
        Topic("1", "Тема", Hours(4, 1, 1)),
    )
    report = evaluate_normative_mvp(
        _utp(topics=topics),
        _program(),
        academic_year="2026–2027",
    )
    assert _verdicts(report)["topic_hours"] == "warning"


def test_type_vs_hours_detects_contradiction_and_mixed_pass() -> None:
    conflict = evaluate_normative_mvp(
        _utp(),
        _program(),
        academic_year="2026–2027",
        lessons=(
            NormativeLessonView(2, 0, "практикум", topic_title="Теория"),
            NormativeLessonView(0, 2, "теоретическое занятие", topic_title="Практика"),
        ),
    )
    assert _verdicts(conflict)["type_vs_hours"] == "warning"
    mixed = evaluate_normative_mvp(
        _utp(),
        _program(),
        academic_year="2026–2027",
        lessons=(
            NormativeLessonView(1, 1, "практическое занятие", topic_title="Смесь"),
        ),
    )
    assert _verdicts(mixed)["type_vs_hours"] == "pass"


def test_attestation_only_when_explicit_in_program() -> None:
    absent = evaluate_normative_mvp(_utp(), _program(), academic_year="2026–2027")
    assert _verdicts(absent)["attestation"] == "not_checked"
    assert "ожидаемые результаты" not in absent.checks[-1].teacher_text.casefold()

    program = _program()
    program = ProgramData(
        title=program.title,
        duration=program.duration,
        student_age=program.student_age,
        goal=program.goal,
        tasks=program.tasks,
        lesson_forms=program.lesson_forms,
        teaching_methods=program.teaching_methods,
        expected_results=program.expected_results,
        knowledge_outcomes=program.knowledge_outcomes,
        skill_outcomes=program.skill_outcomes,
        content_items=program.content_items,
        duration_years=program.duration_years,
        age_min=program.age_min,
        age_max=program.age_max,
        attestation_statements=("Итоговая аттестация проводится в конце года.",),
    )
    missing = evaluate_normative_mvp(
        _utp(topics=(Topic("1", "Введение", Hours(2, 1, 1)),)),
        program,
        academic_year="2026–2027",
        lessons=(
            NormativeLessonView(1, 1, "беседа", "устный опрос", "Введение"),
        ),
    )
    assert _verdicts(missing)["attestation"] == "warning"

    found = evaluate_normative_mvp(
        _utp(topics=(Topic("12", "Итоговая аттестация", Hours(2, 0, 2)),)),
        program,
        academic_year="2026–2027",
    )
    assert _verdicts(found)["attestation"] == "pass"


def test_local_grid_gap_and_short_week_do_not_change_schedule() -> None:
    week = AcademicWeek(
        1,
        date(2026, 12, 28),
        date(2026, 12, 30),
        "Декабрь",
        "2026–2027",
    )
    topic = Topic("1", "Тема", Hours(2, 1, 1), parent_section="Раздел")
    schedule = ScheduleResult(
        weeks=(week,),
        elements=(
            ScheduledElement("Раздел", "1", "Тема", "theory", 1, week),
            ScheduledElement("Раздел", "1", "Тема", "practice", 1, week),
        ),
    )
    before = schedule
    report = evaluate_normative_mvp(
        _utp(topics=(topic,), table_total=2, table_theory=1, table_practice=1),
        _program(),
        academic_year="2026–2027",
        schedule=schedule,
    )
    verdicts = _verdicts(report)
    assert verdicts["academic_grid"] == "warning"
    assert verdicts["vacation_gap"] == "pass"
    assert verdicts["short_week_full_load"] == "warning"
    assert verdicts["plan_matches_utp"] == "pass"
    assert verdicts["theory_practice_hours"] == "pass"
    assert schedule is before
    assert schedule.weeks[0].start == date(2026, 12, 28)
    assert "Перенос не выполнялся" in " ".join(item.teacher_text for item in report.warnings)

    gap_week = AcademicWeek(
        2,
        date(2027, 1, 4),
        date(2027, 1, 10),
        "Январь",
        "2026–2027",
    )
    gap_schedule = ScheduleResult(weeks=(gap_week,), elements=())
    gap_report = evaluate_normative_mvp(
        _utp(),
        _program(),
        academic_year="2026–2027",
        schedule=gap_schedule,
    )
    assert _verdicts(gap_report)["vacation_gap"] == "warning"


def test_other_year_skips_approved_grid_and_vacation_profile() -> None:
    schedule = build_schedule(parse_utp(REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"), "2027–2028")
    report = evaluate_normative_mvp(
        parse_utp(REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"),
        None,
        academic_year="2027–2028",
        schedule=schedule,
    )
    verdicts = _verdicts(report)
    assert verdicts["academic_grid"] == "warning"
    assert verdicts["vacation_gap"] == "not_checked"
    assert verdicts["academic_year_match"] == "warning"
    assert "только утверждённая сетка 2026–2027" in " ".join(
        item.teacher_text for item in report.checks if item.check_id == "academic_grid"
    )


def test_reference_key_and_tour_guides_expose_layers() -> None:
    key_program = parse_program(
        (REFERENCES / "Программа КЛЮЧ.DOC").read_bytes(),
        "Программа КЛЮЧ.DOC",
        study_year=2,
    )
    key_utp = parse_utp(REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx")
    key_schedule = build_schedule(key_utp)
    key_rows = build_content_model(key_schedule, key_utp, key_program, "УТП КЛЮЧ 2 г. 2ч.docx")
    key_lessons = tuple(
        NormativeLessonView(
            theory_hours=row.source.theory_hours,
            practice_hours=row.source.practice_hours,
            lesson_type=row.lesson_type,
            assessment_method=row.assessment_method,
            topic_title=row.source.topic_title,
        )
        for row in build_lesson_content_v2(key_rows)
    )
    key_report = evaluate_normative_mvp(
        key_utp,
        key_program,
        academic_year="2026–2027",
        study_year_hints=("УТП КЛЮЧ 2 г. 2ч.docx",),
        schedule=key_schedule,
        lessons=key_lessons,
    )
    key_verdicts = _verdicts(key_report)
    assert key_verdicts["registry_in_force"] == "pass"
    assert key_verdicts["academic_grid"] == "pass"
    assert key_verdicts["vacation_gap"] == "pass"
    assert key_verdicts["short_week_full_load"] == "warning"
    assert key_verdicts["topic_hours"] == "pass"
    assert key_verdicts["plan_matches_utp"] == "pass"
    assert key_verdicts["theory_practice_hours"] == "pass"
    assert key_verdicts["type_vs_hours"] == "pass"
    assert key_verdicts["attestation"] == "not_checked"
    assert {item.layer for item in key_report.for_layer(NormativeLayer.FEDERAL)}
    assert key_report.for_layer(NormativeLayer.LOCAL)
    assert key_report.for_layer(NormativeLayer.METHODICAL)
    assert all("соответствует НПА" not in item.teacher_text for item in key_report.for_layer(NormativeLayer.METHODICAL))

    tp_path = next(
        path
        for path in REFERENCES.iterdir()
        if path.stat().st_size == 80421
    )
    upload = validate_upload(UploadPurpose.PROGRAM, tp_path.name, tp_path.read_bytes())
    tp_program = parse_program(upload.content, upload.filename, study_year=1)
    tp_utp = resolve_utp(None, upload)
    tp_schedule = build_schedule(tp_utp)
    tp_rows = build_content_model(tp_schedule, tp_utp, tp_program, upload.filename)
    tp_lessons = tuple(
        NormativeLessonView(
            theory_hours=row.source.theory_hours,
            practice_hours=row.source.practice_hours,
            lesson_type=row.lesson_type,
            assessment_method=row.assessment_method,
            topic_title=row.source.topic_title,
        )
        for row in build_lesson_content_v2(tp_rows)
    )
    tp_report = evaluate_normative_mvp(
        tp_utp,
        tp_program,
        academic_year="2026–2027",
        study_year_hints=(upload.filename,),
        schedule=tp_schedule,
        lessons=tp_lessons,
    )
    tp_verdicts = _verdicts(tp_report)
    assert tp_verdicts["academic_grid"] == "pass"
    assert tp_verdicts["vacation_gap"] == "pass"
    assert tp_verdicts["short_week_full_load"] == "warning"
    assert tp_verdicts["plan_matches_utp"] == "pass"
    assert tp_verdicts["type_vs_hours"] == "pass"
    assert tp_program.attestation_statements
    assert tp_verdicts["attestation"] == "warning"


def test_key_program_exposes_reliable_duration_and_age() -> None:
    program = parse_program(
        (REFERENCES / "Программа КЛЮЧ.DOC").read_bytes(),
        "Программа КЛЮЧ.DOC",
        study_year=2,
    )
    assert program.duration == "3 года"
    assert program.duration_years == 3
    assert program.student_age == "8 – 11 лет"
    assert program.age_min == 8
    assert program.age_max == 11
