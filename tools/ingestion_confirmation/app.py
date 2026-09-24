"""Explicit QA entry point; never imported or launched by production."""
import os
import streamlit as st
from calendar_pedagoga.ingestion_confirmation import build_model
from calendar_pedagoga.ingestion_confirmation.ui import render_shadow

st.set_page_config(page_title='Подтверждение структуры', layout='wide')
st.title('Подтверждение структуры программы')
enabled = os.environ.get('KP_INGESTION_CONFIRMATION_SHADOW') == '1'
if not enabled:
    st.info('Проверка структуры отключена.')
    st.stop()

# Tests inject exactly the same model consumed after uploads. No corpus routing.
model = st.session_state.get('review_model')
if model is None:
    from calendar_pedagoga.lossless_document import extract_bytes
    from calendar_pedagoga.structural_interpretation import interpret_document
    program = st.file_uploader('Программа', type=None, key='program')
    external = st.file_uploader('Учебный план отдельным файлом (если есть)', type=None, key='external')
    if program is None:
        st.info('Загрузите программу для проверки структуры.')
        st.stop()
    def interpret(data, slot):
        from hashlib import sha256
        digest = sha256(data).hexdigest()
        previous = st.session_state.get('extraction:' + slot)
        if previous and previous[0] == digest:
            return previous[1]
        with st.spinner('Читаем структуру документа…'):
            document = interpret_document(extract_bytes(data))
        st.session_state['extraction:' + slot] = (digest, document)
        return document
    try:
        model = build_model(interpret(program.getvalue(), 'program'), interpret(external.getvalue(), 'external') if external else None)
    except (ValueError, RuntimeError) as error:
        st.error(str(error))
        st.stop()
render_shadow(model, enabled=enabled)
