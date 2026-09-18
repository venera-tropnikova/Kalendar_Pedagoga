from __future__ import annotations

from dataclasses import replace

import pytest

from calendar_pedagoga.confirmed_study_plan import confirmed_plan_from_manual_rows
from calendar_pedagoga.content_engine_v2 import (
    LessonContentV2Row,
    REQUIRED_ACTION,
    build_lesson_content_v2,
    derive_fields_v2,
)
from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.pipeline import (
    CalendarDocumentStatus,
    SemanticReviewRequired,
    _build_pipeline_lesson_content,
    _build_pipeline_lesson_content_outcome,
    _draft_resolved_rows,
    _lesson_rows_from_v2,
)
from calendar_pedagoga.program_parsing import ProgramData
from calendar_pedagoga.scheduling import build_schedule
from calendar_pedagoga.semantic_review import (
    ManualSemanticConfirmation,
    apply_manual_semantic_confirmations,
    build_review_context_fingerprint,
    build_semantic_review_cases,
    draft_candidate_safety_issues,
    review_proposal_docx_issues,
    source_grounded_review_proposal,
)
from calendar_pedagoga.lesson_resolution import resolve_lesson_content


def _source(week: int, text: str = "Выполнение упражнения.") -> CalendarContentRow:
    return CalendarContentRow(
        week_number=week,
        date_range="01–07.09",
        month="Сентябрь",
        section="Раздел",
        topic_number=str(week),
        topic_title=f"Тема {week}",
        source_topic_title=f"Тема {week}",
        theory_hours=0,
        practice_hours=2,
        total_hours=2,
        match_status=MatchStatus.EXACT,
        program_section="Раздел",
        program_topic=f"Тема {week}",
        program_content_full=text,
        program_content_preview=text,
        source_program_name="Программа",
        source_utp_name="utp.docx",
    )


def _review_row(week: int = 1, text: str = "Выполнение упражнения.") -> LessonContentV2Row:
    source = _source(week, text)
    derived = derive_fields_v2(
        topic_title=source.topic_title,
        theory_text="",
        practice_text=text,
        program_content=text,
        theory_hours=0,
        practice_hours=2,
    )
    clause = text.rstrip(".")
    return LessonContentV2Row(
        source=source,
        theory_text=derived.theory_text,
        practice_text=derived.practice_text,
        lesson_type=derived.lesson_type,
        planned_result=derived.planned_result,
        assessment_method=derived.assessment_method,
        action=derived.frame.action,
        object=derived.frame.object,
        conditions=derived.frame.conditions,
        warnings=(f"NEEDS_REVIEW: {clause}",),
        clause_coverage=((clause, "NEEDS_REVIEW"),),
        clause_roles=((clause, REQUIRED_ACTION),),
    )


def _confirmation(case, *, result="Выполняет упражнение.", control=None):
    return ManualSemanticConfirmation(
        review_id=case.review_id,
        source_fingerprint=case.source_fingerprint,
        planned_result=result,
        assessment_method=(
            control or "педагогическое наблюдение за выполнением упражнения"
        ),
    )


def test_review_cases_include_only_blocked_weeks() -> None:
    blocked = _review_row(1)
    covered = replace(
        _review_row(2),
        clause_coverage=(("Выполнение упражнения", "COVERED"),),
        warnings=(),
    )
    cases = build_semantic_review_cases(
        (blocked, covered), context_fingerprint="context"
    )
    assert [case.week_number for case in cases] == [1]
    assert cases[0].program_source == "Выполнение упражнения."
    assert cases[0].status == "REVIEW_REQUIRED"


@pytest.mark.parametrize(
    ("result", "control", "issue"),
    [
        ("", "", "RESULT не заполнен"),
        (
            "Собирает модель.",
            "педагогическое наблюдение за сборкой модели",
            "обязательный SOURCE",
        ),
        (
            "Выполняет упражнение.",
            "устный опрос по истории",
            "CONTROL не покрывает",
        ),
    ],
)
def test_invalid_manual_confirmation_stays_blocked(
    result: str, control: str, issue: str
) -> None:
    row = _review_row()
    cases = build_semantic_review_cases((row,), context_fingerprint="context")
    confirmation = _confirmation(cases[0], result=result, control=control)
    applied = apply_manual_semantic_confirmations(
        (row,), cases, {cases[0].review_id: confirmation}
    )
    assert applied.pending_cases == cases
    assert issue in " ".join(dict(applied.errors)[cases[0].review_id])


