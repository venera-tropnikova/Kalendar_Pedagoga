"""Production final-ready gate: proven SOURCE plus nonempty RESULT/CONTROL.

Readiness is decided from structured provenance only: MatchStatus,
program_content_full, weekly_content_assigned, provenance_codes, and
empty RESULT/CONTROL fields.  No program/topic/week/warning string matching.
"""

from __future__ import annotations

from calendar_pedagoga.content_engine_v2 import (
    LessonContentV2Row,
    PROVENANCE_UNINFORMATIVE_TOPIC_TITLE,
    _rc_verbosity_block_reasons,
    is_sentence_frame_closed_row,
    is_utp_topic_derived_row,
    week_has_unresolved_mandatory_review,
)
from calendar_pedagoga.content_generation import CalendarContentRow, WeekTopicPart
from calendar_pedagoga.matching import MatchStatus


SOURCE_NOT_MATCHED = "SOURCE_NOT_MATCHED"
EMPTY_RESULT = "EMPTY_RESULT"
EMPTY_CONTROL = "EMPTY_CONTROL"
GENERIC_ONLY = "GENERIC_ONLY"
UNINFORMATIVE_TOPIC_TITLE = "UNINFORMATIVE_TOPIC_TITLE"

READINESS_CODES = frozenset(
    {
        SOURCE_NOT_MATCHED,
        EMPTY_RESULT,
        EMPTY_CONTROL,
        GENERIC_ONLY,
        UNINFORMATIVE_TOPIC_TITLE,
    }
)

_PROVEN_MATCH_STATUSES = frozenset(
    {
        MatchStatus.EXACT,
        MatchStatus.NORMALIZED,
        MatchStatus.TEXT_MATCH,
        MatchStatus.USER_CONFIRMED,
    }
)
_MISSING_FIELD_BY_CODE = {
    SOURCE_NOT_MATCHED: "SOURCE",
    EMPTY_RESULT: "RESULT",
    EMPTY_CONTROL: "CONTROL",
    GENERIC_ONLY: "RESULT/CONTROL",
    UNINFORMATIVE_TOPIC_TITLE: "название темы",
}
_ACTION_BY_CODE = {
    SOURCE_NOT_MATCHED: (
        "Сопоставьте фрагмент содержания программы с темой недели "
        "или явно подтвердите ручные данные."
    ),
    EMPTY_RESULT: (
        "Заполните планируемый результат текстом из SOURCE, "
        "а не только из заголовка темы."
    ),
    EMPTY_CONTROL: (
        "Заполните контроль, согласованный с RESULT."
    ),
    GENERIC_ONLY: (
        "Замените шаблонный RESULT/CONTROL текстом из SOURCE "
        "или явно подтвердите содержание недели."
    ),
    UNINFORMATIVE_TOPIC_TITLE: (
        "Уточните название темы. RESULT и CONTROL писать не нужно."
    ),
}


def _has_hours(value: object) -> bool:
    if value is None:
        return False
    try:
        return value > 0
    except TypeError:
        return False


def _synthetic_part(source: CalendarContentRow) -> WeekTopicPart:
    return WeekTopicPart(
        topic_number=source.topic_number,
        topic_title=source.topic_title,
        section=source.section,
        theory_hours=source.theory_hours,
        practice_hours=source.practice_hours,
        match_status=source.match_status,
        program_section=source.program_section,
        program_topic=source.program_topic,
        program_content_full=source.program_content_full,
        warnings=source.warnings,
        weekly_content_assigned=False,
    )


def _hour_bearing_parts(row: LessonContentV2Row) -> tuple[WeekTopicPart, ...]:
    source = row.source
    parts = source.week_parts or (_synthetic_part(source),)
    bearing = tuple(
        part
        for part in parts
        if _has_hours(part.theory_hours) or _has_hours(part.practice_hours)
    )
    if bearing:
        return bearing
    if (
        _has_hours(source.total_hours)
        or _has_hours(source.theory_hours)
        or _has_hours(source.practice_hours)
    ):
        return parts
    return ()


def _part_has_proven_source(part: WeekTopicPart) -> bool:
    if part.match_status not in _PROVEN_MATCH_STATUSES:
        return False
    if (part.program_content_full or "").strip():
        return True
    return bool(part.weekly_content_assigned)


def _has_source_cell_text(row: LessonContentV2Row) -> bool:
    return bool(row.theory_text.strip() or row.practice_text.strip())


def _has_structured_generic_only(row: LessonContentV2Row) -> bool:
    return GENERIC_ONLY in row.provenance_codes


def _has_covered_required_clause(row: LessonContentV2Row) -> bool:
    return any(status == "COVERED" for _clause, status in row.clause_coverage)


def production_readiness_codes(row: LessonContentV2Row) -> tuple[str, ...]:
    """Return gate codes for one hour-bearing week. Empty when the week is proven."""

    if not _hour_bearing_parts(row):
        return ()

    codes: list[str] = []
    parts = _hour_bearing_parts(row)
    closed = is_sentence_frame_closed_row(row)
    closed_title = is_utp_topic_derived_row(row) and closed
    uninformative = PROVENANCE_UNINFORMATIVE_TOPIC_TITLE in row.provenance_codes
    result = (row.planned_result or "").strip()
    control = (row.assessment_method or "").strip()
    proven = all(_part_has_proven_source(part) for part in parts)
    if (
        not closed_title
        and not uninformative
        and (not proven or not _has_source_cell_text(row))
    ):
        codes.append(SOURCE_NOT_MATCHED)

    if not result:
        codes.append(EMPTY_RESULT)
    if not control:
        codes.append(EMPTY_CONTROL)
    if (
        not closed_title
        and not uninformative
        and _has_structured_generic_only(row)
        and not _has_covered_required_clause(row)
    ):
        codes.append(GENERIC_ONLY)
    if uninformative:
        codes.append(UNINFORMATIVE_TOPIC_TITLE)
    return tuple(dict.fromkeys(codes))


def row_blocks_final_delivery(row: LessonContentV2Row) -> bool:
    """True when the week must keep the document in draft.

    A filled closed row whose only leftover is informational NEEDS_REVIEW is
    a notice. Empty cells, GENERIC_ONLY, unmatched SOURCE, verbosity, and a
    non-closed unresolved row stay blocking.
    """

    if production_readiness_codes(row):
        return True
    result = (row.planned_result or "").strip()
    control = (row.assessment_method or "").strip()
    if not result or not control:
        return True
    if _rc_verbosity_block_reasons(result, control):
        return True
    if is_sentence_frame_closed_row(row):
        return False
    return week_has_unresolved_mandatory_review(row)


def rows_have_production_readiness_gaps(
    rows: tuple[LessonContentV2Row, ...],
) -> bool:
    return any(production_readiness_codes(row) for row in rows)


def missing_fields_for_codes(codes: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            _MISSING_FIELD_BY_CODE[code]
            for code in codes
            if code in _MISSING_FIELD_BY_CODE
        )
    )


def user_action_for_codes(codes: tuple[str, ...] | list[str]) -> str:
    actions = [
        _ACTION_BY_CODE[code]
        for code in codes
        if code in _ACTION_BY_CODE
    ]
    return " ".join(dict.fromkeys(actions))


def readiness_codes_from_reasons(reasons: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(reason for reason in reasons if reason in READINESS_CODES)
