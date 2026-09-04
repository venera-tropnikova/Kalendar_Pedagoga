from datetime import date
from pathlib import Path

from calendar_pedagoga.academic_year import (
    AcademicYearMention,
    AcademicYearStatus,
    default_academic_year_start,
    extract_academic_year_mentions,
    format_academic_year,
    normalize_academic_year,
    resolve_academic_year,
    resolve_academic_year_from_documents,
)
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import parse_program
from calendar_pedagoga.resolve_utp import resolve_utp
from calendar_pedagoga.scheduling import build_academic_weeks, build_schedule
from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload


REFERENCES = Path(__file__).resolve().parents[1] / "references"

_APPROVED_2026_2027 = (
    (date(2026, 9, 1), date(2026, 9, 6)),
    (date(2026, 9, 7), date(2026, 9, 13)),
    (date(2026, 9, 14), date(2026, 9, 20)),
    (date(2026, 9, 21), date(2026, 9, 27)),
    (date(2026, 9, 28), date(2026, 10, 4)),
    (date(2026, 10, 5), date(2026, 10, 11)),
    (date(2026, 10, 12), date(2026, 10, 18)),
    (date(2026, 10, 19), date(2026, 10, 25)),
    (date(2026, 10, 26), date(2026, 11, 1)),
    (date(2026, 11, 2), date(2026, 11, 8)),
    (date(2026, 11, 9), date(2026, 11, 15)),
    (date(2026, 11, 16), date(2026, 11, 22)),
    (date(2026, 11, 23), date(2026, 11, 29)),
    (date(2026, 11, 30), date(2026, 12, 6)),
    (date(2026, 12, 7), date(2026, 12, 13)),
    (date(2026, 12, 14), date(2026, 12, 20)),
    (date(2026, 12, 21), date(2026, 12, 27)),
    (date(2026, 12, 28), date(2026, 12, 30)),
    (date(2027, 1, 11), date(2027, 1, 17)),
    (date(2027, 1, 18), date(2027, 1, 24)),
    (date(2027, 1, 25), date(2027, 1, 31)),
    (date(2027, 2, 1), date(2027, 2, 7)),
    (date(2027, 2, 8), date(2027, 2, 14)),
    (date(2027, 2, 15), date(2027, 2, 21)),
    (date(2027, 2, 22), date(2027, 2, 28)),
    (date(2027, 3, 1), date(2027, 3, 7)),
    (date(2027, 3, 8), date(2027, 3, 14)),
    (date(2027, 3, 15), date(2027, 3, 21)),
    (date(2027, 3, 22), date(2027, 3, 28)),
    (date(2027, 3, 29), date(2027, 4, 4)),
    (date(2027, 4, 5), date(2027, 4, 11)),
    (date(2027, 4, 12), date(2027, 4, 18)),
    (date(2027, 4, 19), date(2027, 4, 25)),
    (date(2027, 4, 26), date(2027, 5, 2)),
    (date(2027, 5, 3), date(2027, 5, 9)),
    (date(2027, 5, 10), date(2027, 5, 16)),
)


def test_format_and_normalize_canonical_pairs() -> None:
    assert format_academic_year(2026) == "2026–2027"
    assert normalize_academic_year("2026-2027") == "2026–2027"
    assert normalize_academic_year("2026–2027") == "2026–2027"
    assert normalize_academic_year("2026/27") == "2026–2027"
    assert normalize_academic_year("план на 2026 - 2027 учебный") == "2026–2027"
    assert normalize_academic_year("2026–2028") is None
    assert normalize_academic_year("2026") is None


def test_extract_requires_academic_year_context() -> None:
    hyphen = extract_academic_year_mentions("план на 2026-2027 учебный год")
    dash = extract_academic_year_mentions("план на 2026–2027 учебный год")
    slash = extract_academic_year_mentions("в 2026/27 учебном году")
    assert [item.year for item in hyphen] == ["2026–2027"]
    assert [item.year for item in dash] == ["2026–2027"]
    assert [item.year for item in slash] == ["2026–2027"]
    assert extract_academic_year_mentions("срок реализации 2001–2005") == ()
    assert extract_academic_year_mentions("возраст 2011-2012 лет") == ()


