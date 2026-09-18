from pathlib import Path
from functools import lru_cache
from unittest.mock import Mock

import pytest

from calendar_pedagoga.confirmed_study_plan import confirmed_plan_from_external_utp
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.docx_generation import build_output_filename
from calendar_pedagoga.pipeline import (
    CalendarDocumentStatus,
    PipelineError,
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


def test_hard_block_prevents_docx_generation(monkeypatch) -> None:
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    utp = parse_utp(utp_path)
    generate_docx = Mock()

    def _raise_hard_block(*_args, **_kwargs):
        raise PipelineError("Повреждённые данные расписания")

    monkeypatch.setattr("calendar_pedagoga.pipeline.build_schedule", _raise_hard_block)
    monkeypatch.setattr(
        "calendar_pedagoga.pipeline.generate_calendar_docx",
        generate_docx,
    )

    with pytest.raises(PipelineError, match="Повреждённые данные") as raised:
        run_calendar_pipeline(
            utp,
            None,
            academic_year="2026–2027",
            template=select_calendar_template(),
            source_utp_name=utp_path.name,
            use_ai=False,
        )

    assert raised.value.status is CalendarDocumentStatus.HARD_BLOCK
    generate_docx.assert_not_called()


def _corpus(*needles: str) -> Path | None:
    roots = (REFERENCES, Path(r"D:\Kalendar_Pedagoga\references"))
    for root in roots:
        if not root.exists():
            continue
        matches = [
            path
            for path in root.iterdir()
            if all(needle.casefold() in path.name.casefold() for needle in needles)
        ]
        if matches:
            return matches[0]
    return None


def _stub_docx_bytes(monkeypatch) -> None:
    monkeypatch.setattr(
        "calendar_pedagoga.pipeline.generate_calendar_docx",
        lambda *_args, **_kwargs: b"PK\x03\x04docx",
    )
    monkeypatch.setattr(
        "calendar_pedagoga.pipeline.validate_calendar_docx",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        "calendar_pedagoga.pipeline.validate_calendar_docx_visual",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        "calendar_pedagoga.pipeline.has_blocking_qa_issues",
        lambda *_args, **_kwargs: False,
    )


def _run_named_corpus(utp_path: Path, program_path: Path, *, study_year: int):
    utp = parse_utp(utp_path)
    program = parse_program(
        program_path.read_bytes(), program_path.name, study_year=study_year
    )
    plan = confirmed_plan_from_external_utp(
        utp, study_year=study_year, source_name=utp_path.name
    )
    result = run_calendar_pipeline(
        plan,
        program,
        academic_year="2026–2027",
        template=select_calendar_template(),
        source_utp_name=utp_path.name,
        use_ai=False,
        program_filename=program_path.name,
    )
    expected = build_output_filename(plan.as_utp_parse_result(), "2026–2027")
    return result, expected


def test_key_y1_draft_uses_ordinary_calendar_filename(monkeypatch) -> None:
    utp_path = _corpus("утп", "ключ", "1 г")
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    if not program_path.exists():
        program_path = _corpus("програм", "ключ")
    if utp_path is None or program_path is None or not program_path.exists():
        pytest.skip("В references нет УТП КЛЮЧ 1 г")
    _stub_docx_bytes(monkeypatch)
    result, expected = _run_named_corpus(utp_path, program_path, study_year=1)
    assert result.status is CalendarDocumentStatus.DRAFT_READY
    assert result.review_cases
    assert result.filename == expected
    assert result.filename == "Календарный_план_КЛЮЧ_2026-2027.docx"
    assert "Черновик_" not in result.filename


def test_climb_keeps_ordinary_calendar_filename(monkeypatch) -> None:
    utp_path = _corpus("утп", "скалолаз")
    program_path = _corpus("програм", "скалолаз")
    if utp_path is None or program_path is None:
        pytest.skip("В references нет документов Скалолазание")
    _stub_docx_bytes(monkeypatch)
    result, expected = _run_named_corpus(utp_path, program_path, study_year=1)
    assert result.status is CalendarDocumentStatus.FINAL_READY
    assert not result.review_cases
    assert result.filename == expected
    assert "Черновик_" not in result.filename


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


def test_analysis_and_pipeline_share_ce2_lesson_fields() -> None:
    from calendar_pedagoga.lesson_resolution import resolve_lesson_content

    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    utp = parse_utp(utp_path)
    program = parse_program(program_path.read_bytes(), program_path.name, study_year=2)
    content = build_content_model(
        build_schedule(utp, "2026–2027"),
        utp,
        program,
        utp_path.name,
    )
    analysis = _build_pipeline_lesson_content(
        content,
        use_content_engine_v2=USE_CONTENT_ENGINE_V2,
    )
    resolved = resolve_lesson_content(analysis)
    assert [
        (row.lesson_type, row.planned_result, row.assessment_method, row.warnings)
        for row in analysis
    ] == [
        (row.lesson_type, row.planned_result, row.assessment_method, row.warnings)
        for row in resolved
    ]
    assert len(analysis) == 36
