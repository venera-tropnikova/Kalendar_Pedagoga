from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import inspect

from calendar_pedagoga.content_engine_v2 import (
    PROVENANCE_GENERIC_ONLY,
    build_lesson_content_v2,
    derive_fields_v2,
)
from calendar_pedagoga.content_generation import CalendarContentRow, WeekTopicPart
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.sentence_frame import (
    CONTROL_CHECK,
    CONTROL_OBSERVATION,
    CONTROL_ORAL,
    control_from_frame,
    frames_for_confirmed_row,
    grammar_gate,
    render_frames,
    render_sentence_frame,
    result_from_frame,
    row_uses_sentence_frame,
    text_is_damaged,
)


def _part(
    *,
    topic_title: str,
    theory_hours: int = 0,
    practice_hours: int = 0,
    content: str = "",
    section: str = "Раздел",
    topic_number: str = "1",
    assigned: bool = True,
    status: MatchStatus = MatchStatus.USER_CONFIRMED,
) -> WeekTopicPart:
    return WeekTopicPart(
        topic_number=topic_number,
        topic_title=topic_title,
        section=section,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        match_status=status,
        program_section=section,
        program_topic=topic_title,
        program_content_full=content,
        weekly_content_assigned=assigned,
    )


def _row(
    *parts: WeekTopicPart,
    match_status: MatchStatus = MatchStatus.USER_CONFIRMED,
    content: str = "",
    topic_title: str = "Тема занятия",
) -> CalendarContentRow:
    if parts:
        topic_title = parts[0].topic_title
        content = content or "\n".join(
            part.program_content_full for part in parts if part.program_content_full
        )
        theory_hours = sum(part.theory_hours for part in parts)
        practice_hours = sum(part.practice_hours for part in parts)
    else:
        theory_hours = 2
        practice_hours = 0
    return CalendarContentRow(
        week_number=1,
        date_range="01–07.09",
        month="Сентябрь",
        section=parts[0].section if parts else "Раздел",
        topic_number=parts[0].topic_number if parts else "1",
        topic_title=topic_title,
        source_topic_title=topic_title,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        total_hours=theory_hours + practice_hours,
        match_status=match_status,
        program_section=parts[0].program_section if parts else "Раздел",
        program_topic=topic_title,
        program_content_full=content,
        program_content_preview=content,
        source_program_name="Программа неизвестного цикла",
        source_utp_name="synthetic.docx",
        week_parts=parts,
    )


def _build(*parts: WeekTopicPart, **kwargs):
    return build_lesson_content_v2((_row(*parts, **kwargs),))[0]


def test_maintaining_order_uses_proven_template_or_fallback() -> None:
    row = _build(
        _part(
            topic_title="Введение",
            theory_hours=2,
            content="Поддержание порядка на рабочем месте в течение занятия.",
        )
    )
    result = row.planned_result
    allowed = (
        result.startswith("Поддерживает порядок"),
        result == "Характеризует содержание темы «Введение».",
    )
    assert any(allowed)
    assert "порядки" not in result
    if result.startswith("Поддерживает порядок"):
        assert "за поддержанием порядка" in row.assessment_method.casefold()
    assert row.assessment_method.strip()
    assert not text_is_damaged(result)
    assert not text_is_damaged(row.assessment_method)


def test_composing_uses_proven_template_or_fallback() -> None:
    row = _build(
        _part(
            topic_title="Композиции",
            practice_hours=3,
            content="Составление композиций из природного сырья.",
        )
    )
    result = row.planned_result
    allowed = (
        result.startswith("Составляет композиции"),
        result == "Выполняет практическую работу по теме «Композиции».",
    )
    assert any(allowed)
    assert "композициу" not in result.casefold()
    if result.startswith("Составляет композиции"):
        assert "за составлением композиций" in row.assessment_method.casefold()
    assert not text_is_damaged(result)
    assert not text_is_damaged(row.assessment_method)


def test_specific_practical_work_is_not_replaced_by_parent_topic() -> None:
    parent = "Аппликация. Настенные композиции"
    row = _build(
        _part(
            topic_title=parent,
            practice_hours=3,
            content="«Аппликация из семян».",
        )
    )
    result = row.planned_result.casefold()
    control = row.assessment_method.casefold()
    assert "семян" in result
    assert parent.casefold() not in result
    assert "семян" in control
    assert "выполняет аппликацию из семян" in result
    assert "аппликации из семян" in control


