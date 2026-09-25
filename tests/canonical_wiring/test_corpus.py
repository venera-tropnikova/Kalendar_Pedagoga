"""One corpus scenario: every branch uses the flagged production screen."""
from pathlib import Path
import json

import pytest
from streamlit.testing.v1 import AppTest

from calendar_pedagoga.ingestion_confirmation import State, apply, assess, build_model, save, start
from calendar_pedagoga.ingestion_shadow import ShadowBlocked, run_shadow
from calendar_pedagoga.lossless_document import extract_document
from calendar_pedagoga.structural_interpretation import interpret_document
from tools.lossless_document.corpus import corpus_paths
from tests.canonical_wiring.test_flag import QUESTIONS, assert_only_question

ROOT = Path(__file__).parents[2]
APP = ROOT / "app.py"
RECORDS = json.loads((ROOT / "tests" / "lossless_document" / "corpus.json").read_text(encoding="utf-8-sig"))
YEAR = "2026–2027"


def explore(session):
    view = assess(session)
    if view.state == State.YEAR_AMBIGUOUS and view.years:
        for year in view.years:
            yield from explore(apply(session, "choose_year", {"year": year}))
    elif view.state == State.PLAN_AMBIGUOUS and view.plans:
        for option in view.plans:
            yield from explore(apply(session, "choose_plan", {"id": option.id}))
    else:
        yield session


@pytest.mark.parametrize("record", RECORDS, ids=[item["id"] for item in RECORDS])
def test_corpus_branch_uses_the_canonical_screen(record, monkeypatch):
    monkeypatch.setenv("CANONICAL_INGESTION_ENABLED", "true")
    resolved = next(item for item in corpus_paths(ROOT / "tests" / "lossless_document" / "corpus.json") if item["id"] == record["id"])
    model = build_model(interpret_document(extract_document(resolved["path"])))
    for session in explore(start(model)):
        view = assess(session)
        assert view.state in QUESTIONS
        app = AppTest.from_file(str(APP), default_timeout=180)
        app.session_state["canonical_model"] = model
        app.session_state["canonical_review_json"] = save(session)
        app.run()
        assert not app.exception
        assert_only_question(app, view.state)
        if view.state != State.VALID:
            with pytest.raises(ValueError):
                apply(session, "confirm")
            with pytest.raises(ShadowBlocked) as caught:
                run_shadow(session, academic_year=YEAR, publish=False)
            assert caught.value.docx is None
            continue
        next(button for button in app.button if button.label == "Да, продолжить").click().run()
        assert not app.exception
        downloads = app.get("download_button")
        assert len(downloads) == 1
        assert downloads[0].label.startswith("Скачать календарный план")
        assert app.session_state["canonical_docx"][1]
        produced = run_shadow(apply(session, "confirm"), academic_year=YEAR, publish=False)
        assert produced.utp.metadata.study_weeks == len(produced.lessons)
        assert produced.utp.metadata.hours_per_year == produced.utp.metadata.study_weeks * produced.utp.metadata.hours_per_week
