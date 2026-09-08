import inspect
from datetime import date
from unittest.mock import patch

from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.match_review import (
    MISSING_PROGRAM_CONTENT_NOTICE,
    ProgramItemRef,
    apply_match_reviews,
    is_disputed_match,
    is_missing_program_content,
    rejected_topic_count,
    resolve_item_ref,
    review_scope,
    topic_key,
    unresolved_disputed,
)
from calendar_pedagoga.matching import (
    MatchStatus,
    bound_program_item,
    match_position,
    match_utp_to_program,
)
from calendar_pedagoga.parsing import Hours, Topic, UtpMetadata, UtpParseResult
from calendar_pedagoga.program_parsing import ProgramContentItem, ProgramData
from calendar_pedagoga.scheduling import AcademicWeek, ScheduledElement, ScheduleResult
from calendar_pedagoga import content_generation
from calendar_pedagoga import ui


def _topic(number, title, section="Раздел", theory=0, practice=2):
    return Topic(number, title, Hours(theory + practice, theory, practice), section)


def _item(number, title, content, section="Раздел", study_year=None):
    return ProgramContentItem(number, title, content, section, study_year)


def _program(*items):
    return ProgramData(
        title="Синтетика",
        duration=None,
        student_age=None,
        goal=None,
        tasks=(),
        lesson_forms=(),
        teaching_methods=(),
        expected_results=(),
        knowledge_outcomes=(),
        skill_outcomes=(),
        content_items=items,
    )


def _one_week_model(topic, program, reviews=None):
    utp = UtpParseResult(
        metadata=UtpMetadata(
            hours_per_week=2,
            hours_per_year=2,
            study_weeks=1,
            workload_provenance="document",
        ),
        sections=(),
        topics=(topic,),
        table_totals=Hours(2, 0, 2),
    )
    week = AcademicWeek(1, date(2026, 9, 1), date(2026, 9, 7), "Сентябрь", "2026–2027")
    schedule = ScheduleResult(
        weeks=(week,),
        elements=(
            ScheduledElement(
                topic.parent_section or topic.title,
                topic.number,
                topic.title,
                "practice",
                2,
                week,
            ),
        ),
        warnings=(),
    )
    return build_content_model(
        schedule,
        utp,
        program,
        "synthetic-utp.docx",
        match_reviews=reviews,
    )


def test_build_content_model_does_not_read_session_state() -> None:
    source = inspect.getsource(content_generation.build_content_model)
    assert "session_state" not in source


def test_unresolved_disputed_leaves_content_empty() -> None:
    topic = _topic("4.1", "Рисование натюрморта", "ИЗО")
    foreign = _item("4.1", "Сольфеджио", "Чужое описание интервалов.", "ИЗО")
    match = match_position(topic, (foreign,))
    assert is_disputed_match(match)
    assert unresolved_disputed((match,), {}) == (match,)

    row = _one_week_model(topic, _program(foreign))[0]
    assert row.program_content_full == ""
    assert row.program_topic == ""
    assert bound_program_item(match) is None


def test_occupied_same_number_is_not_disputed_and_shows_notice() -> None:
    halt = _topic("4.1", "Рисование натюрморта", "ИЗО")
    keep = _topic("4.2", "Сольфеджио", "ИЗО")
    item = _item("4.1", "Сольфеджио", "Интервалы и слуховые диктанты.", "ИЗО", 1)
    matches = match_utp_to_program((halt, keep), (item,))
    assert not is_disputed_match(matches[0])
    assert is_missing_program_content(matches[0])
    assert unresolved_disputed(matches, {}) == ()

    utp = UtpParseResult(
        metadata=UtpMetadata(
            hours_per_week=2,
            hours_per_year=4,
            study_weeks=2,
            workload_provenance="document",
        ),
        sections=(),
        topics=(halt, keep),
        table_totals=Hours(4, 0, 4),
    )
    week1 = AcademicWeek(1, date(2026, 9, 1), date(2026, 9, 7), "Сентябрь", "2026–2027")
    week2 = AcademicWeek(2, date(2026, 9, 8), date(2026, 9, 14), "Сентябрь", "2026–2027")
    schedule = ScheduleResult(
        weeks=(week1, week2),
        elements=(
            ScheduledElement("ИЗО", halt.number, halt.title, "practice", 2, week1),
            ScheduledElement("ИЗО", keep.number, keep.title, "practice", 2, week2),
        ),
        warnings=(),
    )
    rows = build_content_model(schedule, utp, _program(item), "synthetic.docx")
    halt_row = next(row for row in rows if row.topic_title == halt.title)
    keep_row = next(row for row in rows if row.topic_title == keep.title)
    assert halt_row.program_content_full == ""
    assert halt_row.warnings == (MISSING_PROGRAM_CONTENT_NOTICE,)
    assert keep_row.program_content_full == "Интервалы и слуховые диктанты."
    assert keep_row.match_status is MatchStatus.EXACT


def test_not_matched_with_candidates_can_be_confirmed() -> None:
    topic = _topic("1.1", "Компас", "Ориентирование")
    item = _item("1.1", "Работа с компасом", "Стрелка и азимут.", "Ориентирование")
    match = match_position(topic, (item,))
    assert match.status in {MatchStatus.NOT_MATCHED, MatchStatus.UNCONFIRMED}
    assert match.ambiguous_candidates
    reviews = {
        topic_key(topic): {
            "decision": "USER_CONFIRMED",
            "item_ref": ProgramItemRef.from_item(item).as_dict(),
        }
    }
    row = _one_week_model(topic, _program(item), reviews)[0]
    assert row.match_status is MatchStatus.USER_CONFIRMED
    assert row.program_content_full == "Стрелка и азимут."


