from pathlib import Path
from functools import lru_cache

from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import parse_program
from calendar_pedagoga.scheduling import build_schedule


REFERENCES = Path(__file__).resolve().parents[1] / "references"


@lru_cache(maxsize=1)
def _key_rows():
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    utp = parse_utp(utp_path)
    program = parse_program(program_path.read_bytes(), program_path.name)
    return build_content_model(build_schedule(utp), utp, program, utp_path.name)


def test_key_content_model_has_36_complete_sourced_rows() -> None:
    rows = _key_rows()

    assert len(rows) == 36
    assert all(row.topic_title and row.source_topic_title for row in rows)
    assert all(row.source_utp_name == "УТП КЛЮЧ 2 г. 2ч.docx" for row in rows)
    assert all(row.program_content_full for row in rows)
    assert all(row.match_status is not MatchStatus.NOT_MATCHED for row in rows)
    assert not any("та же тема" in row.topic_title.lower() for row in rows)
    assert sum(row.total_hours for row in rows) == 72
    assert sum(row.theory_hours for row in rows) == 22
    assert sum(row.practice_hours for row in rows) == 50


def test_key_multweek_topics_keep_exact_source_links() -> None:
    rows = _key_rows()
    city = rows[2:5]
    assert [row.week_number for row in city] == [3, 4, 5]
    assert all(row.topic_title == "Мой город" for row in city)
    assert len({row.program_content_full for row in city}) == 1
    pharmacy = next(row for row in rows if row.topic_title == "Аптечка")
    assert pharmacy.program_topic == "Медицинская аптечка."
    assert pharmacy.match_status is MatchStatus.TEXT_MATCH
    assert all(
        row.topic_title == "Туристско-краеведческие праздники, соревнования."
        for row in rows[31:]
    )


def test_tour_guides_without_program_has_empty_content_and_warning() -> None:
    utp_path = REFERENCES / "УТП ТП 3г. 2ч.docx"
    utp = parse_utp(utp_path)
    rows = build_content_model(build_schedule(utp), utp, None, utp_path.name)

    assert len(rows) == 36
    assert all(row.topic_title for row in rows)
    assert all(row.program_content_full == "" for row in rows)
    assert all(row.match_status is MatchStatus.NOT_MATCHED for row in rows)
    assert all(row.warnings for row in rows)
    assert sum(row.total_hours for row in rows) == 72
