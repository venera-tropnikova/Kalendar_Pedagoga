from pathlib import Path

import pytest

from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.scheduling import build_schedule


REFERENCES = Path(__file__).resolve().parents[1] / "references"


@pytest.mark.parametrize(
    "filename, expected_theory, expected_practice, expected_positions",
    (
        ("УТП КЛЮЧ 2 г. 2ч.docx", 22, 50, 13),
        ("УТП ТП 3г. 2ч.docx", 24, 48, 22),
    ),
)
def test_reference_utp_fills_36_weeks_without_overload(
    filename: str,
    expected_theory: int,
    expected_practice: int,
    expected_positions: int,
) -> None:
    utp = parse_utp(REFERENCES / filename)
    schedule = build_schedule(utp)
    loads = [
        sum(element.hours for element in schedule.elements if element.week.number == week.number)
        for week in schedule.weeks
    ]

    assert len(schedule.weeks) == 36
    assert sum(element.hours for element in schedule.elements) == 72
    assert sum(e.hours for e in schedule.elements if e.part_type == "theory") == expected_theory
    assert sum(e.hours for e in schedule.elements if e.part_type == "practice") == expected_practice
    assert len({(e.topic_number, e.topic, e.section) for e in schedule.elements}) == expected_positions
    assert all(load == 2 for load in loads)


def test_key_week_boundaries_follow_reference_calendar() -> None:
    utp = parse_utp(REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx")
    weeks = build_schedule(utp).weeks

    assert weeks[0].date_range == "01–06.09"
    assert weeks[17].date_range == "28–30.12"
    assert weeks[18].date_range == "11–17.01"
    assert weeks[-1].date_range == "10–16.05"


def test_cross_month_weeks_show_both_months() -> None:
    utp = parse_utp(REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx")
    weeks = build_schedule(utp).weeks

    by_range = {week.date_range: week.month for week in weeks}
    assert by_range["28.09–04.10"] == "Сентябрь / Октябрь"
    assert by_range["26.10–01.11"] == "Октябрь / Ноябрь"
    assert by_range["30.11–06.12"] == "Ноябрь / Декабрь"
    assert by_range["29.03–04.04"] == "Март / Апрель"
    assert by_range["26.04–02.05"] == "Апрель / Май"

    # Короткая декабрьская неделя не пересекает месяц.
    assert by_range["28–30.12"] == "Декабрь"
