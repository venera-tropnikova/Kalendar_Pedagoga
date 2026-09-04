from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document
from streamlit.testing.v1 import AppTest

from calendar_pedagoga.academic_year import default_academic_year_start, format_academic_year
from calendar_pedagoga.practice_slots import SLOT_CONTINUE_WARNING, SLOT_PACK_WARNING
from calendar_pedagoga.ui import _teacher_generation_warnings


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


def _utp_file() -> Path:
    return _reference_named("утп", "ключ")


def _default_year() -> str:
    return format_academic_year(default_academic_year_start())


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
    assert app.number_input[0].label == "Начало учебного года"
    assert int(app.number_input[0].value) == default_academic_year_start()
    assert "2026–2027 / 2027–2028 / 2028–2029" not in _page_text(app)
    assert [item.label for item in app.text_input] == ["Группа №", "Класс", "ФИО педагога"]
    assert all(item.value in {"", None} for item in app.text_input)
    assert not any("ИИ" in (item.label or "") for item in getattr(app, "checkbox", []))
    assert "Дополнить содержание с помощью ИИ" not in _page_text(app)
    assert "Группа Нет" not in _page_text(app)
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
    assert int(app.number_input[0].value) == default_academic_year_start()


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
    assert int(app.number_input[0].value) == default_academic_year_start()


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


def test_utp_year_is_suggested_after_upload() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    _upload(app, 0, _program_file())
    _upload(app, 1, _utp_file())
    app.run()

    assert not app.exception
    assert int(app.number_input[0].value) == 2026
    notices = " ".join(
        [
            _page_text(app),
            " ".join(item.value or "" for item in getattr(app, "info", [])),
            " ".join(item.value or "" for item in getattr(app, "caption", [])),
        ]
    )
    assert "2026–2027" in notices
    assert "УТП" in notices
    assert "2026–2027 / 2027–2028" not in notices


def test_analysis_screen_shows_study_year_from_program_filename() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    _upload(app, 0, _program_file())
    app.run()
    _check_button(app).click().run()

    text = _page_text(app)
    assert not app.exception
    assert "Документы проверены" in text
    assert "Учебный год:</strong> " in text
    assert "Год обучения:</strong> 1 год обучения" in text
    assert "Источник учебного года" not in text
    assert "Возраст:</strong> Не найдено" not in text
    assert "Нормативная и методическая проверка" in text
    assert "Документы закона" in text
    assert "Календарь учреждения" in text
    assert "Сверка ваших часов" in text
    assert "Эта проверка не изменяет Word автоматически." in text
    assert "Часы программы, УТП и плана совпадают." in text
    assert (
        "1–6 сентября и 28–30 декабря — короткие недели, часы на них остаются. "
        "Даты приложение не сдвигает."
    ) in text
    assert "в темах УТП она не найдена" in text
    assert "Срок программы не указан, сравнить год со сроком нельзя." in text
    assert "Федеральные документы" not in text
    assert "Локальная сетка" not in text
    assert "Методическая сверка" not in text
    assert "Это не вывод о соответствии НПА." not in text
    assert "Реестр НПА" not in text
    assert "Федеральные документы, локальная сетка" not in text
    assert "01–06.09" not in text
    assert "28–30.12" not in text
    assert "в календарном плане и УТП" not in text
    assert "нет обоих чисел" not in text
    assert "Год обучения определён." not in text
    assert "Занятия не стоят в каникулярном разрыве" not in text
    assert "Что в порядке" not in text
    assert "PASS" not in text
    assert "NOT CHECKED" not in text
    assert "Данные успешно прочитаны" not in text
    assert "Некоторые поля отсутствуют" not in text
    assert "Что не найдено и как это влияет на Word" not in text
    assert "На файл Word не влияет." not in text
    assert "Недостающие сведения не мешают сформировать календарный план." in text
    assert "На что обратить внимание" in text
    assert "Что не удалось проверить" in text
    captions = " ".join(item.value or "" for item in getattr(app, "caption", []))
    assert "Справочник документов из реестра" in f"{text} {captions}"
    assert not any(button.label == "Заменить документы" for button in app.button)
    assert any(button.label == "Сформировать календарный план" for button in app.button)
    assert "Чтобы заменить документы" in text
    assert not any(
        "Данные успешно прочитаны" in (item.value or "") for item in app.success
    )


