"""Canonical upload, Stage C confirmation, and the Stage E calendar route."""
from __future__ import annotations

from hashlib import sha256

_BLOCKED = {
    "UNCONFIRMED": "Сначала подтвердите структуру. Календарь не сформирован.",
    "YEAR_AMBIGUOUS": "Выберите год обучения. Календарь не сформирован.",
    "PLAN_AMBIGUOUS": "Выберите учебный план. Календарь не сформирован.",
    "COLUMN_AMBIGUOUS": "Уточните колонки учебного плана. Календарь не сформирован.",
    "HOURS_CONFLICT": "Часы в плане не согласованы. Календарь не сформирован.",
    "CONTENT_BOUNDARY_AMBIGUOUS": "Уточните границы содержания. Календарь не сформирован.",
    "BINDING_AMBIGUOUS": "Подтвердите связи тем и разделов. Календарь не сформирован.",
    "SOURCE_CONFLICT": "Источники плана различаются. Выберите один. Календарь не сформирован.",
    "AMBIGUOUS_BINDING": "Связь темы и раздела неоднозначна. Календарь не сформирован.",
    "SEMANTIC_COLLAPSE": "Различающиеся фрагменты нельзя свести в одну формулировку. Календарь не сформирован.",
    "DOWNSTREAM_DEFECT": "Содержание не прошло проверку готовности. Календарь не сформирован.",
    "MISSING_EXPLICIT_WEEKS": "В подтверждённом плане нет единственного числа учебных недель. Календарь не сформирован.",
    "WORKLOAD_MISMATCH": "Число недель не сходится с часами плана. Календарь не сформирован.",
    "FRACTIONAL_HOUR_GRID": "В плане есть дробные часы. Календарь не сформирован.",
    "SCHEDULE": "Недели и часы не сходятся с подтверждённым планом. Календарь не сформирован.",
    "DOCX_QA": "Документ не прошёл проверку оформления. Календарь не сформирован.",
    "DOCX_MODEL_MISMATCH": "Файл не совпал с календарной моделью. Календарь не сформирован.",
}


def blocked_message(error) -> str:
    return _BLOCKED.get(error.code, "Структуру нельзя использовать для календаря. Календарь не сформирован.")


def render_canonical_flow() -> None:
    import streamlit as st

    from calendar_pedagoga.academic_year import default_academic_year_start, format_academic_year
    from calendar_pedagoga.ingestion_confirmation import assess, State
    from calendar_pedagoga.ingestion_confirmation.ui import render_confirmation
    from calendar_pedagoga.ingestion_shadow import ShadowBlocked, run_shadow

    st.title("Календарь педагога")
    st.session_state.setdefault("canonical_year_start", default_academic_year_start())
    year_start = st.number_input(
        "Начало учебного года календаря",
        min_value=2000,
        max_value=2100,
        step=1,
        key="canonical_year_start",
    )
    academic_year = format_academic_year(int(year_start))
    model = _model(st)
    if model is None:
        st.info("Загрузите программу. Отдельный учебный план нужен только если его нет в программе.")
        return
    session = _session(st, model)
    updated = render_confirmation(session)
    if updated is not session:
        _store(st, model, updated)
        st.rerun()
    view = assess(updated)
    if not updated.confirmed or view.state != State.VALID:
        return
    cache_key = model.fingerprint + ":" + academic_year + ":" + str(len(updated.events))
    cached = st.session_state.get("canonical_docx")
    if not cached or cached[0] != cache_key:
        try:
            result = run_shadow(updated, academic_year=academic_year, publish=True)
        except ShadowBlocked as error:
            st.session_state.pop("canonical_docx", None)
            st.error(blocked_message(error))
            return
        st.session_state["canonical_docx"] = (cache_key, result.docx)
        docx = result.docx
    else:
        docx = cached[1]
    st.download_button(
        f"Скачать календарный план за {academic_year} учебный год",
        data=docx,
        file_name=f"kalendarny-plan-{academic_year}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        type="primary",
        use_container_width=True,
    )


def _model(st):
    program = st.file_uploader("Программа", type=None, key="canonical_program")
    external = st.file_uploader(
        "Учебный план отдельным файлом (если есть)",
        type=None,
        key="canonical_external",
    )
    manual = st.file_uploader(
        "План отдельным документом, если его нет в программе",
        type=None,
        key="canonical_manual",
    )
    if program is None and external is None and manual is None:
        return st.session_state.get("canonical_model")
    if program is None:
        st.error("Загрузите программу. Календарь не сформирован.")
        return None
    try:
        from calendar_pedagoga.ingestion_confirmation import build_model

        return build_model(
            _document(st, program, "program"),
            _document(st, external, "external"),
            _document(st, manual, "manual"),
        )
    except (ValueError, RuntimeError, OSError) as error:
        st.error("Документ не прочитан. Календарь не сформирован.")
        st.caption(str(error))
        return None


def _document(st, upload, slot):
    key = "canonical_extract:" + slot
    if upload is None:
        st.session_state.pop(key, None)
        return None
    data = upload.getvalue()
    digest = sha256(data).hexdigest()
    cached = st.session_state.get(key)
    if cached and cached[0] == digest:
        return cached[1]
    from calendar_pedagoga.lossless_document import extract_bytes
    from calendar_pedagoga.structural_interpretation import interpret_document

    document = interpret_document(extract_bytes(data))
    st.session_state[key] = (digest, document)
    return document


def _session(st, model):
    from calendar_pedagoga.ingestion_confirmation import restore, start

    raw = st.session_state.get("canonical_review_json")
    if raw:
        session = restore(model, raw)
    else:
        session = start(model, st.session_state.get("canonical_review"))
    _store(st, model, session)
    return session


def _store(st, model, session):
    from calendar_pedagoga.ingestion_confirmation import save

    st.session_state["canonical_review_json"] = save(session)
    st.session_state["canonical_review"] = session
    st.session_state["canonical_review_fingerprint"] = model.fingerprint
