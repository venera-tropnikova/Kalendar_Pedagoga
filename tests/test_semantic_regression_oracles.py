"""Pinned final-gate regression oracles for the KEY and TOUR corpora."""

from pathlib import Path

from calendar_pedagoga.content_engine_v2 import (
    build_lesson_content_v2,
    unresolved_mandatory_review_blocks,
)
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import infer_study_year_number, parse_program
from calendar_pedagoga.resolve_utp import apply_workload_from_document
from calendar_pedagoga.scheduling import build_schedule


REFERENCES = Path(__file__).resolve().parents[1] / "references"


def _final_semantic_blocks(
    *,
    utp_filename: str,
    program_filename: str,
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    utp_path = REFERENCES / utp_filename
    program_path = REFERENCES / program_filename
    utp = apply_workload_from_document(parse_utp(utp_path))
    study_year = infer_study_year_number(utp.metadata.study_year)
    assert study_year is not None
    program = parse_program(
        program_path.read_bytes(),
        program_path.name,
        study_year=study_year,
    )
    schedule = build_schedule(utp, "2026–2027")
    content = build_content_model(schedule, utp, program, utp_path.name)
    rows = build_lesson_content_v2(content)
    return unresolved_mandatory_review_blocks(rows)


def test_tour_final_semantic_block_oracle() -> None:
    blocks = _final_semantic_blocks(
        utp_filename="УТП ТП 3г. 2ч.docx",
        program_filename="Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx",
    )

    assert [week for week, _clauses in blocks] == [9, 16, 18, 26, 30]
    assert len(blocks) == 5
    assert sum(len(clauses) for _week, clauses in blocks) == 16


def test_key_final_semantic_block_oracle() -> None:
    blocks = _final_semantic_blocks(
        utp_filename="УТП КЛЮЧ 2 г. 2ч.docx",
        program_filename="Программа КЛЮЧ.DOC",
    )

    assert [week for week, _clauses in blocks] == [
        2,
        3,
        6,
        7,
        8,
        9,
        10,
        11,
        14,
        15,
        32,
    ]
    assert len(blocks) == 11
    assert sum(len(clauses) for _week, clauses in blocks) == 34