def test_generation_click_runs_pipeline_and_exposes_download() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    _upload(app, 0, _program_file())
    _upload(app, 2, _template_file())
    app.run()
    _check_button(app).click().run()

    generated = SimpleNamespace(
        filename="calendar.docx",
        content=b"generated-docx",
        warnings=(
            "Ширина таблицы не задана явно; возможны переносы на новые страницы.",
            SLOT_CONTINUE_WARNING,
            SLOT_PACK_WARNING,
            "Неоднозначное соответствие для «Тема»: вариант А",
        ),
        ai_usage=None,
    )
    assert [item.label for item in app.text_input] == ["Группа №", "Класс", "ФИО педагога"]
    assert not any("ИИ" in (item.label or "") for item in getattr(app, "checkbox", []))
    generate = next(
        button for button in app.button if button.label == "Сформировать календарный план"
    )
    with patch("calendar_pedagoga.ui.run_calendar_pipeline", return_value=generated) as pipeline:
        generate.click().run()

    pipeline.assert_called_once()
    assert pipeline.call_args.kwargs["use_ai"] is False
    assert "ai_provider" not in pipeline.call_args.kwargs
    assert pipeline.call_args.kwargs["group_number"] == ""
    assert pipeline.call_args.kwargs["class_name"] == ""
    assert pipeline.call_args.kwargs["teacher_name"] == ""
    assert pipeline.call_args.kwargs["academic_year"] == _default_year()
    assert "1 г" in (pipeline.call_args.kwargs["program_filename"] or "")
    assert "Дополнить содержание с помощью ИИ" not in _page_text(app)
    assert "Группа Нет" not in _page_text(app)
    assert app.session_state["calendar_generation_pending"] is False
    assert app.session_state["calendar_generation_succeeded"] is True
    assert app.session_state["calendar_download"].content == b"generated-docx"
    assert [item.value for item in app.success][-1] == "Календарный план готов"
    assert app.get("download_button")[0].label == "Скачать календарный план"
    assert "Ширина таблицы" not in _page_text(app)
    assert not any("Ширина таблицы" in (item.value or "") for item in app.warning)
    assert not any("продолжение уже представленного" in (item.value or "") for item in app.warning)
    assert not any("нескольких исходных практических" in (item.value or "") for item in app.warning)
    assert any(
        "Неоднозначное соответствие для «Тема»" in (item.value or "")
        for item in app.warning
    )
    stored = app.session_state["calendar_warnings"]
    assert SLOT_CONTINUE_WARNING in stored
    assert SLOT_PACK_WARNING in stored


def test_teacher_name_is_optional_and_invalidates_download() -> None:
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
    teacher = next(item for item in app.text_input if item.label == "ФИО педагога")
    assert teacher.value in {"", None}

    generate = next(
        button for button in app.button if button.label == "Сформировать календарный план"
    )
    with patch("calendar_pedagoga.ui.run_calendar_pipeline", return_value=generated) as pipeline:
        generate.click().run()
    assert pipeline.call_args.kwargs["teacher_name"] == ""
    assert app.session_state["calendar_generation_succeeded"] is True
    assert app.session_state["calendar_download"].content == b"generated-docx"

    teacher = next(item for item in app.text_input if item.label == "ФИО педагога")
    teacher.set_value("Иванов И.И.").run()
    assert "calendar_generation_invalidated" in app.session_state
    assert app.session_state["calendar_generation_invalidated"]
    assert "calendar_download" not in app.session_state
    assert app.session_state["analysis_ready"] is True

    generate = next(
        button for button in app.button if button.label == "Сформировать календарный план"
    )
    with patch("calendar_pedagoga.ui.run_calendar_pipeline", return_value=generated) as pipeline:
        generate.click().run()
    assert pipeline.call_args.kwargs["teacher_name"] == "Иванов И.И."
    assert app.session_state["calendar_generation_succeeded"] is True


def test_teacher_generation_warnings_hide_only_internal_slot_diagnostics() -> None:
    visible = "Неоднозначное соответствие для «Тема»: вариант А"
    assert _teacher_generation_warnings(
        (
            "Ширина таблицы не задана явно; возможны переносы на новые страницы.",
            SLOT_CONTINUE_WARNING,
            SLOT_PACK_WARNING,
            visible,
        )
    ) == (visible,)