def test_mixed_theory_and_practice_keep_both_actions() -> None:
    row = _build(
        _part(
            topic_title="Ткачество",
            theory_hours=1,
            content="Поддержание порядка на рабочем месте.",
            topic_number="6",
        ),
        _part(
            topic_title="Декоративные работы",
            practice_hours=2,
            content="«Аппликация из семян».",
            topic_number="7",
            section="Другой раздел",
        ),
    )
    result = row.planned_result
    control = row.assessment_method
    assert "Поддерживает порядок" in result or "Характеризует содержание темы" in result
    assert "Выполняет аппликацию из семян" in result or "практическую работу" in result
    assert "опрос" in control.casefold() or "наблюдение" in control.casefold()
    assert "наблюдение" in control.casefold()
    theory_present = (
        "поддерживает" in result.casefold() or "характеризует" in result.casefold()
    )
    practice_present = "выполняет" in result.casefold()
    assert theory_present and practice_present
    assert "за поддержанием порядка" in control.casefold() or "опрос по теме" in control.casefold()
    assert "за выполнением" in control.casefold()


def test_observation_uses_instrumental() -> None:
    part = _part(
        topic_title="Введение",
        theory_hours=2,
        content="Поддержание порядка на рабочем месте.",
    )
    frame = frames_for_confirmed_row(_row(part), (part,))[0]
    frame = replace(frame, control_type=CONTROL_OBSERVATION)
    _result, control, fell = render_sentence_frame(frame)
    assert not fell
    assert control.casefold().startswith(
        "педагогическое наблюдение за поддержанием порядка"
    )
    assert grammar_gate(frame, _result, control)
    analysis = _part(
        topic_title="Конструкция",
        theory_hours=2,
        content="Анализ конструкции изделия.",
    )
    analysis_frame = frames_for_confirmed_row(_row(analysis), (analysis,))[0]
    _result, control, fell = render_sentence_frame(analysis_frame)
    assert not fell
    assert "за анализом конструкции" in control.casefold()
    composing = _part(
        topic_title="Композиции",
        practice_hours=3,
        content="Составление композиций из природного сырья.",
    )
    composing_frame = frames_for_confirmed_row(_row(composing), (composing,))[0]
    _result, control, fell = render_sentence_frame(composing_frame)
    assert not fell
    assert "за составлением композиций" in control.casefold()


def test_oral_uses_dative() -> None:
    part = _part(
        topic_title="Введение",
        theory_hours=2,
        content="Поддержание порядка на рабочем месте.",
    )
    frame = replace(
        frames_for_confirmed_row(_row(part), (part,))[0],
        control_type=CONTROL_ORAL,
    )
    result, control, fell = render_sentence_frame(frame)
    assert not fell
    assert "опрос по поддержанию порядка" in control.casefold()
    assert grammar_gate(frame, result, control)
    topic = "Флобаризация изотопов"
    fallback = _build(
        _part(
            topic_title=topic,
            theory_hours=2,
            content="Квантовая флобаризация изотопов без готового шаблона.",
        )
    )
    assert fallback.assessment_method == f"Устный опрос по теме «{topic}»."


def test_check_uses_genitive() -> None:
    part = _part(
        topic_title="Конструкция",
        theory_hours=2,
        content="Анализ конструкции изделия.",
    )
    frame = replace(
        frames_for_confirmed_row(_row(part), (part,))[0],
        control_type=CONTROL_CHECK,
    )
    result, control, fell = render_sentence_frame(frame)
    assert not fell
    assert control.casefold().startswith("проверка анализа конструкции")
    assert grammar_gate(frame, result, control)
    composing = _part(
        topic_title="Композиции",
        practice_hours=3,
        content="Составление композиций из природного сырья.",
    )
    composing_frame = replace(
        frames_for_confirmed_row(_row(composing), (composing,))[0],
        control_type=CONTROL_CHECK,
    )
    _result, control, _fell = render_sentence_frame(composing_frame)
    assert "проверка составления композиций" in control.casefold()


def test_unknown_construction_uses_safe_fallback() -> None:
    topic = "Неизвестная конструкция"
    row = _build(
        _part(
            topic_title=topic,
            practice_hours=3,
            content="Квантовая флобаризация без готового шаблона.",
        )
    )
    assert row.planned_result == f"Выполняет практическую работу по теме «{topic}»."
    assert "по теме" in row.assessment_method
    assert row.assessment_method.count("«") == row.assessment_method.count("»")


