from datetime import date
import hashlib
import inspect
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document
from streamlit.testing.v1 import AppTest

from calendar_pedagoga import ui
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


def _disputed_program_docx() -> bytes:
    document = Document()
    headings = {
        "Содержание программы 1-го года обучения",
        "1. Введение",
        "1.1 Ансамбль",
        "2. Краеведение",
        "2.1 Моя семья",
        "4. Изобразительное искусство",
        "4.1 Сольфеджио",
    }
    for text in (
        "Дополнительная общеобразовательная программа «Синтетика»",
        "Цель: сформировать навыки.",
        "Задачи: научить основам.",
        "Содержание программы 1-го года обучения",
        "1. Введение",
        "Знакомство с программой.",
        "1.1 Ансамбль",
        "Репетиция состава.",
        "2. Краеведение",
        "Изучение родного края.",
        "2.1 Моя семья",
        "История семьи.",
        "4. Изобразительное искусство",
        "4.1 Сольфеджио",
        "Интервалы и слуховые диктанты.",
    ):
        paragraph = document.add_paragraph()
        run = paragraph.add_run(text)
        if text in headings:
            run.bold = True
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def _disputed_utp_docx() -> bytes:
    document = Document()
    document.add_paragraph("Учебно-тематический план")
    document.add_paragraph("Год обучения: первый")
    document.add_paragraph("Количество часов в неделю: 2")
    document.add_paragraph("Общее количество часов в год: 72")
    document.add_paragraph("36 учебных недель")
    table = document.add_table(rows=8, cols=5)
    headers = ("№", "Тема", "всего", "теория/лекции", "практика")
    for cell, value in zip(table.rows[0].cells, headers, strict=True):
        cell.text = value
    rows = (
        ("1", "Введение", "24", "0", "24"),
        ("1.1", "Ансамбль", "24", "0", "24"),
        ("2", "Краеведение", "24", "0", "24"),
        ("2.1", "Моя семья", "24", "0", "24"),
        ("4", "Изобразительное искусство", "24", "0", "24"),
        ("4.1", "Рисование натюрморта", "24", "0", "24"),
        ("Итого", "", "72", "0", "72"),
    )
    for index, values in enumerate(rows, start=1):
        for cell, value in zip(table.rows[index].cells, values, strict=True):
            cell.text = value
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def _upload(app: AppTest, index: int, path: Path) -> None:
    app.get("file_uploader")[index].set_value((path.name, path.read_bytes(), DOCX_MIME))


def _upload_bytes(app: AppTest, index: int, name: str, data: bytes) -> None:
    app.get("file_uploader")[index].set_value((name, data, DOCX_MIME))


def _upload_disputed(app: AppTest, *, template: bool = False) -> None:
    _upload_bytes(app, 0, "program-synthetic.docx", _disputed_program_docx())
    _upload_bytes(app, 1, "utp-synthetic.docx", _disputed_utp_docx())
    if template:
        _upload(app, 2, _template_file())


def _clear_buttons(app: AppTest):
    return [button for button in app.button if button.label == "×"]


def _page_text(app: AppTest) -> str:
    return " ".join(item.value or "" for item in app.markdown)


def _analysis_ready(app: AppTest) -> bool:
    return "analysis_ready" in app.session_state and bool(app.session_state["analysis_ready"])


def _session_flag(app: AppTest, key: str) -> bool:
    return key in app.session_state and bool(app.session_state[key])


def _has_info(app: AppTest, text: str) -> bool:
    return any(text in (item.value or "") for item in getattr(app, "info", []))


def _check_button(app: AppTest):
    return next(button for button in app.button if button.label == "Проверить документы")


