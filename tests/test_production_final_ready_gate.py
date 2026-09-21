from __future__ import annotations

from dataclasses import replace

from calendar_pedagoga.content_engine_v2 import (
    LessonContentV2Row,
    PROVENANCE_GENERIC_ONLY,
    REQUIRED_ACTION,
    derive_fields_v2,
    validate_manual_lesson_content,
)
from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.pipeline import (
    CalendarDocumentStatus,
    _build_pipeline_lesson_content_outcome,
)
from calendar_pedagoga.production_readiness import (
    EMPTY_CONTROL,
    EMPTY_RESULT,
    GENERIC_ONLY,
    SOURCE_NOT_MATCHED,
    production_readiness_codes,
)
from calendar_pedagoga.semantic_review import (
    ManualSemanticConfirmation,
    apply_manual_semantic_confirmations,
)


def _source(**kwargs) -> CalendarContentRow:
    base = dict(
        week_number=1,
        date_range="01–07.09",
        month="Сентябрь",
        section="Раздел",
        topic_number="1",
        topic_title="Тема 1",
        source_topic_title="Тема 1",
        theory_hours=0,
        practice_hours=2,
        total_hours=2,
        match_status=MatchStatus.EXACT,
        program_section="Раздел",
        program_topic="Тема 1",
        program_content_full="Выполнение упражнения.",
        program_content_preview="Выполнение упражнения.",
        source_program_name="Программа",
        source_utp_name="utp.docx",
        warnings=(),
    )
    base.update(kwargs)
    return CalendarContentRow(**base)


def _row(*, source: CalendarContentRow | None = None, **kwargs) -> LessonContentV2Row:
    payload = dict(
        source=source or _source(),
        theory_text="",
        practice_text="Выполнение упражнения.",
        lesson_type="практическое занятие",
        planned_result="Выполняет упражнение.",
        assessment_method="педагогическое наблюдение за выполнением упражнения",
        action="выполняет",
        object="упражнение",
        conditions="",
        warnings=(),
        clause_coverage=(("Выполнение упражнения", "COVERED"),),
        clause_roles=(("Выполнение упражнения", REQUIRED_ACTION),),
    )
    payload.update(kwargs)
    return LessonContentV2Row(**payload)


def _outcome(row: LessonContentV2Row, monkeypatch, *, confirmations=None):
    monkeypatch.setattr(
        "calendar_pedagoga.pipeline.build_lesson_content_v2",
        lambda _content: (row,),
    )
    return _build_pipeline_lesson_content_outcome(
        (row.source,),
        use_content_engine_v2=True,
        semantic_revision="revision",
        manual_confirmations=confirmations,
    )


def test_hours_with_empty_source_are_not_final_ready(monkeypatch) -> None:
    row = _row(
        source=_source(
            match_status=MatchStatus.NOT_MATCHED,
            program_content_full="",
            program_content_preview="",
        ),
        theory_text="",
        practice_text="",
        planned_result="",
        assessment_method="педагогическое наблюдение",
        warnings=("Недостаточно данных источника; использован безопасный fallback.",),
        provenance_codes=(GENERIC_ONLY,),
        clause_coverage=(),
        clause_roles=(),
    )
    assert production_readiness_codes(row) == (
        SOURCE_NOT_MATCHED,
        EMPTY_RESULT,
        GENERIC_ONLY,
    )
    outcome = _outcome(row, monkeypatch)
    assert outcome.status is CalendarDocumentStatus.DRAFT_READY
    assert [case.week_number for case in outcome.review_cases] == [1]
    assert SOURCE_NOT_MATCHED in outcome.review_cases[0].reasons
    assert EMPTY_RESULT in outcome.review_cases[0].reasons


def test_source_with_empty_result_is_not_final_ready(monkeypatch) -> None:
    row = _row(
        planned_result="",
        assessment_method="педагогическое наблюдение за выполнением упражнения",
        clause_coverage=(("Выполнение упражнения", "NEEDS_REVIEW"),),
    )
    assert EMPTY_RESULT in production_readiness_codes(row)
    assert SOURCE_NOT_MATCHED not in production_readiness_codes(row)
    outcome = _outcome(row, monkeypatch)
    assert outcome.status is CalendarDocumentStatus.DRAFT_READY
    assert EMPTY_RESULT in outcome.review_cases[0].reasons


def test_result_with_empty_control_is_not_final_ready(monkeypatch) -> None:
    row = _row(assessment_method="")
    assert production_readiness_codes(row) == (EMPTY_CONTROL,)
    outcome = _outcome(row, monkeypatch)
    assert outcome.status is CalendarDocumentStatus.DRAFT_READY
    assert EMPTY_CONTROL in outcome.review_cases[0].reasons


def test_generic_only_result_is_not_final_ready(monkeypatch) -> None:
    row = _row(
        planned_result="Знакомится с темой 1.",
        assessment_method="педагогическое наблюдение",
        warnings=("Безопасный шаблон CE2: unproven_object_case.",),
        provenance_codes=(GENERIC_ONLY,),
        clause_coverage=(),
        clause_roles=(),
    )
    assert production_readiness_codes(row) == (GENERIC_ONLY,)
    outcome = _outcome(row, monkeypatch)
    assert outcome.status is CalendarDocumentStatus.DRAFT_READY
    assert GENERIC_ONLY in outcome.review_cases[0].reasons


