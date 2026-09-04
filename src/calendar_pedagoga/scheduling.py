"""Детерминированное распределение часов УТП по учебным неделям."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from calendar_pedagoga.academic_year import (
    APPROVED_ACADEMIC_YEAR,
    APPROVED_WEEK_COUNT,
    missing_local_exceptions_warning,
    normalize_academic_year,
)
from calendar_pedagoga.parsing import Topic, UtpParseResult


MONTHS = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


@dataclass(frozen=True)
class AcademicWeek:
    number: int
    start: date
    end: date
    month: str
    academic_year: str

    @property
    def date_range(self) -> str:
        if self.start.month == self.end.month:
            return f"{self.start:%d}–{self.end:%d.%m}"
        return f"{self.start:%d.%m}–{self.end:%d.%m}"


@dataclass(frozen=True)
class ScheduledElement:
    section: str
    topic_number: str | None
    topic: str
    part_type: str
    hours: int
    week: AcademicWeek


@dataclass(frozen=True)
class ScheduleResult:
    weeks: tuple[AcademicWeek, ...]
    elements: tuple[ScheduledElement, ...]
    warnings: tuple[str, ...] = ()


class ScheduleValidationError(ValueError):
    """Календарная сетка не прошла обязательные контрольные проверки."""


def _month_label(start: date, end: date) -> str:
    if start.month == end.month:
        return MONTHS[start.month]
    return f"{MONTHS[start.month]} / {MONTHS[end.month]}"


def _approved_2026_2027_ranges(weeks_count: int) -> list[tuple[date, date]]:
    """Утверждённый профиль 2026–2027: короткие недели и зимний разрыв."""

    ranges: list[tuple[date, date]] = []
    start = date(2026, 9, 1)
    ranges.append((start, date(2026, 9, 6)))
    start = date(2026, 9, 7)
    for _ in range(2, 18):
        ranges.append((start, start + timedelta(days=6)))
        start += timedelta(days=7)
    ranges.append((date(2026, 12, 28), date(2026, 12, 30)))
    start = date(2027, 1, 11)
    while len(ranges) < weeks_count:
        ranges.append((start, start + timedelta(days=6)))
        start += timedelta(days=7)
    return ranges


def _basic_academic_week_ranges(start_year: int, weeks_count: int) -> list[tuple[date, date]]:
    """36 календарных недель от 1 сентября без годовых каникул и переносов."""

    start = date(start_year, 9, 1)
    first_end = start + timedelta(days=6 - start.weekday())
    ranges = [(start, first_end)]
    cursor = first_end + timedelta(days=1)
    while len(ranges) < weeks_count:
        week_end = cursor + timedelta(days=6)
        ranges.append((cursor, week_end))
        cursor = week_end + timedelta(days=1)
    return ranges


def build_academic_weeks(
    academic_year: str = "2026–2027", weeks_count: int = 36
) -> tuple[AcademicWeek, ...]:
    """Построить сетку: профиль 2026–2027 или базовые 36 недель другого года."""

    canonical = normalize_academic_year(academic_year)
    if canonical is None or weeks_count != APPROVED_WEEK_COUNT:
        raise ValueError("Учебный год должен быть парой YYYY–YYYY+1, сетка — 36 недель.")
    if canonical == APPROVED_ACADEMIC_YEAR:
        ranges = _approved_2026_2027_ranges(weeks_count)
    else:
        ranges = _basic_academic_week_ranges(int(canonical[:4]), weeks_count)
    # Если учебная неделя пересекает границу месяцев, показываем оба месяца.
    # Такая строка получает собственную подпись (например, «Сентябрь / Октябрь»)
    # и поэтому не объединяется с соседними месячными блоками в DOCX.
    return tuple(
        AcademicWeek(index, start, end, _month_label(start, end), canonical)
        for index, (start, end) in enumerate(ranges, start=1)
    )


def ordered_topics(utp: UtpParseResult) -> tuple[Topic, ...]:
    """Вернуть позиции в порядке разделов исходной таблицы УТП."""
    ordered: list[Topic] = []
    for section in utp.sections:
        ordered.extend(
            topic for topic in utp.topics if topic.parent_section == section.title
        )
    return tuple(ordered)


def build_schedule(
    utp: UtpParseResult, academic_year: str = "2026–2027"
) -> ScheduleResult:
    weeks_count = utp.metadata.study_weeks
    weekly_load = utp.metadata.hours_per_week
    if weeks_count is None or weekly_load is None:
        raise ScheduleValidationError("В УТП не найдены количество недель или недельная нагрузка.")
    weeks = build_academic_weeks(academic_year, weeks_count)
    elements: list[ScheduledElement] = []
    week_index = 0
    used = 0
    for topic in ordered_topics(utp):
        for part_type, amount in (
            ("theory", topic.hours.theory),
            ("practice", topic.hours.practice),
        ):
            remaining = amount
            while remaining:
                if week_index >= len(weeks):
                    raise ScheduleValidationError("Часы УТП не помещаются в учебный период.")
                portion = min(remaining, weekly_load - used)
                elements.append(
                    ScheduledElement(
                        topic.parent_section or topic.title,
                        topic.number,
                        topic.title,
                        part_type,
                        portion,
                        weeks[week_index],
                    )
                )
                remaining -= portion
                used += portion
                if used == weekly_load:
                    week_index += 1
                    used = 0
    canonical = normalize_academic_year(academic_year) or academic_year
    warnings = (
        ()
        if canonical == APPROVED_ACADEMIC_YEAR
        else (missing_local_exceptions_warning(canonical),)
    )
    result = ScheduleResult(weeks, tuple(elements), warnings)
    validate_schedule(result, utp)
    return result


def validate_schedule(result: ScheduleResult, utp: UtpParseResult) -> None:
    weekly_load = utp.metadata.hours_per_week or 0
    expected = utp.table_totals
    if expected is None:
        raise ScheduleValidationError("В УТП не найдена итоговая строка часов.")
    totals = {
        "theory": sum(e.hours for e in result.elements if e.part_type == "theory"),
        "practice": sum(e.hours for e in result.elements if e.part_type == "practice"),
    }
    if totals["theory"] != expected.theory or totals["practice"] != expected.practice:
        raise ScheduleValidationError("Контрольные суммы теории или практики не совпали с УТП.")
    if sum(totals.values()) != expected.total:
        raise ScheduleValidationError("Общая сумма распределённых часов не совпала с УТП.")
    scheduled_topics = {(e.topic_number, e.topic, e.section) for e in result.elements}
    source_topics = {(t.number, t.title, t.parent_section or t.title) for t in utp.topics}
    if scheduled_topics != source_topics:
        raise ScheduleValidationError("Не все позиции УТП распределены по календарю.")
    loads = [sum(e.hours for e in result.elements if e.week.number == week.number) for week in result.weeks]
    if any(load > weekly_load for load in loads):
        raise ScheduleValidationError("Обнаружено превышение недельной нагрузки.")
    if any(load != weekly_load for load in loads):
        raise ScheduleValidationError("Обнаружены пустые часы внутри учебного периода.")
