from pathlib import Path
from functools import lru_cache

from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.pipeline import (
    USE_CONTENT_ENGINE_V2,
    _build_pipeline_lesson_content,
    run_calendar_pipeline,
)
from calendar_pedagoga.docx_qa import has_blocking_qa_issues, validate_calendar_docx
from calendar_pedagoga.organization_template import select_calendar_template
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import infer_study_year_number, parse_program
from calendar_pedagoga.resolve_utp import resolve_utp
from calendar_pedagoga.scheduling import build_schedule
from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload


REFERENCES = Path(__file__).resolve().parents[1] / "references"


@lru_cache(maxsize=1)
def _key_pipeline():
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    utp = parse_utp(utp_path)
    program = parse_program(program_path.read_bytes(), program_path.name, study_year=2)
    return run_calendar_pipeline(
        utp,
        program,
        academic_year="2026–2027",
        template=select_calendar_template(),
        source_utp_name=utp_path.name,
        use_ai=False,
    )


def test_key_end_to_end_pipeline_returns_docx() -> None:
    result = _key_pipeline()
    assert result.content.startswith(b"PK\x03\x04")
    assert result.filename.endswith(".docx")
    assert len(result.resolved_lessons) == 36
    assert not has_blocking_qa_issues(
        validate_calendar_docx(result.content, expected_weeks=36)
    )


def test_tour_guides_pipeline_without_program_is_limited_qa_reference() -> None:
    utp_path = REFERENCES / "УТП ТП 3г. 2ч.docx"
    utp = parse_utp(utp_path)
    result = run_calendar_pipeline(
        utp,
        None,
        academic_year="2026–2027",
        template=select_calendar_template(),
        source_utp_name=utp_path.name,
        use_ai=False,
    )
    assert len(result.resolved_lessons) == 36
    assert all(
        not row.theory_text and not row.practice_text
        for row in result.resolved_lessons
    )
    assert all(
        row.lesson_type and row.planned_result and row.assessment_method
        for row in result.resolved_lessons
    )
    assert any("программа не загружена" in warning.casefold() for warning in result.warnings)


def test_content_engine_v2_flag_defaults_on() -> None:
    assert USE_CONTENT_ENGINE_V2 is True


@lru_cache(maxsize=1)
def _tour_guides_inputs():
    program_path = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    template_path = REFERENCES / "Календарный план.docx"
    validated = validate_upload(
        UploadPurpose.PROGRAM,
        program_path.name,
        program_path.read_bytes(),
    )
    utp = resolve_utp(None, validated)
    program = parse_program(
        validated.content,
        validated.filename,
        study_year=infer_study_year_number(utp.metadata.study_year),
    )
    template = select_calendar_template(template_path.name, template_path.read_bytes())
    return utp, program, template, program_path.name


def _source_skeleton(source) -> tuple:
    return (
        source.week_number,
        source.date_range,
        source.month,
        source.topic_number,
        source.topic_title,
        source.theory_hours,
        source.practice_hours,
        source.total_hours,
    )


def test_pipeline_content_engine_switch_keeps_source_grid() -> None:
    utp, program, _template, source_name = _tour_guides_inputs()
    content = build_content_model(build_schedule(utp, "2026–2027"), utp, program, source_name)
    ce1 = _build_pipeline_lesson_content(content, use_content_engine_v2=False)
    ce2 = _build_pipeline_lesson_content(content, use_content_engine_v2=True)
    assert [_source_skeleton(row.source) for row in ce1] == [
        _source_skeleton(row.source) for row in ce2
    ]
    assert any(left.planned_result != right.planned_result for left, right in zip(ce1, ce2))


@lru_cache(maxsize=1)
def _tour_guides_ce2_pipeline():
    utp, program, template, source_name = _tour_guides_inputs()
    return run_calendar_pipeline(
        utp,
        program,
        academic_year="2026–2027",
        template=template,
        source_utp_name=source_name,
        program_filename=source_name,
        use_ai=False,
        use_content_engine_v2=True,
    )


def test_tour_guides_pipeline_ce2_without_ai_keeps_grid() -> None:
    utp, program, _template, source_name = _tour_guides_inputs()
    baseline = _build_pipeline_lesson_content(
        build_content_model(build_schedule(utp, "2026–2027"), utp, program, source_name),
        use_content_engine_v2=False,
    )
    result = _tour_guides_ce2_pipeline()
    assert result.content.startswith(b"PK\x03\x04")
    assert result.filename.endswith(".docx")
    assert len(result.resolved_lessons) == 36
    assert sum(row.source.source.theory_hours for row in result.resolved_lessons) == 27
    assert sum(row.source.source.practice_hours for row in result.resolved_lessons) == 45
    assert sum(row.source.source.total_hours for row in result.resolved_lessons) == 72
    assert [_source_skeleton(row.source.source) for row in result.resolved_lessons] == [
        _source_skeleton(row.source) for row in baseline
    ]
    assert all(
        row.lesson_type and row.planned_result and row.assessment_method
        for row in result.resolved_lessons
    )
    assert not has_blocking_qa_issues(
        validate_calendar_docx(result.content, expected_weeks=36)
    )