def test_user_rejected_and_unconfirmed_do_not_use_missing_content_notice() -> None:
    topic = _topic("4.1", "Рисование натюрморта", "ИЗО")
    foreign = _item("4.1", "Сольфеджио", "Чужое описание интервалов.", "ИЗО")
    rejected = {topic_key(topic): {"decision": "USER_REJECTED", "item_ref": None}}
    rejected_row = _one_week_model(topic, _program(foreign), rejected)[0]
    assert any("решение педагога" in warning for warning in rejected_row.warnings)
    assert MISSING_PROGRAM_CONTENT_NOTICE not in rejected_row.warnings

    unconfirmed_row = _one_week_model(topic, _program(foreign))[0]
    assert unconfirmed_row.match_status is MatchStatus.UNCONFIRMED
    assert any("номер без подтверждения названия" in warning for warning in unconfirmed_row.warnings)
    assert MISSING_PROGRAM_CONTENT_NOTICE not in unconfirmed_row.warnings


def test_user_confirm_binds_only_selected_item() -> None:
    topic = _topic("4.1", "Рисование натюрморта", "ИЗО")
    foreign = _item("4.1", "Сольфеджио", "Чужое описание интервалов.", "ИЗО", 1)
    chosen = _item("8.2", "Натюрморт акварелью", "Только выбранный текст.", "ИЗО", 1)
    reviews = {
        topic_key(topic): {
            "decision": "USER_CONFIRMED",
            "item_ref": ProgramItemRef.from_item(chosen).as_dict(),
        }
    }
    row = _one_week_model(topic, _program(foreign, chosen), reviews)[0]
    assert row.match_status is MatchStatus.USER_CONFIRMED
    assert row.program_topic == chosen.title
    assert row.program_content_full == "Только выбранный текст."
    assert "Чужое" not in row.program_content_full


def test_user_rejected_leaves_content_empty_with_warning() -> None:
    topic = _topic("4.1", "Рисование натюрморта", "ИЗО")
    foreign = _item("4.1", "Сольфеджио", "Чужое описание интервалов.", "ИЗО")
    reviews = {topic_key(topic): {"decision": "USER_REJECTED", "item_ref": None}}
    row = _one_week_model(topic, _program(foreign), reviews)[0]
    assert row.program_content_full == ""
    assert row.program_topic == ""
    assert any("решение педагога" in warning for warning in row.warnings)
    assert rejected_topic_count(reviews) == 1


def test_auto_exact_and_text_ignore_reviews() -> None:
    exact_topic = _topic("2.1", "Ансамбль", "Музыка")
    exact_item = _item("5.0", "Ансамбль", "Репетиция состава.", "Музыка")
    exact = match_position(exact_topic, (exact_item,))
    assert exact.status is MatchStatus.EXACT

    text_topic = _topic(
        "1.1",
        "Туристские путешествия, история развития туризма",
        "Введение",
    )
    text_item = _item(
        "1.1",
        "Туристские путешествия, история развития туризма в г. Салават",
        "История туризма.",
        "Введение",
    )
    text = match_position(text_topic, (text_item,))
    assert text.status is MatchStatus.TEXT_MATCH

    poison = {
        topic_key(exact_topic): {"decision": "USER_REJECTED", "item_ref": None},
        topic_key(text_topic): {"decision": "USER_REJECTED", "item_ref": None},
    }
    applied = apply_match_reviews((exact, text), (exact_item, text_item), poison)
    assert applied[0].status is MatchStatus.EXACT
    assert bound_program_item(applied[0]) is exact_item
    assert applied[1].status is MatchStatus.TEXT_MATCH
    assert bound_program_item(applied[1]) is text_item

    exact_row = _one_week_model(exact_topic, _program(exact_item), poison)[0]
    assert exact_row.program_content_full == "Репетиция состава."
    assert exact_row.match_status is MatchStatus.EXACT


def test_resolve_item_ref_does_not_pick_first_if_ambiguous() -> None:
    first = _item("1.1", "Тема", "Первый текст.", "Раздел", 1)
    second = _item("1.1", "Тема", "Второй текст.", "Раздел", 1)
    ref = ProgramItemRef.from_item(first)
    assert resolve_item_ref((first, second), ref) is None
    assert resolve_item_ref((first,), ref) is first


def test_review_scope_changes_with_file_or_study_year() -> None:
    same = review_scope("utp.docx", b"utp", "program.docx", b"prog", 1)
    assert same == review_scope("utp.docx", b"utp", "program.docx", b"prog", 1)
    assert same != review_scope("utp.docx", b"other", "program.docx", b"prog", 1)
    assert same != review_scope("utp.docx", b"utp", "program.docx", b"other", 1)
    assert same != review_scope("utp.docx", b"utp", "program.docx", b"prog", 2)


def test_reviews_for_scope_reset_when_scope_changes() -> None:
    key = ("4.1", "Тема", "Раздел")
    state = {
        "match_reviews": {key: {"decision": "USER_CONFIRMED", "item_ref": None}},
        "match_reviews_scope": "old-scope",
    }
    with patch.object(ui.st, "session_state", state):
        reviews = ui._reviews_for_scope("new-scope")
        assert reviews == {}
        assert state["match_reviews"] == {}
        assert state["match_reviews_scope"] == "new-scope"
