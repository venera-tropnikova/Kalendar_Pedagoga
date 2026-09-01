from pathlib import Path
from functools import lru_cache

from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.lesson_content import build_lesson_content, calculate_fill_metrics
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import parse_program
from calendar_pedagoga.scheduling import build_schedule


REFERENCES = Path(__file__).resolve().parents[1] / "references"


@lru_cache(maxsize=1)
def _key_lessons():
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    utp = parse_utp(utp_path)
    program = parse_program(program_path.read_bytes(), program_path.name)
    content = build_content_model(build_schedule(utp), utp, program, utp_path.name)
    return build_lesson_content(content)


def test_key_lesson_content_uses_only_program_fragments() -> None:
    rows = _key_lessons()
    assert len(rows) == 36
    for row in rows:
        source = row.source.program_content_full
        assert not row.theory_text or row.theory_text in source
        assert not row.practice_text or row.practice_text in source
        assert not row.theory_text or row.theory_text != row.practice_text
        assert (row.source.theory_hours != 0) or row.theory_text == ""
        assert (row.source.practice_hours != 0) or row.practice_text == ""
        assert row.lesson_type == row.planned_result == row.assessment_method == ""


def test_explicit_practice_marker_splits_city_without_rewriting() -> None:
    rows = _key_lessons()
    city = rows[2:5]
    assert city[0].theory_text.startswith("Время основания города")
    assert city[0].practice_text == ""
    assert city[1].theory_text == ""
    assert city[1].practice_text.startswith("Экскурсии по улицам города")
    assert city[2].practice_text == city[1].practice_text


def test_mixed_topic_without_explicit_split_stays_empty_with_warning() -> None:
    rows = _key_lessons()
    final_rows = rows[31:]
    assert all(not row.theory_text and not row.practice_text for row in final_rows)
    assert all(any("нет явной границы" in warning for warning in row.warnings) for row in final_rows)


def test_tour_guides_without_program_generates_no_fields() -> None:
    utp_path = REFERENCES / "УТП ТП 3г. 2ч.docx"
    utp = parse_utp(utp_path)
    content = build_content_model(build_schedule(utp), utp, None, utp_path.name)
    rows = build_lesson_content(content)

    assert len(rows) == 36
    assert all(
        not any((row.theory_text, row.practice_text, row.lesson_type, row.planned_result, row.assessment_method))
        for row in rows
    )
    assert calculate_fill_metrics(rows).overall_percent == 0