def test_r13_prohibition_cannot_be_confirmed_as_positive_action() -> None:
    source = "Выполнение упражнения без страховки запрещено."
    row = _review_row(text=source)
    cases = build_semantic_review_cases((row,), context_fingerprint="context")
    confirmation = _confirmation(
        cases[0],
        result="Выполняет упражнение без страховки.",
        control="педагогическое наблюдение за выполнением упражнения без страховки",
    )
    applied = apply_manual_semantic_confirmations(
        (row,), cases, {cases[0].review_id: confirmation}
    )
    assert applied.pending_cases == cases
    assert "R13" in " ".join(dict(applied.errors)[cases[0].review_id])


def test_prohibition_label_can_be_confirmed_as_a_knowledge_object() -> None:
    clauses = (
        "Животные и птицы в рисунках детей",
        "Запрещающие знаки «Берегите природу»",
    )
    row = replace(
        _review_row(text=". ".join(clauses) + "."),
        clause_coverage=tuple((clause, "NEEDS_REVIEW") for clause in clauses),
        clause_roles=tuple((clause, REQUIRED_ACTION) for clause in clauses),
    )
    case = build_semantic_review_cases(
        (row,), context_fingerprint="context"
    )[0]
    confirmation = ManualSemanticConfirmation(
        review_id=case.review_id,
        source_fingerprint=case.source_fingerprint,
        planned_result=(
            "Характеризует животных и птиц в рисунках детей и запрещающие "
            "знаки «Берегите природу»."
        ),
        assessment_method=(
            "Устный опрос по животным и птицам в рисунках детей и "
            "запрещающим знакам «Берегите природу»."
        ),
    )

    applied = apply_manual_semantic_confirmations(
        (row,), (case,), {case.review_id: confirmation}
    )

    assert applied.pending_cases == ()
    assert applied.accepted_review_ids == (case.review_id,)
    assert applied.errors == ()


def test_valid_confirmation_is_revalidated_and_covered() -> None:
    row = _review_row()
    cases = build_semantic_review_cases((row,), context_fingerprint="context")
    confirmation = _confirmation(cases[0])
    applied = apply_manual_semantic_confirmations(
        (row,), cases, {cases[0].review_id: confirmation}
    )
    assert applied.pending_cases == ()
    assert applied.accepted_review_ids == (cases[0].review_id,)
    assert applied.rows[0].clause_coverage == (("Выполнение упражнения", "COVERED"),)
    assert applied.rows[0].source is row.source
    assert applied.rows[0].lesson_type == row.lesson_type


def test_stale_fingerprint_and_review_id_are_rejected() -> None:
    row = _review_row()
    cases = build_semantic_review_cases((row,), context_fingerprint="new")
    stale = ManualSemanticConfirmation(
        review_id="semantic-review:stale",
        source_fingerprint="stale",
        planned_result="Выполняет упражнение.",
        assessment_method="педагогическое наблюдение за выполнением упражнения",
    )
    applied = apply_manual_semantic_confirmations(
        (row,), cases, {stale.review_id: stale}
    )
    assert applied.pending_cases == cases
    assert "устарело" in " ".join(dict(applied.errors)[stale.review_id])

    wrong_fingerprint = replace(
        _confirmation(cases[0]), source_fingerprint="stale"
    )
    applied = apply_manual_semantic_confirmations(
        (row,), cases, {cases[0].review_id: wrong_fingerprint}
    )
    assert applied.pending_cases == cases
    assert "устарело" in " ".join(dict(applied.errors)[cases[0].review_id])


def test_confirming_one_week_does_not_unblock_another() -> None:
    rows = (_review_row(1), _review_row(2))
    cases = build_semantic_review_cases(rows, context_fingerprint="context")
    applied = apply_manual_semantic_confirmations(
        rows,
        cases,
        {cases[0].review_id: _confirmation(cases[0])},
    )
    assert applied.accepted_review_ids == (cases[0].review_id,)
    assert [case.week_number for case in applied.pending_cases] == [2]


def test_pipeline_exposes_structured_review_required(monkeypatch) -> None:
    row = _review_row()
    monkeypatch.setattr(
        "calendar_pedagoga.pipeline.build_lesson_content_v2",
        lambda _content: (row,),
    )
    with pytest.raises(SemanticReviewRequired) as raised:
        _build_pipeline_lesson_content(
            (row.source,),
            use_content_engine_v2=True,
            semantic_revision="revision",
        )
    assert raised.value.status == "REVIEW_REQUIRED"
    assert [case.week_number for case in raised.value.review_cases] == [1]

    case = raised.value.review_cases[0]
    lessons = _build_pipeline_lesson_content(
        (row.source,),
        use_content_engine_v2=True,
        semantic_revision="revision",
        manual_confirmations={case.review_id: _confirmation(case)},
    )
    assert lessons[0].planned_result == "Выполняет упражнение."