def _fake_generated(**overrides: object) -> SimpleNamespace:
    payload = {
        "filename": "calendar.docx",
        "content": b"generated-docx",
        "warnings": (),
        "ai_usage": None,
        "resolved_lessons": (),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _check_and_resolve(
    app: AppTest,
    generated: SimpleNamespace | None = None,
    *,
    prefer_confirm: bool = True,
):
    result = generated if generated is not None else _fake_generated()
    with patch(
        "calendar_pedagoga.ui.run_calendar_pipeline", return_value=result
    ) as pipeline:
        _check_button(app).click().run()
        _resolve_disputed_matches(app, prefer_confirm=prefer_confirm)
    return pipeline


def _generate_buttons(app: AppTest):
    return [
        button
        for button in app.button
        if button.label in {"Сформировать календарный план", "Сформировать заново"}
    ]


def _resolve_disputed_matches(app: AppTest, *, prefer_confirm: bool = True) -> AppTest:
    for _ in range(40):
        confirms = [
            button
            for button in app.button
            if button.label == "Подтвердить соответствие" and not button.disabled
        ]
        rejects = [
            button for button in app.button if button.label == "Соответствия нет"
        ]
        if not confirms and not rejects:
            return app
        if prefer_confirm and confirms:
            confirms[0].click().run()
            continue
        if rejects:
            rejects[0].click().run()
            continue
        confirms[0].click().run()
    raise AssertionError("остались нерешённые спорные соответствия")


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
    assert any(button.label == "Проверить документы" for button in app.button)
    assert any(button.label == "Открыть календарь" for button in app.button)
    assert "1. Документы" in _page_text(app)
    assert "2. Сведения для плана" in _page_text(app)
    assert f"Календарь {_default_year()} учебного года" in _page_text(app)
    assert "Недели №1–36 соответствуют строкам календарного плана" in _page_text(app)
    notes = " ".join(item.value or "" for item in app.markdown)
    assert "Загрузите отдельно, только если УТП находится в другом файле" in notes
    assert "Если есть образец вашей организации — загрузите его; иначе используем стандартный" in notes


def test_check_requires_program() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    _check_button(app).click().run()

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

    _upload(app, 2, template)
    app.run()
    assert template.name in _page_text(app)
    assert len(_clear_buttons(app)) == 2


def test_clear_program_resets_analysis_but_keeps_other_files() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    program = _program_file()
    template = _template_file()
    _upload(app, 0, program)
    _upload(app, 2, template)
    app.run()
    _check_and_resolve(app)

    assert _analysis_ready(app) is True
    assert "calendar_download" in app.session_state

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
    _check_and_resolve(app)

    text = _page_text(app)
    assert not app.exception
    assert "Календарный план" in text
    assert _default_year() in text
    assert "1 год обучения" in text
    assert "Программа распознана" in text
    assert "УТП соответствует программе" in text
    assert "Часы совпадают: 72" in text
    assert "Учебный год определён" in text
    assert "замечан" in text
    assert any(
        item.label == "Что проверено и какие есть замечания"
        for item in app.expander
    )
    assert any(button.label == "Изменить данные" for button in app.button)
    assert not _generate_buttons(app)
    assert app.get("download_button")[0].label == (
        f"Скачать календарный план за {_default_year()} учебный год"
    )
    assert "Нормативная и методическая проверка" in text
    assert "Документы закона" in text
    assert "Календарь учреждения" in text
    assert "Сверка ваших часов" in text
    assert "Код приложения обновлён" not in text
    assert "Перезапустите приложение" not in text

def test_calendar_uses_the_scheduling_grid_and_keeps_short_weeks_in_note() -> None:
    weeks = ui.build_academic_weeks(_default_year())
    study, short, breaks = ui._academic_day_sets(_default_year())
    calendar_html = ui._academic_calendar_html(_default_year())

    assert [week.number for week in weeks] == list(range(1, 37))
    assert ui._calendar_day_class(weeks[0].start, study, short, breaks) == "kp-cal-study"
    assert ui._calendar_day_class(weeks[0].end, study, short, breaks) == "kp-cal-wknd"
    assert (
        ui._calendar_day_class(date(2026, 10, 26), study, short, breaks)
        == "kp-cal-break"
    )
    assert "kp-cal-short" not in calendar_html
    assert "№1" in ui._short_weeks_note(_default_year())
    assert "№18" in ui._short_weeks_note(_default_year())
    assert len(ui._TEACHER_LINKS) >= 2


def test_calendar_week_before_generation_requests_calendar_plan() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    next(
        button for button in app.button if button.label == "Открыть календарь"
    ).click().run()

    week_buttons = [button for button in app.button if button.label.startswith("№")]
    assert len(week_buttons) == 36
    assert [button.label for button in week_buttons] == [f"№{number}" for number in range(1, 37)]

    week_buttons[1].click().run()
    assert not app.exception
    assert any(
        "Сформируйте календарный план" in (item.value or "")
        for item in app.info
    )


def test_calendar_month_before_generation_requests_calendar_plan() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    next(
        button for button in app.button if button.label == "Открыть календарь"
    ).click().run()

    month_button = next(
        button for button in app.button if button.label == "Октябрь 2026"
    )
    assert month_button.help == "Открыть план на месяц"
    month_button.click().run()

    assert not app.exception
    assert "Октябрь 2026" in _page_text(app)
    assert "Недели №6–№9" in _page_text(app)
    assert any(
        "Сначала сформируйте календарный план."
        in (item.value or "")
        for item in app.info
    )
    assert not any(
        item.label.startswith("Скачать план на")
        for item in app.get("download_button")
    )


def test_long_week_topic_caption_keeps_every_source_topic() -> None:
    parts = (
        SimpleNamespace(topic_number="4", topic_title="Подготовка маршрута"),
        SimpleNamespace(topic_number="5", topic_title="Работа с картой"),
    )
    row = SimpleNamespace(
        source=SimpleNamespace(
            source=SimpleNamespace(
                week_parts=parts,
                topic_number="4",
                topic_title="Подготовка маршрута",
            )
        )
    )

    assert ui._week_topic_caption(row) == (
        "4. Подготовка маршрута; 5. Работа с картой"
    )

def test_calendar_marks_confirmed_holidays_and_uses_plain_language_legend() -> None:
    study, short, breaks = ui._academic_day_sets(_default_year())

    assert (
        ui._calendar_day_class(date(2026, 11, 4), study, short, breaks)
        == "kp-cal-holiday"
    )
    assert (
        ui._calendar_day_class(date(2027, 1, 1), study, short, breaks)
        == "kp-cal-holiday"
    )
    source = Path(ui.__file__).read_text(encoding="utf-8")
    assert "Красный — выходной / официальный праздник" in source
    assert "Бежевый — рекомендуемые каникулы / перерыв" in source
    assert "переносы выходных 2027 года не размечены" in source
    html_row = ui._calendar_day_row_html(
        year=2026, month=11, day_numbers=[2, 3, 4, 5, 6, 7, 8],
        study=study, short=short, breaks=breaks,
    )
    assert 'title=\"День народного единства\"' in html_row
    january_row = ui._calendar_day_row_html(
        year=2027, month=1, day_numbers=[4, 5, 6, 7, 8, 9, 10],
        study=study, short=short, breaks=breaks,
    )
    assert 'title=\"Рождество Христово\"' in january_row
    assert january_row.count("kp-cal-holiday") == 5
    assert 'data-calendar-week-header=\"numero\"' in source
    header = ui._calendar_days_header_html()
    assert ui._CALENDAR_WEEK_HEADER == "\N{NUMERO SIGN}"
    assert ord(ui._CALENDAR_WEEK_HEADER) == 0x2116
    assert header.startswith(
        '<div class="kp-cal-days-head" lang="ru" translate="no">'
        '<span class="kp-cal-week-head notranslate" translate="no" '
        'data-calendar-week-header="numero" aria-label="№ нед.">&#8470; нед.</span>'
    )
    assert "Нет" not in header
    assert "None" not in header
    assert "False" not in header
    assert (
        ui._calendar_day_class(date(2026, 10, 5), study, short, breaks)
        == "kp-cal-professional"
    )
    teacher_day = ui._calendar_day_row_html(
        year=2026, month=10, day_numbers=[5, 6, 7, 8, 9, 10, 11],
        study=study, short=short, breaks=breaks,
    )
    assert 'title="5 октября — День учителя"' in teacher_day
    assert 'data-professional="5 октября — День учителя"' in teacher_day
    assert "Профессиональная дата" in source
    recommended = ui._recommended_school_breaks(_default_year())
    assert date(2026, 10, 26) in recommended
    assert date(2026, 11, 3) in recommended
    assert date(2026, 12, 31) in recommended
    assert date(2027, 1, 10) in recommended
    assert date(2027, 3, 27) in recommended
    assert date(2027, 4, 4) in recommended
    assert date(2027, 5, 27) in recommended
    assert date(2027, 8, 31) in recommended
    assert date(2027, 2, 15) not in recommended
    first_class = ui._recommended_school_breaks(_default_year(), "1 класс")
    assert date(2027, 2, 15) in first_class
    assert date(2027, 2, 21) in first_class
    assert (
        ui._calendar_day_class(date(2026, 10, 31), study, short, breaks)
        == "kp-cal-break kp-cal-wknd"
    )


def test_recommended_break_cards_reuse_confirmed_calendar_periods() -> None:
    cards_html = ui._recommended_break_cards_html(_default_year())

    assert "Рекомендуемые школьные каникулы" in cards_html
    assert cards_html.count('class="kp-cal-vacation-card"') == 4
    assert "26 октября — 3 ноября 2026" in cards_html
    assert "31 декабря 2026 — 10 января 2027" in cards_html
    assert "27 марта — 4 апреля 2027" in cards_html
    assert "27 мая — 31 августа 2027" in cards_html
    assert "Дополнительные каникулы для 1 класса" not in cards_html

    first_class_html = ui._recommended_break_cards_html(
        _default_year(), "1 класс"
    )
    assert (
        "Дополнительные каникулы для 1 класса: 15–21 февраля 2027"
        in first_class_html
    )


def test_month_detail_uses_scheduling_months_and_ready_snapshot_rows() -> None:
    september = ui._month_weeks(_default_year(), 2026, 9)
    october = ui._month_weeks(_default_year(), 2026, 10)
    january = ui._month_weeks(_default_year(), 2027, 1)
    may = ui._month_weeks(_default_year(), 2027, 5)

    assert [week.number for week in september] == [1, 2, 3, 4, 5]
    assert [week.number for week in october] == [6, 7, 8, 9]
    assert [week.number for week in january] == [19, 20, 21]
    assert [week.number for week in may] == [35, 36]

    row6 = ("05–11.10", "6. Тема", "Теория", "Практика", "Тип", "Результат", "Контроль", "")
    row9 = ("26.10–01.11", "9. Тема", "Теория", "Практика", "Тип", "Результат", "Контроль", "")
    rows = ui._month_detail_rows({6: (row6,), 9: (row9,)}, october)

    assert rows == (("№6", *row6), ("№9", *row9))


def test_monthly_docx_is_an_unchanged_subset_of_the_annual_docx(monkeypatch) -> None:
    # This is a content-projection test; renderer boundaries have separate tests.
    monkeypatch.setattr(ui, 'detect_data_row_page_spans', lambda *args, **kwargs: None)
    annual_path = (
        REFERENCES
        / "Календарный_план_Туристы_проводники_3_год_2026-2027_Верно.docx"
    )
    annual_content = annual_path.read_bytes()
    annual_hash = hashlib.sha256(annual_content).hexdigest()

    def rows_by_week(content: bytes) -> dict[int, tuple[str, ...]]:
        document = Document(BytesIO(content))
        table = document.tables[0]
        columns = ui._columns_for_table(table)
        rows: dict[int, tuple[str, ...]] = {}
        for row in table.rows[2:]:
            first_line = row.cells[columns.week].text.splitlines()[:1]
            try:
                number = int(first_line[0].strip()) if first_line else None
            except ValueError:
                number = None
            if number is not None:
                rows[number] = tuple(cell.text for cell in row.cells)
        return rows

    annual_rows = rows_by_week(annual_content)
    for year, month, expected in (
        (2026, 9, {1, 2, 3, 4, 5}),
        (2026, 10, {6, 7, 8, 9}),
        (2027, 1, {19, 20, 21}),
        (2027, 5, {35, 36}),
    ):
        monthly_content = ui._monthly_plan_docx(
            annual_content, _default_year(), year, month
        )
        monthly_rows = rows_by_week(monthly_content)
        assert set(monthly_rows) == expected
        assert monthly_rows == {
            number: annual_rows[number] for number in sorted(expected)
        }

    assert hashlib.sha256(annual_content).hexdigest() == annual_hash


def test_week_detail_projects_every_ready_plan_row_without_inventing_mark() -> None:
    def row(topic: str, theory: str, practice: str) -> SimpleNamespace:
        return SimpleNamespace(
            source=SimpleNamespace(
                source=SimpleNamespace(
                    week_number=19,
                    date_range="11–17 января",
                    week_parts=(),
                    topic_number="5.1",
                    topic_title=topic,
                )
            ),
            theory_text=theory,
            practice_text=practice,
            lesson_type="Комбинированное занятие",
            planned_result="Планируемый результат",
            assessment_method="Практическое задание",
        )

    rows = ui._week_detail_rows(
        (
            row("Длинная тема, часть 1", "Теория 1", "Практика 1"),
            row("Длинная тема, часть 2", "Теория 2", "Практика 2"),
        ),
        19,
    )

    assert len(rows) == 2
    assert rows[0][:4] == (
        "11–17 января",
        "5.1. Длинная тема, часть 1",
        "Теория 1",
        "Практика 1",
    )
    assert rows[1][1] == "5.1. Длинная тема, часть 2"
    assert all(item[-1] == "" for item in rows)

def test_generation_click_runs_pipeline_and_exposes_download() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    _upload(app, 0, _program_file())
    _upload(app, 2, _template_file())
    app.run()
    generated = SimpleNamespace(
        filename="calendar.docx",
        content=b"generated-docx",
        warnings=(
            "Ширина таблицы не задана явно; возможны переносы на новые страницы.",
            SLOT_CONTINUE_WARNING,
            SLOT_PACK_WARNING,
            "Неоднозначное соответствие для «Тема»: вариант А",
            "Недостаточно данных источника; использован безопасный fallback.",
            "Безопасный шаблон CE2: unproven_object_case.",
        ),
        ai_usage=None,
    )
    assert [item.label for item in app.text_input] == ["Группа №", "Класс", "ФИО педагога"]
    assert not any("ИИ" in (item.label or "") for item in getattr(app, "checkbox", []))
    pipeline = _check_and_resolve(app, generated)

    pipeline.assert_called_once()
    assert pipeline.call_args.kwargs["match_reviews"] == app.session_state["match_reviews"]
    assert pipeline.call_args.kwargs["use_ai"] is False
    assert "ai_provider" not in pipeline.call_args.kwargs
    assert pipeline.call_args.kwargs["group_number"] == ""
    assert pipeline.call_args.kwargs["class_name"] == ""
    assert pipeline.call_args.kwargs["teacher_name"] == ""
    assert pipeline.call_args.kwargs["academic_year"] == _default_year()
    assert "1 г" in (pipeline.call_args.kwargs["program_filename"] or "")
    assert "Дополнить содержание с помощью ИИ" not in _page_text(app)
    assert "Группа Нет" not in _page_text(app)
    assert "calendar_generation_pending" not in app.session_state
    assert app.session_state["calendar_generation_succeeded"] is True
    assert app.session_state["calendar_download"].content == b"generated-docx"
    assert "Календарный план сформирован с замечаниями" in _page_text(app)
    assert "✓ Календарный план готов" not in _page_text(app)
    assert app.get("download_button")[0].label == (
        f"Скачать календарный план за {_default_year()} учебный год"
    )
    assert not _generate_buttons(app)
    assert "Ширина таблицы" not in _page_text(app)
    assert not any("Ширина таблицы" in (item.value or "") for item in app.warning)
    assert not any("продолжение уже представленного" in (item.value or "") for item in app.warning)
    assert not any("нескольких исходных практических" in (item.value or "") for item in app.warning)
    assert not any(
        "Неоднозначное соответствие для «Тема»" in (item.value or "")
        for item in app.warning
    )
    assert not any("Недостаточно данных источника" in (item.value or "") for item in app.warning)
    assert not any("Некоторые формулировки" in (item.value or "") for item in app.warning)
    assert not any("Не найден отдельный блок" in (item.value or "") for item in app.warning)
    assert "Неоднозначное соответствие для «Тема»" in _page_text(app)
    assert "Недостаточно данных источника" in _page_text(app)
    assert "Некоторые формулировки автоматически приведены" in _page_text(app)
    assert "не мешают формированию" in _page_text(app)
    assert any(
        item.label == "Что проверено и какие есть замечания"
        for item in app.expander
    )
    assert callable(pipeline.call_args.kwargs.get("on_progress"))
    stored = app.session_state["calendar_warnings"]
    assert SLOT_CONTINUE_WARNING in stored
    assert SLOT_PACK_WARNING in stored

    with patch("calendar_pedagoga.ui.run_calendar_pipeline") as rerun_pipeline:
        app.run()
        rerun_pipeline.assert_not_called()
    assert app.session_state["calendar_download"].content == b"generated-docx"

    next(button for button in app.button if button.label == "Изменить данные").click().run()
    assert "calendar_download" not in app.session_state
    assert "calendar_generation_succeeded" not in app.session_state
    assert len(app.get("download_button")) == 0
    assert not _generate_buttons(app)


def test_generated_plan_survives_calendar_and_week_click_reruns() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    _upload(app, 0, _program_file())
    _upload(app, 2, _template_file())
    app.run()
    source = SimpleNamespace(
        week_number=19,
        date_range="11–17.01",
        week_parts=(),
        topic_number="4.2",
        topic_title="Аптечка",
    )
    resolved = SimpleNamespace(
        source=SimpleNamespace(source=source),
        theory_text="Фактическая теория",
        practice_text="Фактическая практика",
        lesson_type="Комбинированное занятие",
        planned_result="Фактический результат",
        assessment_method="Практическое задание",
    )
    october_source = SimpleNamespace(
        week_number=6,
        date_range="05–11.10",
        week_parts=(),
        topic_number="2.1",
        topic_title="Октябрьская тема",
    )
    october_resolved = SimpleNamespace(
        source=SimpleNamespace(source=october_source),
        theory_text="Октябрьская теория",
        practice_text="Октябрьская практика",
        lesson_type="Практическое занятие",
        planned_result="Октябрьский результат",
        assessment_method="Октябрьский контроль",
    )
    generated = SimpleNamespace(
        filename="calendar.docx",
        content=b"generated-docx",
        warnings=(),
        ai_usage=None,
        resolved_lessons=(october_resolved, resolved),
    )
    _check_and_resolve(app, generated)

    open_buttons = [
        button for button in app.button
        if button.label == "Открыть календарь"
    ]
    assert len(open_buttons) == 1
    with patch(
        "calendar_pedagoga.ui._monthly_plan_docx",
        return_value=b"monthly-docx",
    ):
        open_buttons[0].click().run()
        next(
            button for button in app.button if button.label == "Октябрь 2026"
        ).click().run()

        month_text = _page_text(app)
        assert "Сформируйте календарный план" not in month_text
        assert "Недели №6–№9" in month_text
        assert "Октябрьская теория" in month_text
        assert "Октябрьская практика" in month_text
        assert "Октябрьский результат" in month_text
        assert "Октябрьский контроль" in month_text
        assert any(
            button.label == "Скачать план на октябрь 2026"
            for button in app.get("download_button")
        )

        next(
            button for button in app.button if button.label == "← К календарю"
        ).click().run()
        next(button for button in app.button if button.label == "№19").click().run()

    text = _page_text(app)
    assert "Сформируйте календарный план" not in text
    assert "Неделя №19 · 11–17 января" in text
    assert "Фактическая теория" in text
    assert "Фактическая практика" in text
    assert "Фактический результат" in text
    assert "Практическое задание" in text
    assert app.session_state["calendar_resolved_lessons"] == (
        october_resolved,
        resolved,
    )

def test_teacher_name_is_optional_and_invalidates_download() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    _upload(app, 0, _program_file())
    _upload(app, 2, _template_file())
    app.run()
    generated = _fake_generated()
    teacher = next(item for item in app.text_input if item.label == "ФИО педагога")
    assert teacher.value in {"", None}

    pipeline = _check_and_resolve(app, generated)
    assert pipeline.call_args.kwargs["teacher_name"] == ""
    assert app.session_state["calendar_generation_succeeded"] is True
    assert app.session_state["calendar_download"].content == b"generated-docx"

    teacher = next(item for item in app.text_input if item.label == "ФИО педагога")
    teacher.set_value("Иванов И.И.").run()
    assert "calendar_generation_invalidated" in app.session_state
    assert app.session_state["calendar_generation_invalidated"]
    assert "calendar_download" not in app.session_state
    assert app.session_state["analysis_ready"] is True
    assert not _generate_buttons(app)
    assert len(app.get("download_button")) == 0

    pipeline = _check_and_resolve(app, generated)
    assert pipeline.call_args.kwargs["teacher_name"] == "Иванов И.И."
    assert app.session_state["calendar_generation_succeeded"] is True


def test_teacher_generation_warnings_hide_internal_diagnostics_and_collapse_ce2(
    caplog,
) -> None:
    visible = "Неоднозначное соответствие для «Тема»: вариант А"
    with caplog.at_level("INFO", logger="calendar_pedagoga.ui"):
        shown = _teacher_generation_warnings(
            (
                "Ширина таблицы не задана явно; возможны переносы на новые страницы.",
                SLOT_CONTINUE_WARNING,
                "Безопасный шаблон CE2: broken_clause_join.",
                "Безопасный шаблон CE2: unproven_object_case.",
                "Безопасный шаблон CE2: broken_clause_join.",
                SLOT_PACK_WARNING,
                visible,
            )
        )
    assert shown == (
        visible,
        "Некоторые формулировки автоматически приведены "
        "к безопасному нейтральному виду.",
    )
    assert "broken_clause_join, unproven_object_case" in caplog.text
    assert all(
        code not in " ".join(shown)
        for code in ("broken_clause_join", "unproven_object_case")
    )


def test_check_button_disabled_while_busy() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    app.session_state["calendar_busy"] = True
    app.session_state["calendar_generate_after_check"] = True
    app.run()
    assert _check_button(app).disabled


def test_check_button_enabled_for_required_and_optional_uploads() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    program = _program_file()
    utp = _utp_file()
    template = _template_file()

    _upload(app, 0, program)
    app.run()
    assert not _check_button(app).disabled

    _upload(app, 1, utp)
    app.run()
    assert not _check_button(app).disabled
    assert not _has_info(app, "Проверяем документы…")

    _upload(app, 2, template)
    app.run()
    assert not _check_button(app).disabled
    assert not _session_flag(app, "calendar_busy")


def test_check_status_visible_while_in_flight() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    _upload(app, 0, _program_file())
    app.run()
    app.session_state["calendar_busy"] = True
    app.session_state["calendar_generate_after_check"] = True
    app.session_state["calendar_work_status"] = "Проверяем документы…"
    app.run()
    assert _check_button(app).disabled
    banners = [
        item.value or ""
        for item in app.markdown
        if 'role="status"' in (item.value or "")
    ]
    assert len(banners) == 1
    assert "Проверяем документы…" in banners[0]
    assert not _has_info(app, "Проверяем документы…")


def test_check_error_reenables_button() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    _upload(app, 0, _program_file())
    _upload_bytes(app, 1, "wrong-utp.docx", _non_utp_docx())
    app.run()
    _check_button(app).click().run()
    assert not app.exception
    assert app.error
    assert not _check_button(app).disabled
    assert not _session_flag(app, "calendar_busy")
    assert not _session_flag(app, "calendar_check_pending")


def test_file_change_clears_stale_busy_and_enables_check() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    _upload(app, 0, _program_file())
    app.run()
    app.session_state["calendar_busy"] = True
    app.session_state["calendar_work_status"] = "Проверяем документы…"
    _upload(app, 1, _utp_file())
    app.run()
    assert not app.exception
    assert not _check_button(app).disabled
    assert not _session_flag(app, "calendar_busy")
    assert not _has_info(app, "Проверяем документы…")


def test_second_click_while_busy_does_not_start_another_generation() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    _upload(app, 0, _program_file())
    app.run()
    generated = _fake_generated()
    with patch("calendar_pedagoga.ui.run_calendar_pipeline", return_value=generated) as pipeline:
        _check_button(app).click().run()
        app.session_state["calendar_busy"] = True
        _check_button(app).click().run()
        _resolve_disputed_matches(app)
    pipeline.assert_called_once()
    assert app.session_state["calendar_download"].content == b"generated-docx"


def test_unresolved_disputed_matches_block_generation() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    _upload_disputed(app, template=True)
    app.run()
    with patch("calendar_pedagoga.ui.run_calendar_pipeline") as pipeline:
        _check_button(app).click().run()
        pipeline.assert_not_called()

    assert "Документы проверены" not in _page_text(app)
    assert "Нужно сопоставить темы" in _page_text(app)
    assert not _generate_buttons(app)
    assert len(app.get("download_button")) == 0
    assert "calendar_download" not in app.session_state


def test_rejected_matches_allow_generation_with_remarks() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    _upload_disputed(app, template=True)
    app.run()
    _check_and_resolve(app, prefer_confirm=False)

    text = _page_text(app)
    assert "Календарный план сформирован с замечаниями" in text
    assert "без связанного содержания программы" in text
    assert "✓ Календарный план готов" not in text
    assert not _generate_buttons(app)
    assert app.get("download_button")[0].label == (
        f"Скачать календарный план за {_default_year()} учебный год"
    )


def test_file_change_resets_match_reviews() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    program = _disputed_program_docx()
    _upload_bytes(app, 0, "program-synthetic.docx", program)
    _upload_bytes(app, 1, "utp-synthetic.docx", _disputed_utp_docx())
    app.run()
    _check_and_resolve(app)
    assert "match_reviews" in app.session_state
    assert app.session_state["match_reviews"]

    app.get("file_uploader")[0].set_value(
        ("program-synthetic.docx", program + b"changed", DOCX_MIME)
    )
    app.run()

    assert "match_reviews" not in app.session_state
    assert "match_reviews_scope" not in app.session_state


def test_group_and_teacher_keep_match_reviews() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    _upload(app, 0, _program_file())
    app.run()
    _check_and_resolve(app)
    reviews = dict(app.session_state["match_reviews"])
    scope = app.session_state["match_reviews_scope"]

    next(item for item in app.text_input if item.label == "Группа №").set_value("5").run()
    assert app.session_state["match_reviews"] == reviews
    assert app.session_state["match_reviews_scope"] == scope

    next(item for item in app.text_input if item.label == "ФИО педагога").set_value(
        "Сидоров С.С."
    ).run()
    assert app.session_state["match_reviews"] == reviews
    assert app.session_state["match_reviews_scope"] == scope
    assert app.session_state["analysis_ready"] is True


def test_missing_content_notice_does_not_ask_confirm_or_reject() -> None:
    from calendar_pedagoga.match_review import MISSING_PROGRAM_CONTENT_NOTICE
    from calendar_pedagoga.matching import ContentMatch, MatchStatus
    from calendar_pedagoga.parsing import Hours, Topic
    from calendar_pedagoga.program_parsing import ProgramData

    halt = Topic("4.1", "Рисование натюрморта", Hours(2, 0, 2), "ИЗО")
    keep = Topic("4.2", "Сольфеджио", Hours(2, 0, 2), "ИЗО")
    matches = (
        ContentMatch(halt, None, MatchStatus.NOT_MATCHED, 0.0),
        ContentMatch(keep, None, MatchStatus.EXACT, 1.0),
    )
    program = ProgramData(
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
        content_items=(),
    )
    infos: list[str] = []
    labels: list[str] = []
    with (
        patch.object(ui.st, "info", side_effect=lambda text, **_: infos.append(text)),
        patch.object(ui.st, "markdown", lambda *_, **__: None),
        patch.object(
            ui.st,
            "button",
            side_effect=lambda label, **_: labels.append(label) or False,
        ),
    ):
        ui._render_missing_content_notices(matches)
        ui._render_match_review_cards(matches, program, "scope")

    assert MISSING_PROGRAM_CONTENT_NOTICE in infos
    assert infos.count(MISSING_PROGRAM_CONTENT_NOTICE) == 1
    assert "Подтвердить соответствие" not in labels
    assert "Соответствия нет" not in labels


def test_identical_missing_content_notice_is_not_duplicated() -> None:
    from calendar_pedagoga.match_review import MISSING_PROGRAM_CONTENT_NOTICE
    from calendar_pedagoga.matching import ContentMatch, MatchStatus
    from calendar_pedagoga.parsing import Hours, Topic

    matches = (
        ContentMatch(Topic("4.1", "Рисование", Hours(2, 0, 2), "ИЗО"), None, MatchStatus.NOT_MATCHED, 0.0),
        ContentMatch(Topic("4.2", "Лепка", Hours(2, 0, 2), "ИЗО"), None, MatchStatus.NOT_MATCHED, 0.0),
    )
    infos: list[str] = []
    with (
        patch.object(ui.st, "info", side_effect=lambda text, **_: infos.append(text)),
        patch.object(ui.st, "markdown", lambda *_, **__: None),
    ):
        ui._render_missing_content_notices(matches)

    assert infos == [MISSING_PROGRAM_CONTENT_NOTICE]


def test_year_conflict_block_does_not_generate() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    _upload(app, 0, _program_file())
    _upload(app, 1, REFERENCES / "УТП ТП 3г. 2ч.docx")
    app.run()
    with patch("calendar_pedagoga.ui.run_calendar_pipeline") as pipeline:
        _check_button(app).click().run()
        pipeline.assert_not_called()

    assert not app.exception
    assert any("противоречат" in (item.value or "") for item in app.error)
    assert not _check_button(app).disabled
    assert not _session_flag(app, "calendar_busy")
    assert len(app.get("download_button")) == 0
    assert not _generate_buttons(app)
    assert "calendar_download" not in app.session_state
    assert not _analysis_ready(app)


def test_generation_failure_hides_download() -> None:
    from calendar_pedagoga.pipeline import PipelineError

    app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    _upload(app, 0, _program_file())
    app.run()
    with patch(
        "calendar_pedagoga.ui.run_calendar_pipeline",
        side_effect=PipelineError("DOCX не прошёл QA: overflow"),
    ) as pipeline:
        _check_button(app).click().run()
        _resolve_disputed_matches(app)
        assert pipeline.called
    assert len(app.get("download_button")) == 0
    assert any(
        "Не удалось сформировать календарный план" in (item.value or "")
        for item in app.error
    )
    assert "calendar_download" not in app.session_state
    assert not _generate_buttons(app)


def test_analysis_uses_pipeline_ce2_and_not_ce1() -> None:
    source = inspect.getsource(ui)
    ce1_call = "build_lesson_content" + "("
    assert ce1_call not in source
    assert "_build_pipeline_lesson_content" in source
    normative = inspect.getsource(ui._lesson_views_for_normative)
    assert "build_lesson_content_v2" not in normative
    assert "row.lesson_type" in normative
