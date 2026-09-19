"""Read-only projection of existing matching rows. No rematch."""

from __future__ import annotations

from collections.abc import Sequence

from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.semantic_atom.models import (
    MatchBinding,
    MatchConfidence,
    ObjectStatus,
    Provenance,
    ScheduleRow,
    fingerprint_source,
    make_object_id,
    projected_span,
)

ADAPTER_NAME = "match_c3"
_MATCH_CALLS = 0

_STATUS_TO_CONFIDENCE = {
    "EXACT": MatchConfidence.EXACT,
    "NORMALIZED": MatchConfidence.NORMALIZED,
    "NUMBER_MATCH": MatchConfidence.NUMBER_MATCH,
    "TEXT_MATCH": MatchConfidence.TEXT_MATCH,
    "USER_CONFIRMED": MatchConfidence.CONFIRMED,
    "UNCONFIRMED": MatchConfidence.AMBIGUOUS,
    "NOT_MATCHED": MatchConfidence.NONE,
}


def match_adapter_calls() -> int:
    return _MATCH_CALLS


def reset_match_adapter_calls() -> None:
    global _MATCH_CALLS
    _MATCH_CALLS = 0


def _hour(value: object) -> str:
    return "" if value is None else str(value)


def _confidence(status: str) -> MatchConfidence:
    return _STATUS_TO_CONFIDENCE[status]


def project_schedule_rows(rows: Sequence[CalendarContentRow]) -> tuple[ScheduleRow, ...]:
    global _MATCH_CALLS
    _MATCH_CALLS += 1
    projected: list[ScheduleRow] = []
    for row in rows:
        extra = f"{row.week_number}:{row.topic_title}:{_hour(row.total_hours)}"
        span = projected_span(
            adapter=ADAPTER_NAME,
            role="schedule",
            document=row.source_utp_name,
            extra=extra,
        )
        projected.append(
            ScheduleRow(
                id=make_object_id("schedule", extra),
                span=span,
                source_fingerprint=span.source_fingerprint,
                provenance=Provenance(
                    adapter=ADAPTER_NAME,
                    role="schedule",
                    clause_index=row.week_number,
                ),
                status=ObjectStatus.PROJECTED,
                week_number=row.week_number,
                date_range=row.date_range,
                month=row.month,
                topic_number="" if row.topic_number is None else str(row.topic_number),
                topic_title=row.topic_title,
                theory_hours=_hour(row.theory_hours),
                practice_hours=_hour(row.practice_hours),
                total_hours=_hour(row.total_hours),
            )
        )
    return tuple(projected)


def project_match_bindings(rows: Sequence[CalendarContentRow]) -> tuple[MatchBinding, ...]:
    global _MATCH_CALLS
    _MATCH_CALLS += 1
    bindings: list[MatchBinding] = []
    for row in rows:
        source = row.program_content_full
        source_hash = fingerprint_source(source)
        raw_status = str(row.match_status)
        evidence = (
            ("match_status", raw_status),
            ("program_topic", row.program_topic),
            ("program_section", row.program_section),
            ("source_hash", source_hash),
        )
        extra = f"{row.week_number}:{raw_status}:{source_hash}"
        span = projected_span(
            adapter=ADAPTER_NAME,
            role="match",
            document=row.source_program_name,
            extra=extra,
        )
        bindings.append(
            MatchBinding(
                id=make_object_id("match", extra),
                span=span,
                source_fingerprint=source_hash,
                provenance=Provenance(
                    adapter=ADAPTER_NAME,
                    role="match",
                    clause_index=row.week_number,
                ),
                status=ObjectStatus.PROJECTED,
                week_number=row.week_number,
                match_status=raw_status,
                confidence=_confidence(raw_status),
                source=source,
                source_hash=source_hash,
                warnings=tuple(row.warnings),
                evidence=evidence,
            )
        )
    return tuple(bindings)


def match_projection_gaps(
    rows: Sequence[CalendarContentRow],
    bindings: Sequence[MatchBinding],
) -> list[str]:
    gaps: list[str] = []
    if len(rows) != len(bindings):
        gaps.append("row_count")
        return gaps
    required = (
        "id",
        "span",
        "source_fingerprint",
        "provenance",
        "status",
        "match_status",
        "confidence",
        "source",
        "source_hash",
        "warnings",
        "evidence",
    )
    for row, binding in zip(rows, bindings, strict=True):
        for field in required:
            if getattr(binding, field, None) in {None}:
                gaps.append(f"W{row.week_number}:lost:{field}")
        if not binding.source_hash and row.program_content_full:
            gaps.append(f"W{row.week_number}:source_hash")
        if binding.source_hash != fingerprint_source(row.program_content_full):
            gaps.append(f"W{row.week_number}:source_hash")
        if binding.source != row.program_content_full:
            gaps.append(f"W{row.week_number}:source")
        if binding.match_status != str(row.match_status):
            gaps.append(f"W{row.week_number}:match_status")
        if binding.confidence != _confidence(str(row.match_status)):
            gaps.append(f"W{row.week_number}:confidence")
        if binding.warnings != tuple(row.warnings):
            gaps.append(f"W{row.week_number}:warnings")
        if binding.week_number != row.week_number:
            gaps.append(f"W{row.week_number}:week")
    return gaps
