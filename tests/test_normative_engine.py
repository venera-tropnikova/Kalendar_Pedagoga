from datetime import date
from pathlib import Path

from calendar_pedagoga.normative_engine import (
    NormativeVerdict,
    academic_year_period,
    evaluate_normative_mvp,
    normalize_academic_year,
)
from calendar_pedagoga.parsing import Hours, UtpMetadata, UtpParseResult
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
) -> UtpParseResult:
    totals = Hours(table_total, 27, 45) if table_total is not None else None
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
        topics=(),
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
    assert academic_year_period("2026–2027") == (date(2026, 9, 1), date(2027, 8, 31))
    assert academic_year_period("2026") is None


def test_mvp_pass_path_for_complete_documents() -> None:
    report = evaluate_normative_mvp(
        _utp(academic_year="2026–2027 учебный год"),
        _program(),
        academic_year="2026–2027",
        study_year_hints=("Программа ТЕСТ 1 г.docx",),
    )
    assert _verdicts(report) == {
        "registry_in_force": "pass",
        "age_found": "pass",
        "duration_found": "pass",
        "study_year_found": "pass",
        "year_within_duration": "pass",
        "yearly_hours": "pass",
        "weekly_load": "pass",
        "expected_results": "pass",
        "academic_year_match": "pass",
    }
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