def test_pipeline_review_outcome_is_draft_ready(monkeypatch) -> None:
    row = _review_row()
    monkeypatch.setattr(
        "calendar_pedagoga.pipeline.build_lesson_content_v2",
        lambda _content: (row,),
    )
    outcome = _build_pipeline_lesson_content_outcome(
        (row.source,),
        use_content_engine_v2=True,
        semantic_revision="revision",
    )
    assert outcome.status is CalendarDocumentStatus.DRAFT_READY
    assert [case.week_number for case in outcome.review_cases] == [1]
    assert len(outcome.rows) == 1


def _docx_rows_for(
    *v2_rows: LessonContentV2Row,
) -> tuple[tuple, tuple]:
    cases = build_semantic_review_cases(v2_rows, context_fingerprint="context")
    resolved = resolve_lesson_content(_lesson_rows_from_v2(v2_rows))
    resolved = tuple(
        replace(
            item,
            planned_result=row.planned_result,
            assessment_method=row.assessment_method,
        )
        for item, row in zip(resolved, v2_rows)
    )
    return _draft_resolved_rows(resolved, v2_rows, cases), cases


def test_draft_keeps_safe_review_content_and_blanks_r13() -> None:
    safe = _review_row(1)
    unsafe = _review_row(
        2,
        "Выполнение упражнения без страховки запрещено.",
    )
    proven = replace(
        _review_row(3),
        clause_coverage=(("Выполнение упражнения", "COVERED"),),
        warnings=(),
    )
    assert not review_proposal_docx_issues(safe)
    assert any("R13" in issue for issue in review_proposal_docx_issues(unsafe))
    marked, cases = _docx_rows_for(safe, unsafe, proven)
    pending_weeks = {case.week_number for case in cases}
    assert pending_weeks == {1, 2}
    assert marked[0].planned_result == safe.planned_result
    assert marked[0].assessment_method == safe.assessment_method
    assert marked[1].planned_result == ""
    assert marked[1].assessment_method == ""
    assert marked[2].planned_result == proven.planned_result
    assert marked[2].assessment_method == proven.assessment_method


def test_semantic_coverage_review_keeps_safe_docx_text() -> None:
    row = _review_row()
    assert not draft_candidate_safety_issues(row)
    assert not review_proposal_docx_issues(row)
    marked, cases = _docx_rows_for(row)
    assert [case.week_number for case in cases] == [1]
    assert marked[0].planned_result == row.planned_result
    assert marked[0].assessment_method == row.assessment_method
    assert marked[0].planned_result
    assert marked[0].assessment_method


def _unsafe_proposal(text: str) -> LessonContentV2Row:
    """Week whose proposed CONTROL cannot cross the first safe gate."""

    row = replace(_review_row(text=text), assessment_method="устный опрос по истории")
    assert review_proposal_docx_issues(row)
    return row


def _stub_second_candidate(monkeypatch, *, result: str, control: str) -> None:
    """Force one exact second-pass candidate to probe a single gate."""

    def _fake_derive(**kwargs):
        derived = derive_fields_v2(**kwargs)
        return replace(derived, planned_result=result, assessment_method=control)

    monkeypatch.setattr(
        "calendar_pedagoga.semantic_review.derive_fields_v2", _fake_derive
    )


def test_unsafe_proposal_is_replaced_by_source_grounded_candidate() -> None:
    row = _unsafe_proposal("Виды туристских узлов.")
    rebuilt = source_grounded_review_proposal(row)
    assert rebuilt is not None
    marked, cases = _docx_rows_for(row)
    assert [case.week_number for case in cases] == [1]
    assert marked[0].planned_result == rebuilt[0]
    assert marked[0].assessment_method == rebuilt[1]
    assert marked[0].assessment_method != row.assessment_method
    assert "туристских узлов" in marked[0].planned_result
    assert "истории" not in marked[0].assessment_method


def test_second_candidate_grammar_fail_blanks_docx_cells(monkeypatch) -> None:
    row = _unsafe_proposal("Виды туристских узлов.")
    _stub_second_candidate(
        monkeypatch,
        result="Характеризует правилу безопасного поведения.",
        control="устный опрос по правилу безопасного поведения",
    )
    assert source_grounded_review_proposal(row) is None
    marked, _cases = _docx_rows_for(row)
    assert marked[0].planned_result == ""
    assert marked[0].assessment_method == ""


