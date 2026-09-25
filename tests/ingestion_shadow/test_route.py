"""Stage E shadow route: one confirmed path, no production switch."""
from fractions import Fraction
import json
from pathlib import Path
import runpy

import pytest

from calendar_pedagoga.ingestion_adapter import ExplicitWorkload
from calendar_pedagoga.ingestion_confirmation import apply, assess, build_model, start, State
from calendar_pedagoga.ingestion_shadow import SHADOW_ENABLED, ShadowBlocked, ingest, run_shadow
from tools.lossless_document.corpus import corpus_paths

helpers = runpy.run_path(str(Path(__file__).parents[1] / "structural_interpretation" / "test_structural_properties.py"))
p, table, interpret = helpers["p"], helpers["table"], helpers["interpret"]
YEAR = "2026–2027"


def confirmed_model():
    values = [
        ("1", "Тема Альфа", "1", "1", "0", "0"),
        ("2", "Тема Бета", "1", "1", "0", "0"),
        ("", "Итого", "2", "2", "0", "0"),
    ]
    body = p("Учебно-тематический план 1 года обучения", bold=True)
    body += table(values=values)
    body += p("Содержание программы 1 года обучения", bold=True)
    body += p("1. Тема Альфа", bold=True) + p("Изучает устройство компаса.")
    body += p("2. Тема Бета", bold=True) + p("Изучает правила ориентирования.")
    return interpret(body)


def confirm(model):
    session = start(model)
    view = assess(session)
    if view.state == State.BINDING_AMBIGUOUS:
        session = apply(session, "bind_topics", {"bindings": {
            question.topic_id: [view.content[index].id]
            for index, question in enumerate(view.questions)
        }})
    assert assess(session).state == State.VALID
    return apply(session, "confirm")


def test_flag_stays_off_and_production_does_not_import_shadow():
    assert SHADOW_ENABLED is False
    root = Path(__file__).parents[2]
    for path in (root / "src" / "calendar_pedagoga").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "ingestion_shadow" not in text
        assert "ingestion_adapter" not in text


def test_unconfirmed_model_does_not_create_docx():
    with pytest.raises(ShadowBlocked) as caught:
        run_shadow(start(build_model(confirmed_model())), academic_year=YEAR)
    assert caught.value.docx is None
    assert caught.value.code == "UNCONFIRMED"


def test_confirmed_route_reaches_docx_without_training_loss():
    result = run_shadow(
        confirm(build_model(confirmed_model())),
        academic_year=YEAR,
        workload=ExplicitWorkload(2, Fraction(1), "explicit"),
    )
    assert result.plan_source == "EMBEDDED"
    assert result.docx
    assert result.workload.study_weeks == 2
    totals = {key: value.arithmetic_value for key, value in result.packet.plan.canonical_plan.totals}
    assert totals["TRAINING"] == 0
    assert totals["THEORY"] + totals["PRACTICE"] == totals["TOTAL"]
    fragment_paths = [
        destination
        for entry in result.packet.ledger.entries
        for destination in entry.destinations
        if destination.startswith("fragments[")
    ]
    assert len(fragment_paths) == len(set(fragment_paths))
    assert all(trace.refs and trace.spans for trace in result.traces if trace.field == "planned_result")
    assert all(lesson.planned_result.strip() and lesson.assessment_method.strip() for lesson in result.lessons)


def _branches(session):
    view = assess(session)
    if view.state == State.YEAR_AMBIGUOUS and view.years:
        for year in view.years:
            yield from _branches(apply(session, "choose_year", {"year": year}))
    elif view.state == State.PLAN_AMBIGUOUS and view.plans:
        for option in view.plans:
            yield from _branches(apply(session, "choose_plan", {"id": option.id}))
    else:
        yield session


def test_corpus_ready_stops_on_existing_downstream_defects_and_blocked_states_do_not():
    root = Path(__file__).parents[2]
    records = json.loads((root / "tests" / "lossless_document" / "corpus.json").read_text(encoding="utf-8-sig"))
    ready = 0
    for record in records:
        resolved = next(item for item in corpus_paths(root / "tests" / "lossless_document" / "corpus.json") if item["id"] == record["id"])
        model = build_model(ingest(resolved["path"]))
        for session in _branches(start(model)):
            view = assess(session)
            if view.state != State.VALID:
                with pytest.raises(ShadowBlocked) as caught:
                    run_shadow(session, academic_year=YEAR)
                assert caught.value.docx is None
                continue
            result = run_shadow(apply(session, "confirm"), academic_year=YEAR, publish=False)
            ready += 1
            assert result.docx == b""
            assert result.utp.metadata.study_weeks == 36
            assert result.utp.metadata.hours_per_year == 144
            assert len(result.lessons) == 36
            totals = {
                key: value.arithmetic_value
                for key, value in result.packet.plan.canonical_plan.totals
            }
            assert totals["TOTAL"] == 144
            assert totals["THEORY"] == 36
            assert totals["TRAINING"] == 36
            assert totals["PRACTICE"] == 72
            identities = [
                (
                    lesson.source.source.topic_number,
                    lesson.source.source.topic_title,
                    lesson.lesson_type,
                    lesson.planned_result,
                    lesson.assessment_method,
                )
                for lesson in result.lessons
            ]
            assert len(identities) == len(set(identities))
    assert ready == 1
