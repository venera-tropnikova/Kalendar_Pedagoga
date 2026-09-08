"""Round-trip: ячейки DOCX против того же production pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re

import pytest

from calendar_pedagoga.content_generation import WeekTopicPart
from calendar_pedagoga.docx_generation import (
    _practice_appearance_counts,
    _topic_cells_for_lesson,
    _topic_display_numbers,
)
from calendar_pedagoga.lesson_content import _clause_units
from calendar_pedagoga.lesson_resolution import ResolvedLessonRow
from calendar_pedagoga.organization_template import select_calendar_template
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.pipeline import PipelineResult, run_calendar_pipeline
from calendar_pedagoga.practice_slots import assign_practice_slots, practice_units_from_content
from calendar_pedagoga.program_parsing import infer_study_year_number, parse_program
from calendar_pedagoga.resolve_utp import resolve_utp
from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload
from docx_roundtrip import ExtractedWeek, extract_logical_weeks


REFERENCES = Path(__file__).resolve().parents[1] / "references"
_TITLE_MIN = 12
_PHRASE_MIN = 20
_HOURS_TAIL_RE = re.compile(r"\s*\(\d+\)\s*$")


@dataclass(frozen=True)
class _Corpus:
    name: str
    utp: object
    result: PipelineResult


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").casefold().strip()


def _cell_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _part_key(part: WeekTopicPart) -> tuple[str | None, str, str]:
    return (part.topic_number, part.topic_title, part.section)


def _week_parts(lesson: ResolvedLessonRow) -> tuple[WeekTopicPart, ...]:
    source = lesson.source.source
    if source.week_parts:
        return source.week_parts
    return (
        WeekTopicPart(
            topic_number=source.topic_number,
            topic_title=source.topic_title,
            section=source.section,
            theory_hours=source.theory_hours,
            practice_hours=source.practice_hours,
            match_status=source.match_status,
            program_section=source.program_section,
            program_topic=source.program_topic,
            program_content_full=source.program_content_full,
            warnings=source.warnings,
        ),
    )


def _own_blob(parts: tuple[WeekTopicPart, ...]) -> str:
    chunks: list[str] = []
    for part in parts:
        chunks.extend(
            (
                part.topic_number or "",
                part.topic_title,
                part.section,
                part.program_topic,
                part.program_content_full,
            )
        )
    return _norm(" ".join(chunks))


def _mismatch(corpus: str, week: int, field: str, expected: str, actual: str) -> str:
    return (
        f"{corpus} неделя {week}: {field}\n"
        f"expected: {expected!r}\n"
        f"actual:   {actual!r}"
    )


@lru_cache(maxsize=2)
def _load_corpus(name: str) -> _Corpus:
    if name == "key":
        utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
        program_path = REFERENCES / "Программа КЛЮЧ.DOC"
        utp = parse_utp(utp_path)
        program = parse_program(program_path.read_bytes(), program_path.name, study_year=2)
        result = run_calendar_pipeline(
            utp,
            program,
            academic_year="2026–2027",
            template=select_calendar_template(),
            source_utp_name=utp_path.name,
            program_filename=program_path.name,
            use_ai=False,
        )
        return _Corpus("КЛЮЧ", utp, result)
    if name == "tour_guides":
        program_path = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
        template_path = REFERENCES / "Календарный план.docx"
        validated = validate_upload(
            UploadPurpose.PROGRAM,
            program_path.name,
            program_path.read_bytes(),
        )
        utp = resolve_utp(None, validated)
        program = parse_program(
            validated.content,
            validated.filename,
            study_year=infer_study_year_number(utp.metadata.study_year),
        )
        template = select_calendar_template(template_path.name, template_path.read_bytes())
        result = run_calendar_pipeline(
            utp,
            program,
            academic_year="2026–2027",
            template=template,
            source_utp_name=program_path.name,
            program_filename=program_path.name,
            use_ai=False,
            use_content_engine_v2=True,
        )
        return _Corpus("Туристы-проводники 1 г.", utp, result)
    raise ValueError(name)


def _paired_weeks(corpus: _Corpus) -> list[tuple[ResolvedLessonRow, ExtractedWeek]]:
    extracted = extract_logical_weeks(corpus.result.content)
    resolved = corpus.result.resolved_lessons
    assert [row.source.source.week_number for row in resolved] == list(range(1, 37))
    assert [row.week_number for row in extracted] == list(range(1, 37)), (
        f"{corpus.name}: DOCX недели {[row.week_number for row in extracted]}"
    )
    return list(zip(resolved, extracted, strict=True))


def _prepared_cells(corpus: _Corpus) -> dict[int, tuple[str, str]]:
    rows = corpus.result.resolved_lessons
    display_numbers = _topic_display_numbers(corpus.utp)
    topic_counts = _practice_appearance_counts(rows)
    topic_occurrences: dict[tuple[str | None, str, str], int] = {}
    prepared: dict[int, tuple[str, str]] = {}
    for lesson in rows:
        week = lesson.source.source.week_number
        prepared[week] = _topic_cells_for_lesson(
            lesson,
            display_numbers,
            topic_counts=topic_counts,
            topic_occurrences=topic_occurrences,
        )
    return prepared


def _foreign_markers(
    foreign: WeekTopicPart,
    own_blob: str,
) -> list[tuple[str, str]]:
    markers: list[tuple[str, str]] = []
    title = _norm(foreign.topic_title).strip(" .")
    if len(title) >= _TITLE_MIN and title not in own_blob:
        markers.append(
            (
                title,
                f"тема {foreign.topic_number} «{foreign.topic_title}»",
            )
        )
    for unit in _clause_units(foreign.program_content_full or ""):
        phrase = _norm(unit)
        if len(phrase) < _PHRASE_MIN or phrase in own_blob:
            continue
        markers.append(
            (
                phrase,
                (
                    f"тема {foreign.topic_number} «{foreign.topic_title}»: "
                    f"{unit}"
                ),
            )
        )
    return markers


def _line_for_part(
    lines: list[str],
    part: WeekTopicPart,
    siblings: tuple[WeekTopicPart, ...],
) -> str | None:
    title = _norm(part.topic_title).strip(" .")
    titled = [
        line
        for line in lines
        if len(title) >= 8 and title in _norm(line)
    ]
    if len(titled) == 1:
        return titled[0]
    others = _norm(
        " ".join(
            f"{item.topic_title} {item.program_content_full}"
            for item in siblings
            if _part_key(item) != _part_key(part)
        )
    )
    distinctive = [
        _norm(unit)
        for unit in _clause_units(part.program_content_full or "")
        if len(_norm(unit)) >= _PHRASE_MIN and _norm(unit) not in others
    ]
    matched: list[str] = []
    for line in lines:
        low = _norm(line)
        if any(marker in low for marker in distinctive):
            matched.append(line)
    if len(matched) == 1:
        return matched[0]
    if len(lines) == 1 and len(siblings) == 1:
        return lines[0]
    if titled:
        return titled[0]
    return None


@pytest.mark.parametrize("corpus_name", ("key", "tour_guides"))
def test_docx_roundtrip_type_result_control_matches_resolved(corpus_name: str) -> None:
    corpus = _load_corpus(corpus_name)
    for lesson, docx in _paired_weeks(corpus):
        week = docx.week_number
        assert docx.lesson_type.strip() == lesson.lesson_type.strip(), _mismatch(
            corpus.name, week, "TYPE", lesson.lesson_type, docx.lesson_type
        )
        assert docx.planned_result.strip() == lesson.planned_result.strip(), _mismatch(
            corpus.name, week, "RESULT", lesson.planned_result, docx.planned_result
        )
        assert docx.assessment.strip() == lesson.assessment_method.strip(), _mismatch(
            corpus.name, week, "CONTROL", lesson.assessment_method, docx.assessment
        )
        assert docx.theory_mark.strip() == "", _mismatch(
            corpus.name, week, "отметка", "", docx.theory_mark
        )
        if docx.lesson_type_mirror is not None:
            assert docx.lesson_type_mirror.strip() == lesson.lesson_type.strip(), _mismatch(
                corpus.name,
                week,
                "TYPE mirror",
                lesson.lesson_type,
                docx.lesson_type_mirror,
            )
        if docx.planned_result_mirror is not None:
            assert (
                docx.planned_result_mirror.strip() == lesson.planned_result.strip()
            ), _mismatch(
                corpus.name,
                week,
                "RESULT mirror",
                lesson.planned_result,
                docx.planned_result_mirror,
            )


def test_docx_roundtrip_org_mirrors_match_primary_fields() -> None:
    corpus = _load_corpus("tour_guides")
    pairs = _paired_weeks(corpus)
    if pairs[0][1].lesson_type_mirror is None:
        pytest.skip(
            "Туристы-проводники: org-шаблон 8 колонок, зеркал TYPE/RESULT нет"
        )
    for lesson, docx in pairs:
        week = docx.week_number
        assert docx.lesson_type_mirror is not None
        assert docx.planned_result_mirror is not None
        assert docx.lesson_type_mirror.strip() == lesson.lesson_type.strip(), _mismatch(
            corpus.name, week, "TYPE mirror", lesson.lesson_type, docx.lesson_type_mirror
        )
        assert (
            docx.planned_result_mirror.strip() == lesson.planned_result.strip()
        ), _mismatch(
            corpus.name,
            week,
            "RESULT mirror",
            lesson.planned_result,
            docx.planned_result_mirror,
        )


@pytest.mark.parametrize("corpus_name", ("key", "tour_guides"))
def test_docx_roundtrip_theory_practice_match_prepared_cells(corpus_name: str) -> None:
    corpus = _load_corpus(corpus_name)
    prepared = _prepared_cells(corpus)
    for lesson, docx in _paired_weeks(corpus):
        week = docx.week_number
        expected_theory, expected_practice = prepared[week]
        assert docx.theory == expected_theory, _mismatch(
            corpus.name, week, "теория (запись)", expected_theory, docx.theory
        )
        assert docx.practice == expected_practice, _mismatch(
            corpus.name, week, "практика (запись)", expected_practice, docx.practice
        )
        assert lesson.source.source.week_number == week


@pytest.mark.parametrize("corpus_name", ("key", "tour_guides"))
def test_docx_roundtrip_week_cells_exclude_foreign_topic_content(corpus_name: str) -> None:
    corpus = _load_corpus(corpus_name)
    catalog: list[tuple[int, WeekTopicPart]] = []
    for lesson in corpus.result.resolved_lessons:
        week = lesson.source.source.week_number
        catalog.extend((week, part) for part in _week_parts(lesson))

    for lesson, docx in _paired_weeks(corpus):
        week = docx.week_number
        parts = _week_parts(lesson)
        own_keys = {_part_key(part) for part in parts}
        own_blob = _own_blob(parts)
        cell = _norm(f"{docx.theory}\n{docx.practice}")
        seen: set[tuple[str | None, str, str]] = set()
        for source_week, foreign in catalog:
            key = _part_key(foreign)
            if key in own_keys or key in seen:
                continue
            seen.add(key)
            for marker, source in _foreign_markers(foreign, own_blob):
                if marker in cell:
                    raise AssertionError(
                        f"{corpus.name} неделя {week}: в ячейке чужой content\n"
                        f"expected: не содержать {marker!r}\n"
                        f"actual theory: {docx.theory!r}\n"
                        f"actual practice: {docx.practice!r}\n"
                        f"источник: неделя {source_week}, {source}"
                    )


@pytest.mark.parametrize("corpus_name", ("key", "tour_guides"))
def test_docx_roundtrip_mixed_weeks_keep_parts_separate(corpus_name: str) -> None:
    corpus = _load_corpus(corpus_name)
    mixed = [
        (lesson, docx)
        for lesson, docx in _paired_weeks(corpus)
        if len(_week_parts(lesson)) > 1
    ]
    if corpus_name == "key":
        if mixed:
            weeks = [docx.week_number for _lesson, docx in mixed]
            raise AssertionError(
                f"КЛЮЧ: expected mixed weeks = 0, actual недели {weeks}"
            )
        pytest.skip("КЛЮЧ: mixed weeks = 0")

    assert len(mixed) == 1, (
        f"{corpus.name}: expected одна mixed week, actual "
        f"{[docx.week_number for _lesson, docx in mixed]}"
    )
    lesson, docx = mixed[0]
    week = docx.week_number
    parts = _week_parts(lesson)
    assert week == 1, _mismatch(corpus.name, week, "mixed week", "1", str(week))
    assert len(parts) == 2, (
        f"{corpus.name} неделя 1: expected две части отдельно, actual "
        f"{[(part.topic_number, part.topic_title) for part in parts]}"
    )

    theory_parts = tuple(part for part in parts if part.theory_hours)
    practice_parts = tuple(part for part in parts if part.practice_hours)
    theory_lines = _cell_lines(docx.theory)
    practice_lines = _cell_lines(docx.practice)
    assert len(theory_lines) == len(theory_parts) == 2, (
        f"{corpus.name} неделя {week}: теория потеряна/склеена/дублирована\n"
        f"expected частей: {[(part.topic_number, part.topic_title) for part in theory_parts]}\n"
        f"actual строк: {theory_lines!r}"
    )
    assert len(practice_lines) == len(practice_parts), (
        f"{corpus.name} неделя {week}: практика потеряна/склеена/дублирована\n"
        f"expected частей: {[part.topic_title for part in practice_parts]}\n"
        f"actual строк: {practice_lines!r}"
    )
    for part, line in zip(theory_parts, theory_lines, strict=True):
        title = _norm(part.topic_title).strip(" .")
        assert title in _norm(line), (
            f"{corpus.name} неделя {week}: строка теории без своей темы\n"
            f"expected title: {part.topic_title!r}\n"
            f"actual: {line!r}"
        )
        for other in theory_parts:
            if _part_key(other) == _part_key(part):
                continue
            other_title = _norm(other.topic_title).strip(" .")
            if len(other_title) < 8 or other_title in title:
                continue
            assert other_title not in _norm(line), (
                f"{corpus.name} неделя {week}: темы смешаны в одной строке теории\n"
                f"expected: только «{part.topic_title}»\n"
                f"actual: {line!r}\n"
                f"источник чужого: «{other.topic_title}»"
            )
    used_practice: set[int] = set()
    for part in practice_parts:
        line = _line_for_part(practice_lines, part, practice_parts)
        assert line is not None, (
            f"{corpus.name} неделя {week}: практика части не найдена\n"
            f"expected topic: {part.topic_title!r}\n"
            f"actual: {practice_lines!r}"
        )
        line_index = practice_lines.index(line)
        assert line_index not in used_practice, (
            f"{corpus.name} неделя {week}: две части попали в одну строку практики\n"
            f"actual: {line!r}"
        )
        used_practice.add(line_index)
        for other in practice_parts:
            if _part_key(other) == _part_key(part):
                continue
            other_title = _norm(other.topic_title).strip(" .")
            this_title = _norm(part.topic_title)
            if len(other_title) >= 8 and other_title not in this_title:
                if other_title in _norm(line) and this_title not in other_title:
                    raise AssertionError(
                        f"{corpus.name} неделя {week}: темы смешаны в практике\n"
                        f"expected: «{part.topic_title}»\n"
                        f"actual: {line!r}\n"
                        f"источник чужого: «{other.topic_title}»"
                    )
            own = _norm(f"{part.topic_title} {part.program_content_full}")
            for unit in _clause_units(other.program_content_full or ""):
                phrase = _norm(unit)
                if len(phrase) < _PHRASE_MIN or phrase in own:
                    continue
                assert phrase not in _norm(line), (
                    f"{corpus.name} неделя {week}: content чужой части в практике\n"
                    f"expected: не содержать {phrase!r}\n"
                    f"actual: {line!r}\n"
                    f"источник: «{other.topic_title}»: {unit}"
                )


@pytest.mark.parametrize("corpus_name", ("key", "tour_guides"))
def test_docx_roundtrip_repeated_slots_exclude_other_slot_content(corpus_name: str) -> None:
    corpus = _load_corpus(corpus_name)
    appearances: dict[tuple[str | None, str, str], list[tuple[int, WeekTopicPart, str]]] = {}
    for lesson, docx in _paired_weeks(corpus):
        week = docx.week_number
        parts = _week_parts(lesson)
        practice_parts = tuple(part for part in parts if part.practice_hours)
        lines = _cell_lines(docx.practice)
        for part in practice_parts:
            line = _line_for_part(lines, part, practice_parts)
            if line is None and len(lines) == 1:
                line = lines[0]
            appearances.setdefault(_part_key(part), []).append(
                (week, part, line or "")
            )

    repeated = False
    for key, rows in appearances.items():
        if len(rows) < 2:
            continue
        repeated = True
        sample = rows[0][1]
        units = practice_units_from_content(
            sample.program_content_full,
            theory_hours=sample.theory_hours,
            practice_hours=sample.practice_hours,
        )
        if len(units) < 2:
            continue
        slots = assign_practice_slots(units, len(rows))
        for index, (week, part, line) in enumerate(rows):
            own = {_norm(item) for item in slots[index] if _norm(item)}
            foreign_units: list[tuple[int, str]] = []
            for other_index, slot in enumerate(slots):
                if other_index == index:
                    continue
                for item in slot:
                    phrase = _norm(item)
                    if len(phrase) < _PHRASE_MIN or phrase in own:
                        continue
                    if any(phrase in owned or owned in phrase for owned in own):
                        continue
                    foreign_units.append((other_index + 1, item))
            cell = _norm(_HOURS_TAIL_RE.sub("", line))
            for slot_number, item in foreign_units:
                phrase = _norm(item)
                if phrase in cell:
                    raise AssertionError(
                        f"{corpus.name} неделя {week}: слот получил content другого слота\n"
                        f"тема: {key[0]} «{key[1]}»\n"
                        f"expected: не содержать {phrase!r}\n"
                        f"actual: {line!r}\n"
                        f"источник: слот {slot_number}: {item}"
                    )
    assert repeated, f"{corpus.name}: в корпусе нет repeated practice topics"