def test_second_candidate_control_coverage_fail_blanks_docx_cells(monkeypatch) -> None:
    row = _unsafe_proposal("Виды туристских узлов.")
    _stub_second_candidate(
        monkeypatch,
        result="Называет виды туристских узлов.",
        control="устный опрос по истории",
    )
    assert source_grounded_review_proposal(row) is None
    marked, _cases = _docx_rows_for(row)
    assert marked[0].planned_result == ""
    assert marked[0].assessment_method == ""


def test_r13_source_leaves_docx_cells_empty() -> None:
    row = _review_row(text="Выполнение упражнения без страховки запрещено.")
    assert any("R13" in issue for issue in review_proposal_docx_issues(row))
    assert source_grounded_review_proposal(row) is None
    marked, cases = _docx_rows_for(row)
    assert [case.week_number for case in cases] == [1]
    assert marked[0].planned_result == ""
    assert marked[0].assessment_method == ""


def test_second_pass_adds_no_meaning_outside_source() -> None:
    # The only candidate this SOURCE can derive names the topic, not the clause.
    row = _unsafe_proposal("Правила безопасного поведения.")
    assert source_grounded_review_proposal(row) is None
    marked, cases = _docx_rows_for(row)
    assert [case.week_number for case in cases] == [1]
    assert marked[0].planned_result == ""
    assert marked[0].assessment_method == ""


def test_pipeline_one_confirmation_keeps_other_week_blocked(monkeypatch) -> None:
    rows = (_review_row(1), _review_row(2))
    monkeypatch.setattr(
        "calendar_pedagoga.pipeline.build_lesson_content_v2",
        lambda _content: rows,
    )
    with pytest.raises(SemanticReviewRequired) as first:
        _build_pipeline_lesson_content(
            tuple(row.source for row in rows),
            use_content_engine_v2=True,
            semantic_revision="revision",
        )
    cases = first.value.review_cases
    with pytest.raises(SemanticReviewRequired) as second:
        _build_pipeline_lesson_content(
            tuple(row.source for row in rows),
            use_content_engine_v2=True,
            semantic_revision="revision",
            manual_confirmations={cases[0].review_id: _confirmation(cases[0])},
        )
    assert second.value.accepted_review_ids == (cases[0].review_id,)
    assert [case.week_number for case in second.value.review_cases] == [2]


def test_no_block_path_is_bit_compatible() -> None:
    content = (_source(1),)
    before = _build_pipeline_lesson_content(
        content, use_content_engine_v2=True, semantic_revision="before"
    )
    after = _build_pipeline_lesson_content(
        content,
        use_content_engine_v2=True,
        semantic_revision="after",
        manual_confirmations={},
    )
    assert after == before


def test_context_changes_invalidate_case_identity() -> None:
    plan = confirmed_plan_from_manual_rows(
        study_year=1,
        rows=[{"section": "Раздел", "topic": "Тема", "total": 2, "theory": 0, "practice": 2}],
        study_weeks=1,
        hours_per_week=2,
    )
    program = ProgramData(
        title="Программа",
        duration=None,
        student_age=None,
        goal=None,
        tasks=(),
        lesson_forms=(),
        teaching_methods=(),
        expected_results=(),
        knowledge_outcomes=(),
        skill_outcomes=(),
        content_items=(),
    )
    schedule = build_schedule(plan.as_utp_parse_result(), "2026–2027")

    def context(**changes):
        values = dict(
            plan=plan,
            program=program,
            academic_year="2026–2027",
            schedule=schedule,
            semantic_revision="revision-a",
        )
        values.update(changes)
        return build_review_context_fingerprint(**values)

    baseline = context()
    changed_topic = replace(plan.topics[0], title="Другая тема")
    variants = (
        context(program=replace(program, title="Другая программа")),
        context(plan=replace(plan, topics=(changed_topic,))),
        context(plan=replace(plan, study_year=2)),
        context(academic_year="2027–2028"),
        context(schedule=replace(schedule, warnings=("changed",))),
        context(semantic_revision="revision-b"),
    )
    assert all(value != baseline for value in variants)
    row = _review_row()
    baseline_case = build_semantic_review_cases(
        (row,), context_fingerprint=baseline
    )[0]
    for value in variants:
        changed_case = build_semantic_review_cases(
            (row,), context_fingerprint=value
        )[0]
        assert changed_case.source_fingerprint != baseline_case.source_fingerprint
        assert changed_case.review_id != baseline_case.review_id
