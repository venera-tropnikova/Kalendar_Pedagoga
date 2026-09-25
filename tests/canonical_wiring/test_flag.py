"""Production UI keeps the legacy screen until the canonical flag is on."""
from dataclasses import replace
from pathlib import Path
import importlib.util
import json
import sys

import pytest
from streamlit.testing.v1 import AppTest

from calendar_pedagoga.canonical_wiring import canonical_ingestion_enabled
from calendar_pedagoga.ingestion_confirmation import State, assess, build_model, start

APP = Path(__file__).parents[2] / "app.py"
ROOT = Path(__file__).parents[2]

_spec = importlib.util.spec_from_file_location(
    "stage_c_structure",
    Path(__file__).parents[1] / "ingestion_confirmation" / "conftest.py",
)
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
structure = _helpers.structure

QUESTIONS = {
    State.VALID: "Да, продолжить",
    State.YEAR_AMBIGUOUS: "Подтвердить год",
    State.SOURCE_CONFLICT: "Использовать выбранный источник",
    State.PLAN_AMBIGUOUS: "Подтвердить план",
    State.COLUMN_AMBIGUOUS: "Подтвердить колонки",
    State.HOURS_CONFLICT: "Да, продолжить",
    State.CONTENT_BOUNDARY_AMBIGUOUS: "Подтвердить выбранные области",
    State.BINDING_AMBIGUOUS: "Подтвердить все связи",
}


def _open(monkeypatch, enabled):
    if enabled:
        monkeypatch.setenv("CANONICAL_INGESTION_ENABLED", "true")
    else:
        monkeypatch.delenv("CANONICAL_INGESTION_ENABLED", raising=False)
    app = AppTest.from_file(str(APP), default_timeout=60)
    return app


def _labels(app):
    return [button.label for button in app.button]


def assert_only_question(app, state):
    labels = _labels(app)
    assert QUESTIONS[state] in labels
    for other, label in QUESTIONS.items():
        if other == state or label == QUESTIONS[state]:
            continue
        assert label not in labels
    if state == State.HOURS_CONFLICT:
        assert next(button for button in app.button if button.label == "Да, продолжить").disabled
        assert app.error
    assert not app.get("download_button")


def models():
    valid = build_model(structure())
    year = structure()
    second = replace(year.year_regions[0], id="another-year", years=(2,))
    year = build_model(replace(year, year_regions=(*year.year_regions, second)))
    source = build_model(structure(), external=structure(label="Иной предмет"))
    plan = build_model(structure(count=2))
    column_doc = structure()
    table = column_doc.tables[0]
    mapped = table.plans[0]
    mapping = replace(mapped.mapping, decision=replace(mapped.mapping.decision, status="NEEDS_CONFIRMATION"))
    column = build_model(replace(column_doc, tables=(replace(table, plans=(replace(mapped, mapping=mapping),)),)))
    hours = build_model(structure(hours_delta=1))
    boundary_doc = structure()
    boundary = build_model(replace(
        boundary_doc,
        year_regions=tuple(
            replace(region, decision=replace(region.decision, status="NEEDS_CONFIRMATION"))
            if region.channel == "CONTENT" else region
            for region in boundary_doc.year_regions
        ),
    ))
    binding_doc = structure()
    binding = build_model(replace(
        binding_doc,
        bindings=tuple(replace(edge, decision=replace(edge.decision, status="NEEDS_CONFIRMATION")) for edge in binding_doc.bindings),
    ))
    return {
        State.VALID: valid,
        State.YEAR_AMBIGUOUS: year,
        State.SOURCE_CONFLICT: source,
        State.PLAN_AMBIGUOUS: plan,
        State.COLUMN_AMBIGUOUS: column,
        State.HOURS_CONFLICT: hours,
        State.CONTENT_BOUNDARY_AMBIGUOUS: boundary,
        State.BINDING_AMBIGUOUS: binding,
    }


def test_flag_defaults_off_and_legacy_screen_is_unchanged(monkeypatch):
    assert canonical_ingestion_enabled() is False
    sys.modules.pop("calendar_pedagoga.canonical_wiring.flow", None)
    app = _open(monkeypatch, False).run()
    assert not app.exception
    assert "Проверить документы" in _labels(app)
    text = " ".join(item.value for item in app.markdown)
    assert "Мы нашли" not in text
    assert "calendar_pedagoga.canonical_wiring.flow" not in sys.modules


def test_flag_on_without_document_does_not_open_legacy_generation(monkeypatch):
    app = _open(monkeypatch, True).run()
    assert not app.exception
    assert "Проверить документы" not in _labels(app)
    assert app.info
    assert not app.get("download_button")


@pytest.mark.parametrize("state", list(QUESTIONS), ids=lambda state: state.value)
def test_flag_on_shows_only_the_current_question(monkeypatch, state):
    built = models()
    assert assess(start(built[state])).state == state
    app = _open(monkeypatch, True)
    app.session_state["canonical_model"] = built[state]
    app.run()
    assert not app.exception
    assert_only_question(app, state)
    assert not app.session_state.get("canonical_review").confirmed


def test_confirmation_persists_until_the_source_changes(monkeypatch):
    model = build_model(structure())
    app = _open(monkeypatch, True)
    app.session_state["canonical_model"] = model
    app.run()
    next(button for button in app.button if button.label == "Да, продолжить").click().run()
    assert not app.exception
    assert app.session_state["canonical_review"].confirmed
    assert not app.get("download_button")
    app.run()
    assert app.session_state["canonical_review"].confirmed
    app.session_state["canonical_model"] = build_model(structure(text="Другой исходный текст."))
    app.run()
    assert not app.exception
    assert not app.session_state["canonical_review"].confirmed
    assert_only_question(app, State.VALID)


def test_valid_confirmation_reaches_downloadable_docx(monkeypatch):
    generators = importlib.util.spec_from_file_location(
        "stage_b_generators",
        ROOT / "tests" / "structural_interpretation" / "test_structural_properties.py",
    )
    module = importlib.util.module_from_spec(generators)
    generators.loader.exec_module(module)
    values = [("1", "Тема Альфа", "1", "1", "0", "0"), ("", "Итого", "1", "1", "0", "0")]
    body = module.p("Учебно-тематический план 1 года обучения", bold=True)
    body += module.table(values=values)
    body += module.p("1 учебных недель")
    body += module.p("Содержание программы 1 года обучения", bold=True)
    body += module.p("1. Тема Альфа", bold=True) + module.p("Изучает устройство компаса.")
    model = build_model(module.interpret(body))
    assert assess(start(model)).state == State.VALID
    app = AppTest.from_file(str(APP), default_timeout=180)
    monkeypatch.setenv("CANONICAL_INGESTION_ENABLED", "true")
    app.session_state["canonical_model"] = model
    app.run()
    next(button for button in app.button if button.label == "Да, продолжить").click().run()
    assert not app.exception
    downloads = app.get("download_button")
    assert len(downloads) == 1
    assert downloads[0].label.startswith("Скачать календарный план")
    assert app.session_state["canonical_docx"][1]
    assert not app.error
