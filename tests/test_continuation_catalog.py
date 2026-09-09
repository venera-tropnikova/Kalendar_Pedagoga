"""Continuation rows share a catalog instead of copying the whole list."""

from pathlib import Path
import re

from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2, derive_fields_v2
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.lesson_content import _split_explicit_practice
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.practice_slots import format_slot_practice_text, split_catalog_across_weeks
from calendar_pedagoga.program_parsing import infer_study_year_number, parse_program
from calendar_pedagoga.resolve_utp import apply_workload_from_document
from calendar_pedagoga.scheduling import build_schedule


REFERENCES = Path(__file__).resolve().parents[1] / "references"


def _slot_fields(practice: str, *, index: int, weeks: int):
    return derive_fields_v2(
        topic_title="Тема занятия",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
        occurrence_index=index,
        practice_appearance_count=weeks,
    )


def _key_year1_city_practice() -> str:
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    program = parse_program(program_path.read_bytes(), program_path.name, study_year=1)
    item = next(
        row
        for row in program.content_items
        if (row.number == "2.4") or row.title.casefold().startswith("мой город")
    )
    explicit = _split_explicit_practice(item.content)
    assert explicit is not None
    return explicit[1]


def test_key_city_excursion_w13_w15_distributes_place_list() -> None:
    """KEY year-1 2.4 catalog on three continuation rows (W13–W15 example)."""

    practice = _key_year1_city_practice()
    assert "Юлаева" in practice
    parts = split_catalog_across_weeks(practice, 3)
    assert parts is not None
    rows = [_slot_fields(practice, index=index, weeks=3) for index in range(3)]
    texts = [
        format_slot_practice_text((part,), continuation=index > 0)
        for index, part in enumerate(parts)
    ]
    assert not texts[0].startswith("Продолжение.")
    assert all(text.startswith("Продолжение.") for text in texts[1:])
    distinctive = (
        "Строителей",
        "Первомайской",
        "Северной",
        "Уфимской",
        "Губкина",
        "Ленинградской",
        "Первостроителей",
        "пожарную часть",
        "бульвару",
        "площадь Ленина",
    )
    joined = " ".join(texts)
    for token in distinctive:
        pattern = rf"(?<![А-Яа-яЁё]){re.escape(token)}(?![А-Яа-яЁё])"
        assert len(re.findall(pattern, joined, flags=re.IGNORECASE)) == 1, token
    assert len(re.findall(r"(?i)юлаева", joined)) == 2
    full = re.sub(r"\s+", " ", practice).casefold()
    assert not any(full in text.casefold() for text in texts[1:])
    results = [row.planned_result for row in rows]
    assert results[0] != results[1]
    assert results[1] != results[2]
    assert all(row.practice_text == practice for row in rows)

    def tokens_of(blob: str) -> set[str]:
        low = blob.casefold()
        return {token for token in distinctive if token.casefold() in low}

    for text, result, control in zip(texts, results, [row.assessment_method for row in rows], strict=True):
        own = tokens_of(text)
        others = set()
        for other in texts:
            if other is not text:
                others |= tokens_of(other)
        leaked = (others - own)
        assert not (leaked & tokens_of(result)), (leaked, result)
        assert not (leaked & tokens_of(control)), (leaked, control)
        assert "экскурси" in result.casefold()
        assert "наблюден" in control.casefold()


def test_synthetic_workshop_list_is_distributed_without_key_names() -> None:
    practice = (
        "Экскурсии в ботанический сад, к обсерватории, "
        "по улицам Садовой, Полевой, Речной, в планетарий, "
        "к мельнице, в краеведческий музей."
    )
    parts = split_catalog_across_weeks(practice, 3)
    assert parts is not None
    tokens = (
        "ботанический сад",
        "обсерватории",
        "Садовой",
        "Полевой",
        "Речной",
        "планетарий",
        "мельнице",
        "краеведческий музей",
    )
    joined = " ".join(parts).casefold()
    for token in tokens:
        assert joined.count(token.casefold()) == 1
    assert "Юлаева" not in joined
    rows = [_slot_fields(practice, index=index, weeks=3) for index in range(3)]
    assert rows[0].planned_result != rows[1].planned_result
    assert not rows[0].planned_result.casefold().startswith("продолжение")
    assert all("экскурси" in row.lesson_type.casefold() for row in rows)
    assert all(row.assessment_method for row in rows)


def test_key_year2_trip_catalog_is_split_across_practice_weeks() -> None:
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    utp = apply_workload_from_document(parse_utp(utp_path))
    program = parse_program(
        program_path.read_bytes(),
        program_path.name,
        study_year=infer_study_year_number(utp.metadata.study_year) or 2,
    )
    generated = build_lesson_content_v2(
        build_content_model(build_schedule(utp, "2026–2027"), utp, program, utp_path.name)
    )
    trips = [
        lesson
        for lesson in generated
        if lesson.source.topic_title == "Экскурсионные поездки"
        and lesson.source.practice_hours
    ]
    assert len(trips) == 4
    results = [lesson.planned_result for lesson in trips]
    assert len(set(results)) > 1
    joined = " ".join(results).casefold()
    for token in (
        "стерлитамакские шиханы",
        "нугушское водохранилище",
        "хазинское урочище",
        "капова пещера",
        "кук-караук",
    ):
        assert token in joined
    for lesson in trips:
        assert lesson.source.practice_hours == 2
        assert lesson.lesson_type == "экскурсия"


def test_synthetic_colon_object_catalog_is_distributed_without_robotics() -> None:
    practice = (
        "Изготовление учебных макетов: макет-мельница, макет-мост, "
        "макет-башня, макет-корабль, макет-самолёт."
    )
    tokens = (
        "макет-мельница",
        "макет-мост",
        "макет-башня",
        "макет-корабль",
        "макет-самолёт",
    )
    parts = split_catalog_across_weeks(practice, 5)
    assert parts is not None
    assert len(parts) == 5
    joined_parts = " ".join(parts)
    assert [joined_parts.casefold().find(token.casefold()) for token in tokens] == sorted(
        joined_parts.casefold().find(token.casefold()) for token in tokens
    )
    for token in tokens:
        assert joined_parts.casefold().count(token.casefold()) == 1
    assert "робот" not in joined_parts.casefold()
    rows = [_slot_fields(practice, index=index, weeks=5) for index in range(5)]
    results = [row.planned_result for row in rows]
    controls = [row.assessment_method for row in rows]
    assert len(set(results)) == 5
    for part, result, control in zip(parts, results, controls, strict=True):
        own = {token for token in tokens if token.casefold() in part.casefold()}
        others = set(tokens) - own
        leaked = {token for token in others if token.casefold() in result.casefold()}
        assert not leaked, (leaked, result)
        leaked_c = {token for token in others if token.casefold() in control.casefold()}
        assert not leaked_c, (leaked_c, control)
        assert own
        assert any(token.casefold() in result.casefold() for token in own)


def test_comma_activity_list_without_colon_is_not_a_catalog() -> None:
    practice = (
        "Составление программ движения по линии, остановки перед препятствием, "
        "сортировки цветных кубиков."
    )
    assert split_catalog_across_weeks(practice, 3) is None