def test_structured_generic_only_blocks_final_ready(monkeypatch) -> None:
    row = _row(
        planned_result="Знакомится с темой 1.",
        assessment_method="педагогическое наблюдение",
        warnings=(),
        provenance_codes=(GENERIC_ONLY,),
        clause_coverage=(),
        clause_roles=(),
    )
    assert production_readiness_codes(row) == (GENERIC_ONLY,)
    assert _outcome(row, monkeypatch).status is CalendarDocumentStatus.DRAFT_READY


def test_warning_text_without_structured_code_does_not_drive_gate() -> None:
    row = _row(
        planned_result="Знакомится с темой 1.",
        assessment_method="педагогическое наблюдение",
        warnings=("Недостаточно данных источника; использован безопасный fallback.",),
        provenance_codes=(),
        clause_coverage=(),
        clause_roles=(),
    )
    assert GENERIC_ONLY not in production_readiness_codes(row)


def test_changed_warning_text_does_not_change_gate_decision() -> None:
    stamped = _row(
        planned_result="Знакомится с темой 1.",
        assessment_method="педагогическое наблюдение",
        warnings=("старый текст fallback",),
        provenance_codes=(GENERIC_ONLY,),
        clause_coverage=(),
        clause_roles=(),
    )
    rewritten = replace(stamped, warnings=("новый текст fallback",))
    assert production_readiness_codes(stamped) == (GENERIC_ONLY,)
    assert production_readiness_codes(rewritten) == (GENERIC_ONLY,)


def test_structured_code_works_without_warning() -> None:
    row = _row(
        planned_result="Знакомится с темой 1.",
        assessment_method="педагогическое наблюдение",
        warnings=(),
        provenance_codes=(GENERIC_ONLY,),
        clause_coverage=(),
        clause_roles=(),
    )
    assert production_readiness_codes(row) == (GENERIC_ONLY,)


def test_covered_source_backed_result_is_not_generic_only() -> None:
    row = _row(
        warnings=("Безопасный шаблон CE2: unproven_object_case.",),
        provenance_codes=(GENERIC_ONLY,),
        clause_coverage=(("Выполнение упражнения", "COVERED"),),
    )
    assert GENERIC_ONLY not in production_readiness_codes(row)
    assert production_readiness_codes(row) == ()


def test_fully_proven_row_stays_final_ready(monkeypatch) -> None:
    row = _row()
    assert production_readiness_codes(row) == ()
    outcome = _outcome(row, monkeypatch)
    assert outcome.status is CalendarDocumentStatus.FINAL_READY
    assert outcome.review_cases == ()


def test_user_confirmed_manual_row_is_final_ready(monkeypatch) -> None:
    row = _row(
        source=_source(match_status=MatchStatus.USER_CONFIRMED),
    )
    assert production_readiness_codes(row) == ()
    outcome = _outcome(row, monkeypatch)
    assert outcome.status is CalendarDocumentStatus.FINAL_READY
    assert outcome.review_cases == ()


def test_explicit_confirmation_clears_empty_result(monkeypatch) -> None:
    row = _row(
        planned_result="",
        assessment_method="",
        clause_coverage=(("Выполнение упражнения", "NEEDS_REVIEW"),),
    )
    first = _outcome(row, monkeypatch)
    assert first.status is CalendarDocumentStatus.DRAFT_READY
    case = first.review_cases[0]
    confirmation = ManualSemanticConfirmation(
        review_id=case.review_id,
        source_fingerprint=case.source_fingerprint,
        planned_result="Выполняет упражнение.",
        assessment_method="педагогическое наблюдение за выполнением упражнения",
    )
    validation = validate_manual_lesson_content(
        row,
        planned_result=confirmation.planned_result,
        assessment_method=confirmation.assessment_method,
    )
    assert validation.accepted
    application = apply_manual_semantic_confirmations(
        (row,), first.review_cases, {confirmation.review_id: confirmation}
    )
    assert application.pending_cases == ()
    assert confirmation.review_id in application.accepted_review_ids
    outcome = _outcome(
        row, monkeypatch, confirmations={confirmation.review_id: confirmation}
    )
    assert outcome.status is CalendarDocumentStatus.FINAL_READY
    assert outcome.review_cases == ()


def test_template_control_does_not_mask_empty_result() -> None:
    row = _row(
        source=_source(
            match_status=MatchStatus.NOT_MATCHED,
            program_content_full="",
            program_content_preview="",
        ),
        theory_text="",
        practice_text="",
        planned_result="",
        assessment_method="Беседа. Педагогическое наблюдение.",
        warnings=("Недостаточно данных источника; использован безопасный fallback.",),
        clause_coverage=(),
        clause_roles=(),
    )
    codes = production_readiness_codes(row)
    assert EMPTY_RESULT in codes
    assert SOURCE_NOT_MATCHED in codes
    assert EMPTY_CONTROL not in codes


def test_ce2_empty_source_stamps_structured_generic_only() -> None:
    derived = derive_fields_v2(
        topic_title="Тема 1",
        theory_text="",
        practice_text="",
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    assert PROVENANCE_GENERIC_ONLY in derived.provenance_codes
    assert GENERIC_ONLY == PROVENANCE_GENERIC_ONLY


def test_zero_hour_week_is_not_gated() -> None:
    row = _row(
        source=_source(
            theory_hours=0,
            practice_hours=0,
            total_hours=0,
            match_status=MatchStatus.NOT_MATCHED,
            program_content_full="",
        ),
        theory_text="",
        practice_text="",
        planned_result="",
        assessment_method="",
    )
    assert production_readiness_codes(row) == ()
