"""Structural slot binding. No real program names and no document reparse."""
from fractions import Fraction
from pathlib import Path
import runpy

import pytest

from calendar_pedagoga.ingestion_adapter import ExplicitWorkload, adapt
from calendar_pedagoga.ingestion_confirmation import apply, assess, build_model, start, State
from calendar_pedagoga.ingestion_shadow import ShadowBlocked, run_shadow
from calendar_pedagoga.ingestion_shadow.route import _schedule, _topics, _workload
from calendar_pedagoga.ingestion_shadow.slot_binding import bind_slots

helpers = runpy.run_path(str(Path(__file__).parents[1] / "structural_interpretation" / "test_structural_properties.py"))
p, table, interpret = helpers["p"], helpers["table"], helpers["interpret"]
YEAR = "2026–2027"


def _document(paragraphs, values):
    body = p("Учебно-тематический план 1 года обучения", bold=True)
    body += table(values=values)
    body += p("Содержание программы 1 года обучения", bold=True)
    for text, bold in paragraphs:
        body += p(text, bold=bold)
    return interpret(body)


def _confirm(model, bindings=None):
    session = start(build_model(model))
    view = assess(session)
    if view.state == State.YEAR_AMBIGUOUS:
        session = apply(session, "choose_year", {"year": view.years[0]})
        view = assess(session)
    if view.state == State.BINDING_AMBIGUOUS:
        if bindings is None:
            bindings = {
                question.topic_id: [view.content[index].id]
                for index, question in enumerate(view.questions)
            }
        session = apply(session, "bind_topics", {"bindings": bindings})
    assert assess(session).state == State.VALID
    return apply(session, "confirm")


def _slots(model, weeks, hours, bindings=None):
    packet = adapt(_confirm(model, bindings))
    topics = _topics(packet)
    workload = ExplicitWorkload(weeks, Fraction(hours), "explicit")
    _utp, elements = _schedule(packet, topics, _workload(packet, workload), YEAR)
    return bind_slots(packet, elements)


def test_fragments_are_assigned_in_order_across_weeks():
    values = [
        ("1", "Тема Альфа", "3", "3", "0", "0"),
        ("2", "Тема Бета", "1", "1", "0", "0"),
        ("", "Итого", "4", "4", "0", "0"),
    ]
    model = _document([
        ("1. Тема Альфа", True),
        ("Первый фрагмент.", False),
        ("Второй фрагмент.", False),
        ("Третий фрагмент.", False),
        ("2. Тема Бета", True),
        ("Фрагмент другой темы.", False),
    ], values)
    slots = _slots(model, 4, 1)
    alpha = [slot for slot in slots if slot.title == "Тема Альфа"]
    beta = [slot for slot in slots if slot.title == "Тема Бета"]
    assert [slot.allocation_index for slot in alpha] == [0, 1, 2]
    assert [slot.text for slot in alpha] == [
        "Первый фрагмент.",
        "Второй фрагмент.",
        "Третий фрагмент.",
    ]
    assert all(slot.fragment_ids and slot.spans and slot.refs for slot in alpha)
    assert len({slot.fragment_ids for slot in alpha}) == 3
    assert beta[0].text == "Фрагмент другой темы."
    assert all("Фрагмент другой темы." not in slot.text for slot in alpha)
    assert all(slot.year == 1 and not slot.blocked for slot in slots)


def test_one_fragment_covers_only_its_hour_share():
    values = [
        ("1", "Тема Альфа", "4", "4", "0", "0"),
        ("", "Итого", "4", "4", "0", "0"),
    ]
    model = _document([
        ("1. Тема Альфа", True),
        ("Единственный фрагмент.", False),
    ], values)
    slots = _slots(model, 2, 2)
    assert len(slots) == 2
    assert slots[0].fragment_ids and slots[0].text == "Единственный фрагмент."
    assert slots[1].title_fallback and not slots[1].fragment_ids
    assert slots[1].text == "Тема Альфа"
    assert slots[0].text not in slots[1].text
    assert slots[0].allocation_index == 0 and slots[1].allocation_index == 1


