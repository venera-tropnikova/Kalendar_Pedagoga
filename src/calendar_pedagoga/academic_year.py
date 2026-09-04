"""Учебный год как канон Y–(Y+1), без литерального списка лет."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING
import re

if TYPE_CHECKING:
    from calendar_pedagoga.parsing import UtpParseResult
    from calendar_pedagoga.program_parsing import ProgramData


APPROVED_ACADEMIC_YEAR = "2026–2027"
APPROVED_WEEK_COUNT = 36

_CONTEXT = re.compile(r"учебн\w*\s+год", re.IGNORECASE)
_PAIR = re.compile(r"(?P<start>\d{4})\s*[-–]\s*(?P<end>\d{4})")
_SHORT = re.compile(r"(?P<start>\d{4})\s*/\s*(?P<end>\d{2})(?!\d)")


class AcademicYearStatus(StrEnum):
    AUTO = "auto"
    SINGLE = "single"
    CONFLICT = "conflict"
    MISSING = "missing"


@dataclass(frozen=True)
class AcademicYearMention:
    year: str
    snippet: str


@dataclass(frozen=True)
class AcademicYearSource:
    year: str
    origin: str
    snippet: str


@dataclass(frozen=True)
class AcademicYearResolution:
    status: AcademicYearStatus
    suggested: str | None
    sources: tuple[AcademicYearSource, ...]
    message: str


def format_academic_year(start_year: int) -> str:
    """Вернуть канон Y–(Y+1)."""

    return f"{start_year}–{start_year + 1}"


def default_academic_year_start(today: date | None = None) -> int:
    """Начало текущего учебного года по календарю: с сентября — этот год."""

    current = today or date.today()
    return current.year if current.month >= 9 else current.year - 1


def academic_year_start(value: str | None) -> int | None:
    normalized = normalize_academic_year(value)
    if normalized is None:
        return None
    return int(normalized[:4])


def _expand_short_end(start: int, end_raw: str) -> int:
    end = (start // 100) * 100 + int(end_raw)
    if end <= start:
        end += 100
    return end


def _canonical_pair(start: int, end: int) -> str | None:
    if end != start + 1:
        return None
    if start < 1990 or start > 2100:
        return None
    return format_academic_year(start)


def normalize_academic_year(value: str | None) -> str | None:
    """Нормализовать уже выделенную или выбранную пару в канон Y–(Y+1)."""

    if not value:
        return None
    pair = _PAIR.search(value)
    if pair is not None:
        return _canonical_pair(int(pair.group("start")), int(pair.group("end")))
    short = _SHORT.search(value)
    if short is None:
        return None
    start = int(short.group("start"))
    return _canonical_pair(start, _expand_short_end(start, short.group("end")))


def academic_year_period(academic_year: str | None) -> tuple[date, date] | None:
    normalized = normalize_academic_year(academic_year)
    if normalized is None:
        return None
    start_year = int(normalized[:4])
    return date(start_year, 9, 1), date(start_year + 1, 8, 31)


def extract_academic_year_mentions(text: str) -> tuple[AcademicYearMention, ...]:
    """Найти пары лет только рядом с явным «учебный год»."""

    if not text:
        return ()
    mentions: list[AcademicYearMention] = []
    seen: set[tuple[str, str]] = set()
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or _CONTEXT.search(line) is None:
            continue
        years: list[str] = []
        for match in _PAIR.finditer(line):
            year = _canonical_pair(int(match.group("start")), int(match.group("end")))
            if year:
                years.append(year)
        for match in _SHORT.finditer(line):
            start = int(match.group("start"))
            year = _canonical_pair(start, _expand_short_end(start, match.group("end")))
            if year:
                years.append(year)
        for year in dict.fromkeys(years):
            key = (year, line)
            if key in seen:
                continue
            seen.add(key)
            mentions.append(AcademicYearMention(year, line))
    return tuple(mentions)


def unique_academic_years(mentions: tuple[AcademicYearMention, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.year for item in mentions))


def _mentions_from_value(value: str | None) -> tuple[AcademicYearMention, ...]:
    year = normalize_academic_year(value)
    if year is None:
        return ()
    return (AcademicYearMention(year, value or year),)


def mentions_from_utp(utp: UtpParseResult | None) -> tuple[AcademicYearMention, ...]:
    if utp is None:
        return ()
    stored = utp.metadata.academic_year_mentions
    if stored:
        return stored
    return _mentions_from_value(utp.metadata.academic_year)


def mentions_from_program(program: ProgramData | None) -> tuple[AcademicYearMention, ...]:
    if program is None:
        return ()
    stored = program.academic_year_mentions
    if stored:
        return stored
    return _mentions_from_value(program.academic_year)


def resolve_academic_year(
    utp_mentions: tuple[AcademicYearMention, ...] = (),
    program_mentions: tuple[AcademicYearMention, ...] = (),
) -> AcademicYearResolution:
    """Выбрать учебный год по документам: авто / один источник / конфликт / нет."""

    utp_years = unique_academic_years(utp_mentions)
    program_years = unique_academic_years(program_mentions)
    sources: list[AcademicYearSource] = []
    for year in utp_years:
        snippet = next(item.snippet for item in utp_mentions if item.year == year)
        sources.append(AcademicYearSource(year, "УТП", snippet))
    for year in program_years:
        snippet = next(item.snippet for item in program_mentions if item.year == year)
        sources.append(AcademicYearSource(year, "программа", snippet))

    years = tuple(dict.fromkeys((*utp_years, *program_years)))
    if not years:
        return AcademicYearResolution(
            AcademicYearStatus.MISSING,
            None,
            (),
            "Учебный год в документах не найден. Укажите его вручную.",
        )
    if len(years) > 1:
        listed = ", ".join(years)
        return AcademicYearResolution(
            AcademicYearStatus.CONFLICT,
            None,
            tuple(sources),
            f"В документах указаны разные учебные годы: {listed}. Укажите год вручную.",
        )
    year = years[0]
    if utp_years and program_years:
        return AcademicYearResolution(
            AcademicYearStatus.AUTO,
            year,
            tuple(sources),
            f"Учебный год {year} указан в программе и УТП.",
        )
    source = sources[0]
    return AcademicYearResolution(
        AcademicYearStatus.SINGLE,
        year,
        tuple(sources),
        f"Учебный год {year} указан в документе «{source.origin}»: «{source.snippet}».",
    )


def resolve_academic_year_from_documents(
    utp: UtpParseResult | None,
    program: ProgramData | None,
) -> AcademicYearResolution:
    return resolve_academic_year(mentions_from_utp(utp), mentions_from_program(program))


def missing_local_exceptions_warning(academic_year: str) -> str:
    return (
        f"Для учебного года {academic_year} каникулы и праздники не заданы. "
        "Использована базовая сетка из 36 недель без переноса занятий."
    )