def test_control_is_not_reparsed_from_result() -> None:
    signature = inspect.signature(control_from_frame)
    assert "result" not in signature.parameters
    assert "planned_result" not in signature.parameters
    part = _part(
        topic_title="Введение",
        theory_hours=2,
        content="Поддержание порядка на рабочем месте.",
    )
    frame = frames_for_confirmed_row(_row(part), (part,))[0]
    control = control_from_frame(frame)
    other_result = "Совершенно другой RESULT без связи с контролем."
    assert control_from_frame(frame) == control
    assert other_result not in control
    assert "за поддержанием порядка" in control.casefold()
    assert result_from_frame(frame) != control


def test_result_and_control_have_closed_quotes_and_are_complete() -> None:
    row = _build(
        _part(
            topic_title="Неизвестная тема со скобками",
            theory_hours=2,
            content="Квантовая флобаризация изотопов без готового шаблона.",
        )
    )
    for text in (row.planned_result, row.assessment_method):
        assert text.count("«") == text.count("»")
        assert not text_is_damaged(text)
        assert text.endswith(".")
        assert "порядки" not in text
        assert "конструкциу" not in text
        assert "деталую" not in text
        assert "историе" not in text
        assert "по тему №у" not in text


def test_unknown_topic_uses_safe_template() -> None:
    topic = "Флобаризация изотопов"
    row = _build(
        _part(
            topic_title=topic,
            theory_hours=2,
            content="Квантовая флобаризация изотопов без готового шаблона.",
        )
    )
    assert row.planned_result == f"Характеризует содержание темы «{topic}»."
    assert row.assessment_method == f"Устный опрос по теме «{topic}»."
    assert PROVENANCE_GENERIC_ONLY in row.provenance_codes


def test_proven_ce2_frame_is_not_degraded() -> None:
    derived = derive_fields_v2(
        topic_title="Укладка рюкзака",
        theory_text="",
        practice_text="Выполнение упражнения.",
        program_content="Выполнение упражнения.",
        theory_hours=0,
        practice_hours=2,
    )
    assert derived.planned_result == "Выполняет упражнение."
    row = _build(
        _part(
            topic_title="Укладка рюкзака",
            practice_hours=2,
            content="Выполнение упражнения.",
            assigned=False,
            status=MatchStatus.EXACT,
        ),
        match_status=MatchStatus.EXACT,
        content="Выполнение упражнения.",
        topic_title="Укладка рюкзака",
    )
    assert row.planned_result == "Выполняет упражнение."
    assert PROVENANCE_GENERIC_ONLY not in row.provenance_codes
    assert not row_uses_sentence_frame(row.source, row.source.week_parts)


def test_non_cia_work_title_is_not_inflected() -> None:
    row = _build(
        _part(
            topic_title="Декоративные украшения",
            practice_hours=3,
            content="«Украшения из ракушек».",
        )
    )
    result = row.planned_result.casefold()
    control = row.assessment_method.casefold()
    assert "украшению" not in result
    assert "украшении" not in control
    assert "ракушек" in result
    assert result.count("«") == result.count("»")
    assert control.count("«") == control.count("»")
    assert not text_is_damaged(row.planned_result)
    assert not text_is_damaged(row.assessment_method)


def test_result_and_control_come_from_one_frame() -> None:
    part = _part(
        topic_title="Введение",
        theory_hours=2,
        content="Поддержание порядка на рабочем месте.",
    )
    row = _row(part)
    frames = frames_for_confirmed_row(row, (part,))
    assert frames
    result, control, _fell = render_sentence_frame(frames[0])
    joined_result, joined_control, _week = render_frames(frames)
    assert result == joined_result
    assert control == joined_control
    built = _build(part)
    assert built.planned_result == result
    assert built.assessment_method == control


def test_no_holdout_dictionary_in_sentence_frame_module() -> None:
    text = Path(
        "src/calendar_pedagoga/sentence_frame.py"
    )
    if not text.is_file():
        text = Path(__file__).resolve().parents[1] / "src/calendar_pedagoga/sentence_frame.py"
    source = text.read_text(encoding="utf-8").casefold()
    forbidden = (
        "природн",
        "аппликац",
        "скульптур",
        "плетён",
        "плетен",
        "солён",
        "солен",
        "турист",
        "ключ",
        "лазан",
    )
    for needle in forbidden:
        assert needle not in source
