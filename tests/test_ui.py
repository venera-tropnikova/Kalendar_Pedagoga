from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_initial_screen_contains_required_controls() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Календарь педагога"
    uploaders = app.get("file_uploader")
    assert len(uploaders) == 3
    assert [uploader.label for uploader in uploaders] == [
        "Загрузите УТП",
        "Загрузите образовательную программу",
        "Шаблон календарного плана вашей организации",
    ]
    assert [uploader.help for uploader in uploaders] == [
        "УТП — учебно-тематический план, DOCX, до 10 МБ.",
        "Программа — образовательная программа, DOC/DOCX, до 10 МБ.",
        "Шаблон — только образец календарного плана организации, DOCX, до 10 МБ.",
    ]
    assert app.selectbox[0].label == "Учебный год"
    assert app.selectbox[0].options == ["2026–2027"]
    assert app.button[0].label == "Проверить документы"


def test_check_requires_utp() -> None:
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    app.button[0].click().run()

    assert not app.exception
    assert app.warning[0].value == "Загрузите УТП."
