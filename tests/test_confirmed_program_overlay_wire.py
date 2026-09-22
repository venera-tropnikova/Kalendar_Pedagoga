from __future__ import annotations

import json
from pathlib import Path

import pytest

from calendar_pedagoga.academic_year import APPROVED_ACADEMIC_YEAR
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.generation_contract import (
    GenerationContractError,
    decode_match_reviews,
    decode_program_overlay,
    encode_match_reviews,
    encode_program_overlay,
)
from calendar_pedagoga.generation_service import _decode_generation_payload
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.organization_template import select_calendar_template
from calendar_pedagoga.pipeline import CalendarDocumentStatus, run_calendar_pipeline
from calendar_pedagoga.production_readiness import GENERIC_ONLY
from calendar_pedagoga.program_parsing import ProgramContentItem, parse_program
from calendar_pedagoga.program_structure_confirmation import (
    CONTENT_ORIGIN_MANUAL,
    confirm_program_structure,
    draft_source_ledger,
    draft_structure_rows,
    embedded_utp_candidates,
    overlay_confirmed_program,
    overlay_unresolved_topic_edits,
    schedule_topics_from_candidate,
    select_embedded_utp,
    unresolved_schedule_rows,
)
from calendar_pedagoga.remote_generation import build_generation_payload
from calendar_pedagoga.scheduling import build_schedule
from tests.docx_roundtrip import extract_logical_weeks
from tests.test_remote_generation import PROGRAM_PATH, REVISION, UTP_PATH, _plan, _template


REFERENCES = Path(__file__).resolve().parents[1] / "references"
EXTRA_REFERENCES = Path(r"D:\Kalendar_Pedagoga\references")


def _corpus(*needles: str) -> Path | None:
    lowered = tuple(needle.casefold() for needle in needles)
    for root in (REFERENCES, EXTRA_REFERENCES):
        if not root.exists():
            continue
        matches = [
            path
            for path in root.iterdir()
            if all(needle in path.name.casefold() for needle in lowered)
        ]
        if matches:
            return matches[0]
    return None


def _item_tuple(item: ProgramContentItem) -> tuple:
    return (
        item.number,
        item.title,
        item.content,
        item.parent_section,
        item.study_year,
    )


def _holdout_confirmation():
    holdout = _corpus("природн", "материал")
    if holdout is None:
        pytest.skip("Нет holdout «Работа с природным материалом»")
    payload = holdout.read_bytes()
    program = parse_program(payload, holdout.name, study_year=1)
    embedded = embedded_utp_candidates(payload, holdout.name)
    selected = select_embedded_utp(embedded, 1)
    assert selected is not None
    topics = schedule_topics_from_candidate(selected)
    draft = draft_structure_rows(program=program, embedded=embedded, study_year=1)
    ledger = draft_source_ledger(program, study_year=1, topics=topics)
    edited = []
    for row in unresolved_schedule_rows(draft):
        title = row["topic"]
        edited.append(
            {
                **row,
                "content_origin": CONTENT_ORIGIN_MANUAL,
                "theory_content": title if row["theory_hours"] not in {"", "0", "0.0"} else "",
                "practice_content": title if row["practice_hours"] not in {"", "0", "0.0"} else "",
                "source_item_id": "",
                "excerpt": "",
            }
        )
    confirmation = confirm_program_structure(
        rows=overlay_unresolved_topic_edits(draft, edited),
        study_year=1,
        study_weeks=32,
        hours_per_week="3",
        scope="overlay-wire",
        source_items=program.content_items,
        ledger=ledger,
        selected_utp=selected,
        embedded=embedded,
    )
    overlaid = overlay_confirmed_program(program, confirmation.program_items)
    return payload, holdout.name, program, confirmation, overlaid


def _roundtrip_payload(payload: dict) -> dict:
    return json.loads(json.dumps(payload, ensure_ascii=False))


