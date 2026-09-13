from io import BytesIO
from pathlib import Path

from docx import Document
import pytest

from calendar_pedagoga.parsing import Hours, Topic, UtpMetadata, UtpParseResult
from calendar_pedagoga.resolve_utp import (
    AUTO_WORKLOAD_WARNING,
    UtpResolutionError,
    apply_workload_from_separate,
    resolve_utp,
)
from calendar_pedagoga.scheduling import build_schedule
from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload


REFERENCES = Path(__file__).resolve().parents[1] / "references"


def _program_without_utp() -> bytes:
    document = Document()
    document.add_paragraph("Дополнительная программа «ТЕСТ»")
    document.add_paragraph("Цель программы: проверка извлечения УТП.")
    document.add_paragraph("Задачи программы:")
    document.add_paragraph("научиться находить таблицу часов.")
    heading = document.add_paragraph()
    heading.add_run("Содержание программы 2-го года обучения").bold = True
    topic = document.add_paragraph()
    topic.add_run("1. Введение").bold = True
    document.add_paragraph("Знакомство с программой.")
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def _synthetic_separate(
    *,
    yearly: int,
    weekly: int | None,
    weeks: int | None = 36,
    topic_hours: tuple[Hours, ...] | None = None,
) -> UtpParseResult:
    hours = topic_hours or (
        Hours(yearly // 2, yearly // 4, yearly // 4),
        Hours(yearly - yearly // 2, yearly // 4, yearly // 2 - yearly // 4),
    )
    topics = tuple(
        Topic(
            number=f"1.{index}",
            title=f"Тема {index}",
            hours=item,
            parent_section="Раздел",
        )
        for index, item in enumerate(hours, start=1)
    )
    return UtpParseResult(
        metadata=UtpMetadata(
            hours_per_week=weekly,
            hours_per_year=yearly,
            study_weeks=weeks,
        ),
        sections=(),
        topics=topics,
        table_totals=Hours(
            yearly,
            sum(item.theory for item in hours),
            sum(item.practice for item in hours),
        ),
        warnings=(),
    )


def test_embedded_utp_from_tour_guides_program() -> None:
    program_path = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    program = validate_upload(
        UploadPurpose.PROGRAM, program_path.name, program_path.read_bytes()
    )
    result = resolve_utp(None, program)
    assert result.table_totals == Hours(72, 27, 45)
    assert len(result.topics) >= 20
    assert result.metadata.study_weeks == 36
    assert result.metadata.hours_per_week == 2
    assert result.metadata.workload_provenance == "derived_36x2"
    assert AUTO_WORKLOAD_WARNING in result.warnings
    schedule = build_schedule(result)
    assert len(schedule.weeks) == 36


def test_separate_utp_has_priority_over_embedded_table() -> None:
    key_utp = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    key_program = REFERENCES / "Программа КЛЮЧ.DOC"
    validated_utp = validate_upload(UploadPurpose.UTP, key_utp.name, key_utp.read_bytes())
    validated_program = validate_upload(
        UploadPurpose.PROGRAM, key_program.name, key_program.read_bytes()
    )
    result = resolve_utp(validated_utp, validated_program)
    assert result.table_totals == Hours(72, 22, 50)
    assert len(result.topics) == 13
    assert result.metadata.workload_provenance == "document"
    assert AUTO_WORKLOAD_WARNING not in result.warnings
    assert any("72" in warning and "144" in warning for warning in result.warnings)


def test_key_regression_separate_files() -> None:
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    validated_utp = validate_upload(UploadPurpose.UTP, utp_path.name, utp_path.read_bytes())
    validated_program = validate_upload(
        UploadPurpose.PROGRAM, program_path.name, program_path.read_bytes()
    )
    utp = resolve_utp(validated_utp, validated_program)
    assert len(utp.topics) == 13
    assert utp.table_totals == Hours(72, 22, 50)
    assert utp.metadata.study_weeks == 36
    assert utp.metadata.hours_per_week == 2
    assert len(build_schedule(utp).weeks) == 36


def test_program_without_embedded_or_separate_utp_fails() -> None:
    program = validate_upload(
        UploadPurpose.PROGRAM,
        "program.docx",
        _program_without_utp(),
    )
    with pytest.raises(UtpResolutionError, match="не найден учебно-тематический план"):
        resolve_utp(None, program)


def test_separate_72_does_not_invent_2h_without_explicit_weekly() -> None:
    with pytest.raises(UtpResolutionError, match="недельн"):
        apply_workload_from_separate(_synthetic_separate(yearly=72, weekly=None))


def test_separate_216_does_not_invent_6h_from_yearly_and_grid() -> None:
    with pytest.raises(UtpResolutionError, match="недельн"):
        apply_workload_from_separate(
            _synthetic_separate(yearly=216, weekly=None, weeks=36)
        )


def test_separate_explicit_weekly_preserves_topics_for_common_totals() -> None:
    cases = (
        (72, 2),
        (108, 3),
        (144, 4),
        (216, 6),
        (324, 9),
    )
    for yearly, weekly in cases:
        source = _synthetic_separate(yearly=yearly, weekly=weekly, weeks=None)
        before_topics = source.topics
        result = apply_workload_from_separate(source)
        assert result.metadata.hours_per_week == weekly
        assert result.metadata.study_weeks == 36
        assert result.metadata.hours_per_year == yearly
        assert result.topics == before_topics
        assert result.table_totals == source.table_totals
        assert result.metadata.workload_provenance == "document_weekly_calendar_grid"
        assert AUTO_WORKLOAD_WARNING not in result.warnings


def test_separate_non_divisible_yearly_with_explicit_weekly_blocks() -> None:
    # 80 и 96 не делятся на 36 без дроби: при явных 2/3 ч/нед годовой итог не сходится.
    for yearly, weekly in ((80, 2), (96, 3), (128, 4)):
        with pytest.raises(UtpResolutionError, match="не согласуется"):
            apply_workload_from_separate(
                _synthetic_separate(yearly=yearly, weekly=weekly, weeks=36)
            )


def test_separate_yearly_mismatch_blocks() -> None:
    with pytest.raises(UtpResolutionError, match="не согласуется"):
        apply_workload_from_separate(
            _synthetic_separate(yearly=216, weekly=2, weeks=36)
        )


def test_separate_topic_hours_not_replaced_by_embedded_totals() -> None:
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    validated_utp = validate_upload(UploadPurpose.UTP, utp_path.name, utp_path.read_bytes())
    validated_program = validate_upload(
        UploadPurpose.PROGRAM, program_path.name, program_path.read_bytes()
    )
    before = validated_utp.parsed
    assert isinstance(before, UtpParseResult)
    result = resolve_utp(validated_utp, validated_program)
    assert [(topic.number, topic.title, topic.hours) for topic in result.topics] == [
        (topic.number, topic.title, topic.hours) for topic in before.topics
    ]
    assert result.table_totals == before.table_totals == Hours(72, 22, 50)
