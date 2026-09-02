from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document
from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
REFERENCES = Path(__file__).resolve().parents[1] / "references"
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _reference_named(*parts: str) -> Path:
    lowered = tuple(part.casefold() for part in parts)
    for path in REFERENCES.iterdir():
        name = path.name.casefold()
        if all(part in name for part in lowered):
            return path
    raise FileNotFoundError(" ".join(parts))


def _program_file() -> Path:
    return _reference_named("туристы-проводники", "1")


def _template_file() -> Path:
    exact = REFERENCES / "Календарный план.docx"
    if exact.exists():
        return exact
    return _reference_named("календарный план.docx")


def _non_utp_docx() -> bytes:
    document = Document()
    document.add_paragraph("Это не учебно-тематический план.")
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def _upload(app: AppTest, index: int, path: Path) -> None:
    app.get("file_uploader")[index].set_value((path.name, path.read_bytes(), DOCX_MIME))


def _clear_buttons(app: AppTest):
    return [button for button in app.button if button.label == "×"]


def _page_text(app: AppTest) -> str:
    return " ".join(item.value or "" for item in app.markdown)


def _analysis_ready(app: AppTest) -> bool:
    return "analysis_ready" in app.session_state and bool(app.session_state["analysis_ready"])


def _check_button(app: AppTest):
    return next(button for button in app.button if button.label == "Проверить документы")


def test_initial_screen_contains_required_controls() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Календарь педагога"
    uploaders = app.get("file_uploader")
    assert len(uploaders) == 3
    assert [uploader.label for uploader in uploaders] == [
        "Загрузите образовательную программу",
        "Загрузите УТП",
        "Шаблон календарного плана вашей организации",
    ]
    assert [uploader.help for uploader in uploaders] == [
        "Программа — образовательная программа, DOC/DOCX, до 10 МБ.",
        "УТП — учебно-тематический план, DOCX, до 10 МБ.",
        "Шаблон — только образец календарного плана организации, DOCX, до 10 МБ.",
    ]
    assert app.selectbox[0].label == "Учебный год"
    assert app.selectbox[0].options == ["2026–2027"]
    assert app.button[0].label == "Проверить документы"
    notes = " ".join(item.value or "" for item in app.markdown)
    assert "Документ с содержанием программы и, если есть, учебно-тематическим планом" in notes
    assert "Загрузите отдельно, только если УТП находится в другом файле" in notes
    assert "Если есть образец вашей организации — загрузите его; иначе используем стандартный" in notes


def test_check_requires_program() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    app.button[0].click().run()

    assert not app.exception
    assert app.error[0].value == "Загрузите программу обучения."
    assert not _clear_buttons(app)


def test_clear_template_keeps_program() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    program = _program_file()
    template = _template_file()
    _upload(app, 0, program)
    _upload(app, 2, template)
    app.run()

    assert [button.label for button in _clear_buttons(app)] == ["×", "×"]
    _clear_buttons(app)[1].click().run()

    assert not app.exception
    names = " ".join(item.value or "" for item in app.markdown)
    assert program.name in names
    assert template.name not in names
    assert len(_clear_buttons(app)) == 1
    assert app.selectbox[0].value == "2026–2027"


def test_clear_program_resets_analysis_but_keeps_other_files() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    program = _program_file()
    template = _template_file()
    _upload(app, 0, program)
    _upload(app, 2, template)
    app.run()
    _check_button(app).click().run()

    assert _analysis_ready(app) is True
    assert "Документы проверены" in _page_text(app)

    template_nonce = app.session_state["upload_nonce_template"]
    _clear_buttons(app)[0].click().run()

    assert _analysis_ready(app) is False
    assert "calendar_context" not in app.session_state
    assert "Документы проверены" not in _page_text(app)
    assert app.session_state["upload_nonce_program"] == 1
    assert app.session_state["upload_nonce_template"] == template_nonce
    assert app.selectbox[0].value == "2026–2027"


def test_clear_wrong_utp_keeps_program_and_template() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    program = _program_file()
    template = _template_file()
    _upload(app, 0, program)
    app.get("file_uploader")[1].set_value(("wrong-utp.docx", _non_utp_docx(), DOCX_MIME))
    _upload(app, 2, template)
    app.run()

    assert len(_clear_buttons(app)) == 3
    program_nonce = app.session_state["upload_nonce_program"]
    template_nonce = app.session_state["upload_nonce_template"]
    _clear_buttons(app)[1].click().run()

    assert app.session_state["upload_nonce_utp"] == 1
    assert app.session_state["upload_nonce_program"] == program_nonce
    assert app.session_state["upload_nonce_template"] == template_nonce
    assert "wrong-utp.docx" not in _page_text(app)


def test_cleared_slot_accepts_new_upload() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    program = _program_file()
    template = _template_file()
    _upload(app, 0, program)
    _upload(app, 2, template)
    app.run()
    _clear_buttons(app)[1].click().run()

    _upload(app, 2, template)
    app.run()

    names = " ".join(item.value or "" for item in app.markdown)
    assert program.name in names
    assert template.name in names
    assert len(_clear_buttons(app)) == 2


def test_generation_click_runs_pipeline_and_exposes_download() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    _upload(app, 0, _program_file())
    _upload(app, 2, _template_file())
    app.run()
    _check_button(app).click().run()

    generated = SimpleNamespace(
        filename="calendar.docx",
        content=b"generated-docx",
        warnings=(),
        ai_usage=None,
    )
    generate = next(
        button for button in app.button if button.label == "Сформировать календарный план"
    )
    with patch("calendar_pedagoga.ui.run_calendar_pipeline", return_value=generated) as pipeline:
        generate.click().run()

    pipeline.assert_called_once()
    assert app.session_state["calendar_generation_pending"] is False
    assert app.session_state["calendar_generation_succeeded"] is True
    assert app.session_state["calendar_download"].content == b"generated-docx"
    assert [item.value for item in app.success][-1] == "Календарный план готов"
    assert app.get("download_button")[0].label == "Скачать календарный план"