def test_overlay_items_round_trip() -> None:
    items = (
        ProgramContentItem(
            number=None,
            title="Лаборатория керамики",
            content="Состав шликера.",
            parent_section="Лаборатория керамики",
            study_year=1,
        ),
        ProgramContentItem(
            number="8",
            title="Картография болот",
            content="Картография болот.",
            parent_section="Картография болот",
            study_year=1,
        ),
    )
    encoded = encode_program_overlay(items)
    assert isinstance(encoded["items"], list)
    decoded = decode_program_overlay(_roundtrip_payload(encoded))
    assert tuple(_item_tuple(item) for item in decoded) == tuple(
        _item_tuple(item) for item in items
    )


def test_match_reviews_restore_tuple_composite_keys() -> None:
    key = ("3", "Скульптурная композиция. Объёмные изделия.", "Скульптурная композиция. Объёмные изделия.")
    reviews = {
        key: {
            "decision": "USER_CONFIRMED",
            "item_ref": {
                "number": None,
                "title": key[1],
                "section": key[2],
                "study_year": 1,
            },
        }
    }
    encoded = encode_match_reviews(reviews)
    assert isinstance(encoded, list)
    assert encoded[0]["topic_number"] == "3"
    assert encoded[0]["topic_title"] == key[1]
    assert encoded[0]["parent_section"] == key[2]
    restored = decode_match_reviews(_roundtrip_payload(encoded))
    assert list(restored) == [key]
    assert restored[key]["decision"] == "USER_CONFIRMED"
    assert restored[key]["item_ref"]["title"] == key[1]


def test_malformed_overlay_is_rejected() -> None:
    with pytest.raises(GenerationContractError):
        decode_program_overlay({"items": "not-a-list"})
    with pytest.raises(GenerationContractError):
        decode_program_overlay({"items": [{"content": "нет названия"}]})
    with pytest.raises(GenerationContractError):
        decode_program_overlay(
            {
                "items": [
                    {
                        "title": "Тема",
                        "content": "x" * 200_001,
                        "number": None,
                        "parent_section": None,
                        "study_year": 1,
                    }
                ]
            }
        )
    with pytest.raises(GenerationContractError):
        decode_program_overlay(
            {
                "items": [
                    {
                        "title": "Тема",
                        "content": "текст",
                        "number": None,
                        "parent_section": None,
                        "study_year": True,
                    }
                ]
            }
        )
    payload = build_generation_payload(
        _plan(),
        academic_year="2026–2027",
        source_plan_name=UTP_PATH.name,
        program_filename=PROGRAM_PATH.name,
        program_content=PROGRAM_PATH.read_bytes(),
        generator_revision=REVISION,
    )
    payload["program_overlay"] = {"items": "not-a-list"}
    with pytest.raises(GenerationContractError):
        _decode_generation_payload(payload)


def test_legacy_payload_without_overlay_keeps_parsed_program() -> None:
    payload = build_generation_payload(
        _plan(),
        academic_year="2026–2027",
        source_plan_name=UTP_PATH.name,
        program_filename=PROGRAM_PATH.name,
        program_content=PROGRAM_PATH.read_bytes(),
        template=_template(),
        generator_revision=REVISION,
    )
    payload["match_reviews"] = {
        '["1", "Тема", null]': {"decision": "USER_CONFIRMED"}
    }
    assert "program_overlay" not in payload
    decoded = _decode_generation_payload(_roundtrip_payload(payload))
    original = parse_program(PROGRAM_PATH.read_bytes(), PROGRAM_PATH.name, study_year=2)
    assert [item.title for item in decoded.program.content_items] == [
        item.title for item in original.content_items
    ]
    assert list(decoded.match_reviews) == ['["1", "Тема", null]']


def test_known_program_auto_path_payload_has_no_overlay() -> None:
    payload = build_generation_payload(
        _plan(),
        academic_year="2026–2027",
        source_plan_name=UTP_PATH.name,
        program_filename=PROGRAM_PATH.name,
        program_content=PROGRAM_PATH.read_bytes(),
        template=_template(),
        generator_revision=REVISION,
    )
    assert "program_overlay" not in payload
    decoded = _decode_generation_payload(_roundtrip_payload(payload))
    original = parse_program(PROGRAM_PATH.read_bytes(), PROGRAM_PATH.name, study_year=2)
    assert tuple(_item_tuple(item) for item in decoded.program.content_items) == tuple(
        _item_tuple(item) for item in original.content_items
    )


