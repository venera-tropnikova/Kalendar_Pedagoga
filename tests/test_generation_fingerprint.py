from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from calendar_pedagoga import ui


ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / "references" / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _generated_app():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.get("file_uploader")[0].set_value((PROGRAM.name, PROGRAM.read_bytes(), MIME))
    app.run()
    next(b for b in app.button if b.label == "Проверить документы").click().run()
    generated = SimpleNamespace(filename="calendar.docx", content=b"test-docx", warnings=())
    with patch.object(ui, "run_calendar_pipeline", return_value=generated) as pipeline:
        next(b for b in app.button if b.label == "Сформировать календарный план").click().run()
        pipeline.assert_called_once()
    assert not app.exception
    assert len(app.get("download_button")) == 1
    return app


def _assert_invalidated(app):
    assert not app.exception
    assert "calendar_download" not in app.session_state
    assert "calendar_generation_succeeded" not in app.session_state
    assert "calendar_generation_pending" not in app.session_state
    assert "calendar_warnings" not in app.session_state
    assert len(app.get("download_button")) == 0
    assert not any(item.value == "Календарный план готов" for item in app.success)
    assert any("заново" in item.value for item in app.info)


def test_existing_fingerprint_keeps_download_without_regeneration():
    with patch.object(ui, "_generator_revision", return_value="CE2-old"), patch.object(ui, "_LOADED_GENERATOR_REVISION", "CE2-old"):
        app = _generated_app()
        original = app.session_state["calendar_generation_fingerprint"]
        with patch.object(ui, "run_calendar_pipeline") as pipeline:
            app.run()
            app.run()
            pipeline.assert_not_called()
        assert not app.exception
        assert app.session_state["calendar_generation_fingerprint"] == original
        assert app.session_state["calendar_download"].content == b"test-docx"
        assert len(app.get("download_button")) == 1


def test_ce2_revision_change_invalidates_download():
    with patch.object(ui, "_generator_revision", return_value="CE2-old") as revision, patch.object(ui, "_LOADED_GENERATOR_REVISION", "CE2-old"):
        app = _generated_app()
        revision.return_value = "CE2-new"
        with patch.object(ui, "run_calendar_pipeline") as pipeline:
            app.run()
            pipeline.assert_not_called()
        _assert_invalidated(app)
        assert "calendar_context" not in app.session_state


def test_same_filename_changed_bytes_invalidates_download():
    app = _generated_app()
    app.get("file_uploader")[0].set_value((PROGRAM.name, PROGRAM.read_bytes() + b"changed", MIME))
    with patch.object(ui, "run_calendar_pipeline") as pipeline:
        app.run()
        pipeline.assert_not_called()
    _assert_invalidated(app)
    assert "calendar_context" not in app.session_state


def test_academic_year_change_invalidates_analysis_and_download():
    app = _generated_app()
    with patch.object(ui, "run_calendar_pipeline") as pipeline:
        app.number_input[0].set_value(2027).run()
        pipeline.assert_not_called()
    _assert_invalidated(app)
    assert "analysis_ready" not in app.session_state or not app.session_state["analysis_ready"]


@pytest.mark.parametrize("field", [0, 1])
def test_group_or_class_change_invalidates_but_keeps_analysis(field):
    app = _generated_app()
    with patch.object(ui, "run_calendar_pipeline") as pipeline:
        app.text_input[field].set_value("2").run()
        pipeline.assert_not_called()
    _assert_invalidated(app)
    assert app.session_state["analysis_ready"]


def test_legacy_result_without_fingerprint_is_rejected():
    app = _generated_app()
    del app.session_state["calendar_generation_fingerprint"]
    app.run()
    _assert_invalidated(app)


def test_timer_guard_invalidates_code_change_without_generating():
    state = {
        "calendar_generation_inputs": "inputs",
        "calendar_generation_fingerprint": ("inputs", "old"),
        "calendar_download": object(),
        "calendar_generation_succeeded": True,
        "calendar_generation_pending": True,
        "calendar_warnings": ("old",),
        "calendar_ai_usage": object(),
        "calendar_generation_error": "old error",
        "calendar_context": object(),
        "analysis_ready": True,
    }
    with patch.object(ui.st, "session_state", state), patch.object(ui, "_generator_revision", return_value="new"), patch.object(ui.st, "info") as info, patch.object(ui.st, "download_button") as download, patch.object(ui, "run_calendar_pipeline") as pipeline:
        ui._show_generation_result.__wrapped__()
        download.assert_not_called()
        pipeline.assert_not_called()
        info.assert_called_once()
    assert "calendar_download" not in state
    assert "calendar_context" not in state
    assert not state["analysis_ready"]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_each_uploaded_file_is_hashed_by_content(index):
    files = [SimpleNamespace(name="same.docx", getvalue=lambda: b"before") for _ in range(3)]
    before = ui._inputs_fingerprint(*files, "2026–2027", "1", "A")
    files[index] = SimpleNamespace(name="same.docx", getvalue=lambda: b"after")
    assert before != ui._inputs_fingerprint(*files, "2026–2027", "1", "A")


def test_generator_revision_reads_bytes_on_each_call():
    original = Path.read_bytes
    before = ui._generator_revision()
    def changed(path):
        data = original(path)
        return data + b"# new CE2" if path.name == "content_engine_v2.py" else data
    with patch.object(Path, "read_bytes", changed):
        assert ui._generator_revision() != before
    assert ui._generator_revision() == before


def test_changed_code_cannot_regenerate_with_old_imports():
    with patch.object(ui, "_generator_revision", return_value="old") as revision, patch.object(ui, "_LOADED_GENERATOR_REVISION", "old"):
        app = _generated_app()
        revision.return_value = "new"
        app.run()
        with patch.object(ui, "run_calendar_pipeline") as pipeline:
            next(b for b in app.button if b.label == "Проверить документы").click().run()
            pipeline.assert_not_called()
        assert not app.exception
        assert not app.get("download_button")
        assert not any(b.label == "Сформировать календарный план" for b in app.button)
        assert any("Перезапустите" in item.value for item in app.warning)