def test_missing_fragment_uses_confirmed_row_title():
    values = [
        ("1", "Тема Альфа", "1", "1", "0", "0"),
        ("", "Итого", "1", "1", "0", "0"),
    ]
    model = _document([("1. Тема Альфа", True)], values)
    slots = _slots(model, 1, 1)
    assert len(slots) == 1
    assert slots[0].title_fallback
    assert slots[0].text == "Тема Альфа"
    assert not slots[0].fragment_ids
    assert slots[0].refs and slots[0].spans


def test_ambiguous_binding_blocks_before_content_engine(monkeypatch):
    values = [
        ("1", "Тема Альфа", "1", "1", "0", "0"),
        ("2", "Тема Бета", "1", "1", "0", "0"),
        ("", "Итого", "2", "2", "0", "0"),
    ]
    model = _document([
        ("1. Тема Альфа", True),
        ("Фрагмент альфы.", False),
        ("2. Тема Бета", True),
        ("Фрагмент беты.", False),
    ], values)
    session = apply(start(build_model(model)), "edit", {"state": "BINDING_AMBIGUOUS"})
    view = assess(session)
    assert view.state == State.BINDING_AMBIGUOUS
    shared = next(question.options[0].id for question in view.questions if question.title == "Тема Альфа")
    confirmed = apply(session, "bind_topics", {"bindings": {
        question.topic_id: [shared] for question in view.questions
    }})
    confirmed = apply(confirmed, "confirm")
    packet = adapt(confirmed)
    topics = _topics(packet)
    workload = ExplicitWorkload(2, Fraction(1), "explicit")
    _utp, elements = _schedule(packet, topics, _workload(packet, workload), YEAR)
    slots = bind_slots(packet, elements)
    assert slots and all(slot.blocked == "AMBIGUOUS_BINDING" for slot in slots)
    assert all(slot.text == "" and slot.fragment_ids == () for slot in slots)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("CE2 must not run for an ambiguous slot")

    monkeypatch.setattr(
        "calendar_pedagoga.content_engine_v2.build_lesson_content_v2",
        forbidden,
    )
    with pytest.raises(ShadowBlocked) as caught:
        run_shadow(confirmed, academic_year=YEAR, workload=ExplicitWorkload(2, Fraction(1), "explicit"))
    assert caught.value.code == "AMBIGUOUS_BINDING"
    assert caught.value.docx is None


def test_other_year_and_other_topic_text_are_not_borrowed():
    values = [
        ("1", "Тема Альфа", "1", "1", "0", "0"),
        ("", "Итого", "1", "1", "0", "0"),
    ]
    body = p("Учебно-тематический план 1 года обучения", bold=True)
    body += table(values=values)
    body += p("Содержание программы 1 года обучения", bold=True)
    body += p("1. Тема Альфа", bold=True) + p("Свой фрагмент.")
    body += p("Учебно-тематический план 2 года обучения", bold=True)
    body += table(values=values)
    body += p("Содержание программы 2 года обучения", bold=True)
    body += p("1. Тема Альфа", bold=True) + p("Чужой год.")
    model = interpret(body)
    session = start(build_model(model))
    view = assess(session)
    assert view.state == State.YEAR_AMBIGUOUS
    session = apply(session, "choose_year", {"year": 1})
    view = assess(session)
    if view.state == State.BINDING_AMBIGUOUS:
        session = apply(session, "bind_topics", {"bindings": {
            question.topic_id: [question.options[0].id] for question in view.questions
        }})
    assert assess(session).state == State.VALID
    packet = adapt(apply(session, "confirm"))
    topics = _topics(packet)
    workload = ExplicitWorkload(1, Fraction(1), "explicit")
    _utp, elements = _schedule(packet, topics, _workload(packet, workload), YEAR)
    slots = bind_slots(packet, elements)
    assert slots
    assert all("Чужой год." not in slot.text for slot in slots)
    assert all(slot.year == 1 for slot in slots)
    assert any(slot.text == "Свой фрагмент." for slot in slots)