def test_holdout_overlay_and_w9_survive_api_decode() -> None:
    payload_bytes, filename, _program, confirmation, overlaid = _holdout_confirmation()
    encoded_items = tuple(_item_tuple(item) for item in overlaid.content_items)
    wire = build_generation_payload(
        confirmation.plan,
        academic_year=APPROVED_ACADEMIC_YEAR,
        source_plan_name="Подтверждённая структура программы",
        program_filename=filename,
        program_content=payload_bytes,
        template=_template(),
        match_reviews=confirmation.match_reviews,
        generator_revision=REVISION,
        program=overlaid,
    )
    assert isinstance(wire["match_reviews"], list)
    assert "program_overlay" in wire
    before = build_content_model(
        build_schedule(confirmation.plan, APPROVED_ACADEMIC_YEAR),
        confirmation.plan,
        overlaid,
        "Подтверждённая структура программы",
        match_reviews=confirmation.match_reviews,
    )
    week9_before = next(row for row in before if row.week_number == 9)
    assert week9_before.match_status is MatchStatus.USER_CONFIRMED
    assert any(part.weekly_content_assigned for part in week9_before.week_parts)
    decoded = _decode_generation_payload(_roundtrip_payload(wire))
    assert tuple(_item_tuple(item) for item in decoded.program.content_items) == encoded_items
    assert set(decoded.match_reviews) == set(confirmation.match_reviews)
    assert all(
        review["decision"] == "USER_CONFIRMED"
        for review in decoded.match_reviews.values()
    )
    content = build_content_model(
        build_schedule(decoded.plan, APPROVED_ACADEMIC_YEAR),
        decoded.plan,
        decoded.program,
        decoded.source_plan_name,
        match_reviews=decoded.match_reviews,
    )
    week9 = next(row for row in content if row.week_number == 9)
    assert week9.match_status is MatchStatus.USER_CONFIRMED
    assert any(part.weekly_content_assigned for part in week9.week_parts)
    assert week9.match_status is week9_before.match_status
    assert week9.program_content_full == week9_before.program_content_full


def test_live_equivalent_holdout_docx_has_no_empty_result_control(monkeypatch) -> None:
    monkeypatch.setattr(
        "calendar_pedagoga.pipeline.validate_calendar_docx",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        "calendar_pedagoga.pipeline.validate_calendar_docx_visual",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        "calendar_pedagoga.pipeline.has_blocking_qa_issues",
        lambda *_args, **_kwargs: False,
    )
    payload_bytes, filename, _program, confirmation, overlaid = _holdout_confirmation()
    wire = build_generation_payload(
        confirmation.plan,
        academic_year=APPROVED_ACADEMIC_YEAR,
        source_plan_name="Подтверждённая структура программы",
        program_filename=filename,
        program_content=payload_bytes,
        match_reviews=confirmation.match_reviews,
        generator_revision=REVISION,
        program=overlaid,
    )
    decoded = _decode_generation_payload(_roundtrip_payload(wire))
    result = run_calendar_pipeline(
        decoded.plan,
        decoded.program,
        academic_year=APPROVED_ACADEMIC_YEAR,
        template=select_calendar_template(),
        source_utp_name=decoded.source_plan_name,
        use_ai=False,
        program_filename=decoded.program_filename,
        match_reviews=decoded.match_reviews,
        semantic_revision=REVISION,
    )
    assert result.status is CalendarDocumentStatus.DRAFT_READY
    assert any(GENERIC_ONLY in case.reasons for case in result.review_cases)
    assert decoded.plan.total_hours == 96
    weeks = extract_logical_weeks(result.content)
    empty_result = [week.week_number for week in weeks if not week.planned_result.strip()]
    empty_control = [week.week_number for week in weeks if not week.assessment.strip()]
    assert len(weeks) == 32
    assert empty_result == []
    assert empty_control == []
    week9 = next(week for week in weeks if week.week_number == 9)
    assert week9.planned_result.strip()
    assert week9.assessment.strip()