def test_resolve_auto_single_conflict_and_missing() -> None:
    same = AcademicYearMention("2026–2027", "план на 2026-2027 учебный год")
    other = AcademicYearMention("2027–2028", "в 2027/28 учебном году")
    auto = resolve_academic_year((same,), (same,))
    assert auto.status is AcademicYearStatus.AUTO
    assert auto.suggested == "2026–2027"
    single = resolve_academic_year((same,), ())
    assert single.status is AcademicYearStatus.SINGLE
    assert "УТП" in single.message
    conflict = resolve_academic_year((same,), (other,))
    assert conflict.status is AcademicYearStatus.CONFLICT
    assert conflict.suggested is None
    missing = resolve_academic_year()
    assert missing.status is AcademicYearStatus.MISSING
    assert missing.suggested is None


def test_control_utp_and_programs_academic_year() -> None:
    key_utp = parse_utp(REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx")
    tp_utp = parse_utp(REFERENCES / "УТП ТП 3г. 2ч.docx")
    assert key_utp.metadata.academic_year == "2026–2027"
    assert tp_utp.metadata.academic_year == "2026–2027"
    assert any("2026/27" in item.snippet for item in key_utp.metadata.academic_year_mentions)

    tp_path = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    tp_program = parse_program(tp_path.read_bytes(), tp_path.name, study_year=1)
    assert tp_program.academic_year is None
    upload = validate_upload(UploadPurpose.PROGRAM, tp_path.name, tp_path.read_bytes())
    embedded = resolve_utp(None, upload)
    assert resolve_academic_year_from_documents(embedded, tp_program).status is (
        AcademicYearStatus.MISSING
    )

    key_program = parse_program(
        (REFERENCES / "Программа КЛЮЧ.DOC").read_bytes(),
        "Программа КЛЮЧ.DOC",
        study_year=2,
    )
    assert key_program.academic_year is None
    single = resolve_academic_year_from_documents(key_utp, key_program)
    assert single.status is AcademicYearStatus.SINGLE
    assert single.suggested == "2026–2027"


def test_approved_2026_2027_grid_is_bit_exact() -> None:
    weeks = build_academic_weeks("2026–2027", 36)
    assert tuple((week.start, week.end) for week in weeks) == _APPROVED_2026_2027
    assert all(week.academic_year == "2026–2027" for week in weeks)
    assert weeks[17].start == date(2026, 12, 28)
    assert weeks[17].end == date(2026, 12, 30)
    assert weeks[18].start == date(2027, 1, 11)


def test_other_year_builds_36_weeks_without_copied_winter_gap() -> None:
    weeks = build_academic_weeks("2027–2028", 36)
    assert len(weeks) == 36
    assert weeks[0].start == date(2027, 9, 1)
    assert weeks[0].end == date(2027, 9, 5)
    assert all(week.academic_year == "2027–2028" for week in weeks)
    ranges = tuple((week.start, week.end) for week in weeks)
    assert (date(2026, 12, 28), date(2026, 12, 30)) not in ranges
    assert date(2027, 1, 11) not in {week.start for week in weeks}
    winter = next(week for week in weeks if week.start <= date(2027, 12, 31) <= week.end)
    assert (winter.end - winter.start).days >= 6
    utp = parse_utp(REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx")
    schedule = build_schedule(utp, "2027–2028")
    assert len(schedule.weeks) == 36
    assert schedule.warnings
    assert "каникулы и праздники не заданы" in schedule.warnings[0]


def test_default_start_uses_september_boundary() -> None:
    assert default_academic_year_start(date(2026, 9, 1)) == 2026
    assert default_academic_year_start(date(2026, 8, 31)) == 2025
