from pathlib import Path

from calendar_pedagoga.parsing import Hours, UtpMetadata, UtpParseResult, parse_utp
from calendar_pedagoga.validation import validate_utp


REFERENCES = Path(__file__).resolve().parents[1] / "references"


def test_parse_key_utp() -> None:
    result = parse_utp(REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx")
    assert result.metadata.program_name == "КЛЮЧ"
    assert result.metadata.academic_year == "2026–2027"
    assert result.metadata.study_year == "второй"
    assert result.metadata.student_age == "11 -12 лет"
    assert result.metadata.hours_per_week == 2
    assert result.metadata.hours_per_year == 72
    assert result.metadata.study_weeks == 36
    assert result.metadata.teacher_name == "И.М.Саранцева"
    assert len(result.sections) == 7
    assert [section.number for section in result.sections] == [
        "1", "2", "3", "4", "5", "6", "7"
    ]
    assert [section.is_standalone_position for section in result.sections] == [
        True, False, False, False, True, True, True
    ]
    assert len(result.topics) == 13
    family = next(topic for topic in result.topics if topic.title == "Моя семья")
    assert family.number is None
    assert family.parent_section == "Краеведение"
    assert result.table_totals == Hours(72, 22, 50)
    assert result.warnings == ()


def test_parse_tour_guides_utp_and_report_conflict() -> None:
    result = parse_utp(REFERENCES / "УТП ТП 3г. 2ч.docx")
    assert result.metadata.program_name == "Туристы проводники"
    assert result.metadata.academic_year == "2026–2027"
    assert result.metadata.study_year == "третий"
    assert result.metadata.student_age == "15-16 лет"
    assert result.metadata.hours_per_week == 2
    assert result.metadata.hours_per_year == 140
    assert result.metadata.study_weeks == 36
    assert result.metadata.stated_schedule_hours == 72
    assert result.metadata.teacher_name == "И.М.Саранцева"
    assert len(result.sections) == 5
    assert len(result.topics) == 22
    assert result.table_totals == Hours(72, 24, 48)
    assert result.topics[-2].hours == Hours(10, 0, 10)
    assert result.topics[-1].hours == Hours(10, 0, 10)
    assert len(result.warnings) == 1
    assert "информационная справка: 140 ч." in result.warnings[0]
    assert "36 недель × 2 часа: 72 ч." in result.warnings[0]
    assert "итоговая строка УТП: 72 ч." in result.warnings[0]


def test_validation_reports_arithmetic_and_table_mismatch() -> None:
    result = UtpParseResult(
        metadata=UtpMetadata(hours_per_week=2, hours_per_year=72, study_weeks=36),
        sections=(),
        topics=(),
        table_totals=Hours(72, 20, 40),
    )
    warnings = validate_utp(result)
    assert any("теория + практика" in warning for warning in warnings)
    assert any("Сумма тем" in warning for warning in warnings)
