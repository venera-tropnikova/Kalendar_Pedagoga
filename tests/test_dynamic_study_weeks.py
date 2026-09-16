from dataclasses import replace
from pathlib import Path

import pytest

from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.docx_qa import has_blocking_qa_issues, validate_calendar_docx
from calendar_pedagoga.organization_template import select_calendar_template
from calendar_pedagoga.parsing import Hours, parse_utp
from calendar_pedagoga.pipeline import run_calendar_pipeline
from calendar_pedagoga.scheduling import build_academic_weeks, build_schedule


REFERENCES = Path(__file__).resolve().parents[1] / "references"
SOURCE_UTP = REFERENCES / "УТП ТП 3г. 2ч.docx"


def _variable_week_utp(*, total: int, weekly: int, weeks: int):
    source = parse_utp(SOURCE_UTP)
    assert source.table_totals is not None
    extra = total - source.table_totals.total
    last = source.topics[-1]
    topics = (
        *source.topics[:-1],
        replace(
            last,
            hours=Hours(
                last.hours.total + extra,
                last.hours.theory,
                last.hours.practice + extra,
            ),
        ),
    )
    return replace(
        source,
        metadata=replace(
            source.metadata,
            hours_per_year=total,
            hours_per_week=weekly,
            study_weeks=weeks,
            workload_provenance="document",
        ),
        topics=topics,
        table_totals=Hours(
            total,
            source.table_totals.theory,
            source.table_totals.practice + extra,
        ),
    )


@pytest.mark.parametrize(("total", "weekly"), ((96, 3), (128, 4)))
def test_dynamic_32_week_pipeline_generates_valid_docx(
    total: int,
    weekly: int,
) -> None:
    utp = _variable_week_utp(total=total, weekly=weekly, weeks=32)
    schedule = build_schedule(utp)
    content = build_content_model(schedule, utp, None, SOURCE_UTP.name)

    assert len(schedule.weeks) == 32
    assert len(content) == 32

    result = run_calendar_pipeline(
        utp,
        None,
        academic_year="2026–2027",
        template=select_calendar_template(),
        source_utp_name=SOURCE_UTP.name,
        use_ai=False,
    )

    assert len(result.resolved_lessons) == 32
    assert sum(row.source.source.total_hours for row in result.resolved_lessons) == total
    assert not has_blocking_qa_issues(
        validate_calendar_docx(result.content, expected_weeks=32)
    )


def test_legacy_default_grid_is_bit_exact_to_explicit_36_weeks() -> None:
    default = build_academic_weeks("2026–2027")
    explicit = build_academic_weeks("2026–2027", 36)

    assert default == explicit
    assert len(default) == 36
