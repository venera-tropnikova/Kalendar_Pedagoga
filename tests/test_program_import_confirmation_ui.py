"""Universal confirmation UI tested without opening a production server."""
from streamlit.testing.v1 import AppTest

APP = '''
from io import BytesIO
from types import SimpleNamespace
import hashlib
import streamlit as st
from docx import Document
from calendar_pedagoga.program_parsing import parse_program_docx
from calendar_pedagoga.upload_validation import ValidatedUpload, UploadPurpose
from calendar_pedagoga.ui import _render_program_content_confirmation

doc = Document()
for text in ["Содержание курса", "Первый год обучения", "1. Раздел", "Текст А.",
             "Второй год обучения", "1. Раздел", "Текст Б."]:
    doc.add_paragraph(text)
stream = BytesIO()
doc.save(stream)
if "data" not in st.session_state:
    st.session_state["data"] = stream.getvalue()
data = st.session_state["data"]
digest = hashlib.sha256(data).hexdigest()
if "initialized" not in st.session_state:
    st.session_state["initialized"] = True
    st.session_state["program_content_pending"] = {
        "digest": digest,
        "upload": ValidatedUpload(UploadPurpose.PROGRAM, "anonymous.docx", data, parse_program_docx(data)),
    }
current = data + b"changed" if st.session_state.get("changed") else data
_render_program_content_confirmation(SimpleNamespace(getvalue=lambda: current))
'''


def test_confirmation_is_one_group_and_persists():
    app = AppTest.from_string(APP, default_timeout=30).run()
    assert not app.exception
    assert len(app.selectbox) == 1
    assert len(app.button) == 1
    assert "1-й год" in app.selectbox[0].options[0]
    app.selectbox[0].select_index(1).run()
    app.button[0].click().run()
    assert not app.exception
    decision = app.session_state["program_content_decision"]
    assert decision["selection"]
    assert app.session_state["calendar_check_pending"]
    app.run()
    assert app.session_state["program_content_decision"] == decision


def test_changed_source_clears_pending_and_confirmation():
    app = AppTest.from_string(APP, default_timeout=30).run()
    app.session_state["program_content_decision"] = {"digest": "old", "selection": "old"}
    app.session_state["changed"] = True
    app.run()
    assert not app.exception
    assert len(app.selectbox) == 0
    assert "program_content_pending" not in app.session_state
    assert "program_content_decision" not in app.session_state
