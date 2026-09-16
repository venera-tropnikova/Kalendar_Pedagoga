from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from calendar_pedagoga.confirmed_study_plan import (
    ConfirmedStudyPlanError,
    confirmed_plan_from_external_utp,
    confirmed_plan_from_manual,
)
from calendar_pedagoga.parsing import Hours, Section, Topic, UtpMetadata, UtpParseResult
from calendar_pedagoga.resolve_utp import resolve_utp
from calendar_pedagoga.scheduling import build_schedule, ordered_topics
from calendar_pedagoga.upload_validation import UploadPurpose, ValidatedUpload, validate_upload


REFERENCES = Path(__file__).resolve().parents[1] / "references"


def _program(name: str, payload: bytes = b"program") -> ValidatedUpload:
    return ValidatedUpload(UploadPurpose.PROGRAM, name, payload, None)


def _fractional_utp(*, weeks: int = 32) -> UtpParseResult:
    first = Topic(
        "1.1",
        "Первая тема",
        Hours(Decimal("1.5"), Decimal("1.5"), 0),
        parent_section="Раздел",
    )
    second = Topic(
        "1.2",
        "Вторая тема",
        Hours(Decimal("94.5"), Decimal("19.5"), Decimal("75")),
        parent_section="Раздел",
    )
    totals = Hours(Decimal("96"), Decimal("21"), Decimal("75"))
    return UtpParseResult(
        metadata=UtpMetadata(
            study_year="1 год обучения",
            hours_per_year=Decimal("96"),
            hours_per_week=Decimal("3"),
            study_weeks=weeks,
        ),
        sections=(Section("1", "Раздел", totals),),
        topics=(first, second),
        table_totals=totals,
    )


def test_external_utp_becomes_confirmed_study_plan() -> None:
    path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    upload = validate_upload(UploadPurpose.UTP, path.name, path.read_bytes())

    plan = resolve_utp(upload, _program("program.docx"), program_study_year=2)

    assert plan.source == "external_utp"
    assert plan.study_year == 2
    assert plan.table_totals == Hours(72, 22, 50)
    assert plan.topics == ordered_topics(upload.parsed)
    assert build_schedule(plan.as_utp_parse_result()) == build_schedule(upload.parsed)


def test_program_hours_and_embedded_utp_cannot_change_confirmed_plan() -> None:
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    upload = validate_upload(UploadPurpose.UTP, utp_path.name, utp_path.read_bytes())
    embedded_program = validate_upload(
        UploadPurpose.PROGRAM,
        program_path.name,
        program_path.read_bytes(),
    )

    plain = resolve_utp(upload, _program("plain.docx"), program_study_year=2)
    embedded = resolve_utp(upload, embedded_program, program_study_year=2)

    assert embedded.topics == plain.topics
    assert embedded.table_totals == plain.table_totals == Hours(72, 22, 50)
    assert embedded.study_weeks == plain.study_weeks == 36
    assert embedded.hours_per_week == plain.hours_per_week == 2


def test_fractional_hours_are_lossless_in_external_adapter() -> None:
    source = _fractional_utp()
    plan = confirmed_plan_from_external_utp(source)

    assert plan.total_hours == Decimal("96")
    assert plan.theory_hours == Decimal("21")
    assert plan.practice_hours == Decimal("75")
    assert plan.topics[0].hours.total == Decimal("1.5")
    assert plan.topics[1].hours.theory == Decimal("19.5")
    assert plan.as_utp_parse_result().table_totals == source.table_totals


@pytest.mark.parametrize(
    ("weeks", "weekly", "total"),
    ((32, 3, 96), (36, 2, 72)),
)
def test_confirmed_plan_preserves_32_and_36_week_grids(
    weeks: int,
    weekly: int,
    total: int,
) -> None:
    topic = Topic("1", "Тема", Hours(total, 0, total), "Тема", True)
    plan = confirmed_plan_from_manual(
        study_year=1,
        topics=(topic,),
        total_hours=total,
        theory_hours=0,
        practice_hours=total,
        study_weeks=weeks,
        hours_per_week=weekly,
    )

    schedule = build_schedule(plan.as_utp_parse_result())

    assert plan.source == "manual"
    assert len(schedule.weeks) == weeks
    assert sum(item.hours for item in schedule.elements) == total


def test_confirmed_plan_rejects_inconsistent_workload() -> None:
    source = _fractional_utp(weeks=36)

    with pytest.raises(ConfirmedStudyPlanError, match="недельная нагрузка"):
        confirmed_plan_from_external_utp(source)
