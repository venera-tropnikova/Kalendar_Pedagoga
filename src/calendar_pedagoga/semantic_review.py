"""Structured manual review contract for unresolved CE2 weeks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json
from typing import Any, Literal

from calendar_pedagoga.confirmed_study_plan import ConfirmedStudyPlan
from calendar_pedagoga.content_engine_v2 import (
    ActionFrame,
    ContentEngineV2Result,
    LessonContentV2Row,
    REQUIRED_ACTION,
    _apply_result_grammar_gate,
    _blocking_grammar_issues,
    _control_covers_all_result_items,
    _normalize_spaces,
    _rc_verbosity_block_reasons,
    _r13_must_abstain_action_reconstruction,
    _role_is_required,
    validate_manual_lesson_content,
    week_has_unresolved_mandatory_review,
)
from calendar_pedagoga.program_parsing import ProgramData
from calendar_pedagoga.scheduling import ScheduleResult


ReviewStatus = Literal["REVIEW_REQUIRED"]


@dataclass(frozen=True)
class SemanticReviewCase:
    """One blocked week and the evidence a teacher must review."""

    review_id: str
    source_fingerprint: str
    week_number: int
    topic_title: str
    program_source: str
    required_clauses: tuple[str, ...]
    proposed_result: str
    proposed_control: str
    reasons: tuple[str, ...]
    status: ReviewStatus = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class ManualSemanticConfirmation:
    """Teacher-entered candidate bound to one exact review source."""

    review_id: str
    source_fingerprint: str
    planned_result: str
    assessment_method: str


@dataclass(frozen=True)
class SemanticReviewApplication:
    rows: tuple[LessonContentV2Row, ...]
    pending_cases: tuple[SemanticReviewCase, ...]
    accepted_review_ids: tuple[str, ...]
    errors: tuple[tuple[str, tuple[str, ...]], ...]


def draft_candidate_safety_issues(row: LessonContentV2Row) -> tuple[str, ...]:
    """Return only existing grammar/R13 issues for a draft proposal.

    Draft eligibility deliberately does not claim semantic coverage.  It only
    decides whether the already-produced proposal may be shown with an
    explicit review marker or must be replaced by a neutral placeholder.
    """

    result = row.planned_result.strip()
    control = row.assessment_method.strip()
    issues: list[str] = []
    if not result or not control:
        issues.append("RESULT/CONTROL не сформированы")

    role_map = dict(row.clause_roles)
    for clause, _status in row.clause_coverage:
        if (
            _role_is_required(role_map.get(clause, REQUIRED_ACTION))
            and _r13_must_abstain_action_reconstruction(clause)
        ):
            issues.append("R13: положительное действие небезопасно для черновика")

    source_context = _normalize_spaces(
        f"{row.theory_text} {row.practice_text} {row.source.program_content_full}"
    )
    issues.extend(_blocking_grammar_issues(result, control, source_context))

    if result:
        covered = tuple(
            (
                clause,
                "COVERED"
                if _role_is_required(role_map.get(clause, REQUIRED_ACTION))
                else status,
            )
            for clause, status in row.clause_coverage
        )
        candidate = ContentEngineV2Result(
            frame=ActionFrame(
                row.source.program_content_full,
                row.action,
                row.object,
                row.conditions,
            ),
            lesson_type=row.lesson_type,
            planned_result=result,
            assessment_method=control,
            theory_text=row.theory_text,
            practice_text=row.practice_text,
            warnings=(),
            clause_coverage=covered,
            clause_roles=row.clause_roles,
        )
        grammar_probe = _apply_result_grammar_gate(
            candidate,
            topic_title=row.source.topic_title,
            theory_text=row.theory_text,
            practice_text=row.practice_text,
            program_content=row.source.program_content_full,
            theory_hours=row.source.theory_hours,
            practice_hours=row.source.practice_hours,
        )
        if grammar_probe.planned_result != result or any(
            status == "NEEDS_REVIEW"
            and _role_is_required(role_map.get(clause, REQUIRED_ACTION))
            for clause, status in grammar_probe.clause_coverage
        ):
            issues.append("RESULT не прошёл существующий grammar gate")

    return tuple(dict.fromkeys(issues))


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _canonical(value.value)
    if is_dataclass(value):
        return {
            field.name: _canonical(getattr(value, field.name))
            for field in fields(value)
            if not field.name.startswith("_")
        }
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _digest(*values: Any) -> str:
    payload = json.dumps(
        [_canonical(value) for value in values],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_review_context_fingerprint(
    *,
    plan: ConfirmedStudyPlan,
    program: ProgramData | None,
    academic_year: str,
    schedule: ScheduleResult,
    semantic_revision: str,
) -> str:
    """Bind confirmations to every semantic input and generator revision."""

    return _digest(
        "semantic-review-v1",
        plan,
        program,
        academic_year,
        schedule,
        semantic_revision,
    )


def review_context_fingerprint_from_rows(
    rows: tuple[LessonContentV2Row, ...],
    *,
    semantic_revision: str,
) -> str:
    """Compatibility context for internal callers that only own CE2 rows."""

    return _digest("semantic-review-v1-rows", rows, semantic_revision)


def _case_reasons(row: LessonContentV2Row) -> tuple[str, ...]:
    role_map = dict(row.clause_roles)
    reasons = [
        clause
        for clause, status in row.clause_coverage
        if status == "NEEDS_REVIEW"
        and _role_is_required(role_map.get(clause, REQUIRED_ACTION))
    ]
    if (
        row.planned_result.strip()
        and not _control_covers_all_result_items(
            row.planned_result, row.assessment_method
        )
    ):
        reasons.append("CONTROL не покрывает финальный RESULT")
    reasons.extend(
        _rc_verbosity_block_reasons(row.planned_result, row.assessment_method)
    )
    reasons.extend(
        warning for warning in row.warnings if "NEEDS_REVIEW" in warning
    )
    return tuple(dict.fromkeys(reasons))


def build_semantic_review_cases(
    rows: tuple[LessonContentV2Row, ...],
    *,
    context_fingerprint: str,
) -> tuple[SemanticReviewCase, ...]:
    cases: list[SemanticReviewCase] = []
    for row in rows:
        if not week_has_unresolved_mandatory_review(row):
            continue
        role_map = dict(row.clause_roles)
        required = tuple(
            clause
            for clause, _status in row.clause_coverage
            if _role_is_required(role_map.get(clause, REQUIRED_ACTION))
        )
        source_fingerprint = _digest(
            context_fingerprint,
            row.source,
            row.theory_text,
            row.practice_text,
            row.clause_roles,
            required,
        )
        review_id = "semantic-review:" + _digest(
            source_fingerprint,
            row.source.week_number,
            row.source.topic_title,
        )
        cases.append(
            SemanticReviewCase(
                review_id=review_id,
                source_fingerprint=source_fingerprint,
                week_number=row.source.week_number,
                topic_title=row.source.topic_title,
                program_source=row.source.program_content_full,
                required_clauses=required,
                proposed_result=row.planned_result,
                proposed_control=row.assessment_method,
                reasons=_case_reasons(row),
            )
        )
    return tuple(cases)


def apply_manual_semantic_confirmations(
    rows: tuple[LessonContentV2Row, ...],
    cases: tuple[SemanticReviewCase, ...],
    confirmations: Mapping[str, ManualSemanticConfirmation] | None,
) -> SemanticReviewApplication:
    supplied = confirmations or {}
    case_by_week = {case.week_number: case for case in cases}
    current_ids = {case.review_id for case in cases}
    accepted: list[str] = []
    errors: dict[str, tuple[str, ...]] = {}
    output: list[LessonContentV2Row] = []

    for key in supplied:
        if key not in current_ids:
            errors[str(key)] = (
                "Подтверждение устарело: изменились SOURCE или входные данные.",
            )

    for row in rows:
        case = case_by_week.get(row.source.week_number)
        if case is None:
            output.append(row)
            continue
        confirmation = supplied.get(case.review_id)
        if confirmation is None:
            output.append(row)
            continue
        if (
            confirmation.review_id != case.review_id
            or confirmation.source_fingerprint != case.source_fingerprint
        ):
            errors[case.review_id] = (
                "Подтверждение устарело: изменились SOURCE или входные данные.",
            )
            output.append(row)
            continue
        validation = validate_manual_lesson_content(
            row,
            planned_result=confirmation.planned_result,
            assessment_method=confirmation.assessment_method,
        )
        if not validation.accepted:
            errors[case.review_id] = validation.issues
            output.append(row)
            continue
        accepted.append(case.review_id)
        output.append(validation.row)

    output_rows = tuple(output)
    pending_weeks = {
        row.source.week_number
        for row in output_rows
        if week_has_unresolved_mandatory_review(row)
    }
    return SemanticReviewApplication(
        rows=output_rows,
        pending_cases=tuple(
            case for case in cases if case.week_number in pending_weeks
        ),
        accepted_review_ids=tuple(accepted),
        errors=tuple(errors.items()),
    )
