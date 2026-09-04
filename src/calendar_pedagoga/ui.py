"""Пользовательский интерфейс формирования календарного плана."""

from __future__ import annotations

import html
import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import streamlit as st

from calendar_pedagoga.academic_year import (
    APPROVED_ACADEMIC_YEAR,
    AcademicYearResolution,
    AcademicYearStatus,
    academic_year_start,
    default_academic_year_start,
    format_academic_year,
    mentions_from_program,
    mentions_from_utp,
    resolve_academic_year,
    resolve_academic_year_from_documents,
)
from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2
from calendar_pedagoga.content_generation import CalendarContentRow, build_content_model
from calendar_pedagoga.lesson_content import LessonContentRow, build_lesson_content
from calendar_pedagoga.normative_engine import (
    NormativeCheck,
    NormativeLayer,
    NormativeLessonView,
    NormativeReport,
    NormativeVerdict,
    evaluate_normative_mvp,
)
from calendar_pedagoga.normative_registry import (
    CalendarRegistryReference,
    NormativeUpdateChoice,
    get_builtin_normative_registry,
    get_update_notice,
    resolve_registry_reference,
)
from calendar_pedagoga.organization_template import (
    ORG_TEMPLATE_UNSUPPORTED_MESSAGE,
    CalendarTemplateSelection,
    OrganizationTemplateError,
    select_calendar_template,
)
from calendar_pedagoga.pipeline import PipelineError, run_calendar_pipeline
from calendar_pedagoga.practice_slots import SLOT_CONTINUE_WARNING, SLOT_PACK_WARNING
from calendar_pedagoga.resolve_utp import UtpResolutionError, resolve_utp
from calendar_pedagoga.transient_documents import TransientDocumentSession
from calendar_pedagoga.upload_validation import (
    UploadPurpose,
    UploadValidationError,
    ValidatedUpload,
    validate_upload,
)
from calendar_pedagoga.parsing import UtpParseResult, parse_utp
from calendar_pedagoga.matching import ContentMatch, MatchStatus, match_utp_to_program
from calendar_pedagoga.program_parsing import (
    ProgramData,
    infer_study_year_number,
    parse_program,
    study_year_label,
)
from calendar_pedagoga.scheduling import (
    ScheduleResult,
    ScheduleValidationError,
    build_schedule,
)


def _generator_revision() -> str:
    """Read the working-tree generator identity, not a cached Git revision."""
    root = Path(__file__).resolve().parents[2]
    paths = [root / "app.py", *sorted((root / "src" / "calendar_pedagoga").glob("*.py"))]
    paths.append(root / "references" / "Календарный план Образец.docx")
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


_LOADED_GENERATOR_REVISION = _generator_revision()


def _inputs_fingerprint(*values: object) -> str:
    parts = []
    for value in values:
        if value is not None and hasattr(value, "getvalue"):
            parts.append([value.name, hashlib.sha256(value.getvalue()).hexdigest()])
        else:
            parts.append(value)
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def _sync_generation_fingerprint(fingerprint: tuple[str, str]) -> bool:
    """Invalidate only; never generate on a rerun or accept an unversioned result."""
    previous = st.session_state.get("calendar_generation_fingerprint")
    if previous == fingerprint:
        return False
    keys = (
        "calendar_download", "calendar_warnings", "calendar_ai_usage",
        "calendar_generation_pending", "calendar_generation_error",
        "calendar_generation_succeeded",
    )
    if any(st.session_state.get(key) for key in keys):
        st.session_state["calendar_generation_invalidated"] = True
    for key in keys:
        st.session_state.pop(key, None)
    st.session_state["calendar_generation_fingerprint"] = fingerprint
    return True


def _refresh_generation_inputs(
    utp_file, program_file, template_file, academic_year, group_number, class_name,
    teacher_name,
) -> None:
    revision = _generator_revision()
    analysis = _inputs_fingerprint(utp_file, program_file, template_file, academic_year)
    inputs = _inputs_fingerprint(analysis, group_number, class_name, teacher_name)
    _sync_generation_fingerprint((inputs, revision))
    analysis_fingerprint = (analysis, revision)
    if st.session_state.get("calendar_analysis_fingerprint") != analysis_fingerprint:
        _reset_analysis_state()
    st.session_state["calendar_analysis_fingerprint"] = analysis_fingerprint
    st.session_state["calendar_generation_inputs"] = inputs


def _reset_analysis_state() -> None:
    if st.session_state.get("calendar_download") or st.session_state.get("calendar_generation_succeeded"):
        st.session_state["calendar_generation_invalidated"] = True
    st.session_state["analysis_ready"] = False
    for key in (
        "analysis_warnings",
        "calendar_download",
        "calendar_warnings",
        "calendar_ai_usage",
        "calendar_generation_pending",
        "calendar_generation_error",
        "calendar_generation_succeeded",
        "calendar_context",
    ):
        st.session_state.pop(key, None)


def _clear_upload_slot(slot: str) -> None:
    nonce_key = f"upload_nonce_{slot}"
    st.session_state[nonce_key] = int(st.session_state.get(nonce_key, 0)) + 1
    st.session_state.pop(f"upload_name_{slot}", None)
    _reset_analysis_state()
    st.rerun()


def _remember_upload(slot: str, uploaded: object | None) -> None:
    name_key = f"upload_name_{slot}"
    previous = st.session_state.get(name_key)
    current = getattr(uploaded, "name", None)
    if previous is not None and current is None:
        st.session_state.pop(name_key, None)
        if st.session_state.get("analysis_ready"):
            _reset_analysis_state()
    elif current is not None:
        st.session_state[name_key] = current


def _file_uploader_with_clear(
    slot: str,
    *,
    label: str,
    type: tuple[str, ...],
    help: str,
) -> object | None:
    nonce = int(st.session_state.setdefault(f"upload_nonce_{slot}", 0))
    uploaded = st.file_uploader(
        label,
        type=type,
        help=help,
        label_visibility="collapsed",
        key=f"upload_{slot}_{nonce}",
    )
    if uploaded is not None:
        name_col, clear_col = st.columns([0.88, 0.12])
        with name_col:
            st.markdown(
                f'<p class="kp-uploaded-name">{html.escape(uploaded.name)}</p>',
                unsafe_allow_html=True,
            )
        with clear_col:
            if st.button(
                "×",
                key=f"clear_{slot}",
                help="Удалить файл",
                use_container_width=True,
            ):
                _clear_upload_slot(slot)
    _remember_upload(slot, uploaded)
    return uploaded


def _inject_landing_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 2.75rem !important;
            padding-bottom: 0.5rem;
            max-width: 900px;
        }
        section.main .block-container {
            padding-top: 2.75rem !important;
        }
        html, body {
            overflow-x: hidden !important;
            overflow-y: auto !important;
            height: auto !important;
            max-height: none !important;
        }
        html body .stApp,
        html body [data-testid="stApp"] {
            overflow-x: hidden !important;
            overflow-y: auto !important;
        }
        html body .stAppViewContainer,
        html body [data-testid="stAppViewContainer"] {
            overflow-x: hidden !important;
            overflow-y: auto !important;
            max-height: none !important;
            scrollbar-width: auto;
            scrollbar-color: #6b7280 #e5e7eb;
        }
        html body [data-testid="stAppViewContainer"] > div {
            overflow-x: hidden !important;
            overflow-y: auto !important;
            height: 100% !important;
            max-height: none !important;
            scrollbar-width: auto;
            scrollbar-color: #6b7280 #e5e7eb;
        }
        html body [data-testid="stAppViewContainer"] > div::-webkit-scrollbar {
            width: 12px;
        }
        html body [data-testid="stAppViewContainer"] > div::-webkit-scrollbar-track {
            background: #e5e7eb;
        }
        html body [data-testid="stAppViewContainer"] > div::-webkit-scrollbar-thumb {
            background: #6b7280;
            border-radius: 8px;
        }
        html body .stMain,
        html body [data-testid="stMain"],
        html body section.main,
        html body .main {
            overflow-x: hidden !important;
            overflow-y: visible !important;
            height: auto !important;
            max-height: none !important;
        }
        html body .block-container,
        html body [data-testid="stMainBlockContainer"] {
            overflow-x: hidden !important;
            overflow-y: visible !important;
            max-height: none !important;
        }
        [data-testid="stMainBlockContainer"] {
            padding-top: 0.75rem;
        }
        header[data-testid="stHeader"] {
            height: 2.25rem;
        }
        .kp-hero-top {
            overflow: visible !important;
            margin: 0 0 0.2rem 0 !important;
            padding-top: 0.25rem;
        }
        [data-testid="stHeading"],
        .stHeading {
            overflow: visible !important;
            height: auto !important;
            min-height: 2.85rem !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        .element-container:has([data-testid="stHeading"]),
        .element-container:has(h1) {
            overflow: visible !important;
            height: auto !important;
            min-height: 2.85rem !important;
            margin-top: 0 !important;
            padding-top: 0 !important;
        }
        .kp-hero-title, h1 {
            font-size: 2rem !important;
            font-weight: 700 !important;
            line-height: 1.35 !important;
            margin: 0 0 0.45rem 0 !important;
            color: #1f2937 !important;
            padding-top: 0.125rem !important;
            overflow: visible !important;
            height: auto !important;
            min-height: 2.5rem !important;
            transform: none !important;
        }
        .kp-hero-subtitle {
            font-size: 1.02rem;
            line-height: 1.4;
            color: #4b5563;
            margin: 0 0 0.55rem 0;
        }
        .kp-step-card {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            padding: 0.55rem 0.85rem 0.15rem 0.85rem;
            margin-bottom: 0.15rem;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .kp-step-title {
            font-size: 1.02rem;
            font-weight: 600;
            color: #111827;
            margin: 0;
        }
        .kp-step-note {
            font-size: 0.92rem;
            color: #6b7280;
            margin: 0.15rem 0 0.35rem 0;
        }
        .kp-badge {
            display: inline-block;
            font-size: 0.68rem;
            font-weight: 700;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            border-radius: 999px;
            padding: 0.15rem 0.55rem;
            margin-left: 0.35rem;
            vertical-align: middle;
        }
        .kp-badge-required {
            background: #eff6ff;
            color: #1d4ed8;
        }
        .kp-badge-optional {
            background: #f3f4f6;
            color: #6b7280;
        }
        .element-container:has(.kp-step-card-year-header) .kp-step-card {
            margin-bottom: 0;
            border-radius: 10px 10px 0 0;
            border-bottom: none;
            padding-bottom: 0.15rem;
        }
        .element-container:has(.kp-step-card-year-header) + .element-container {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-top: none;
            border-bottom: none;
            border-radius: 0;
            padding: 0 0.85rem 0.15rem 0.85rem;
            margin-top: -0.15rem !important;
            margin-bottom: 0;
            box-shadow: none;
        }
        .element-container:has(.kp-step-card-year-header) + .element-container [data-testid="stSelectbox"] {
            margin-top: 0;
            margin-bottom: 0;
        }
        .element-container:has(.kp-step-card-year-header) + .element-container [data-testid="stSelectbox"] > div {
            margin-top: 0;
        }
        .element-container:has(.kp-step-card-year-header) + .element-container + .element-container {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-top: none;
            border-radius: 0 0 10px 10px;
            padding: 0 0.85rem 0.55rem 0.85rem;
            margin-top: 0 !important;
            margin-bottom: 0.15rem;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .kp-normative {
            margin: 0.1rem 0 0.2rem 0;
        }
        .kp-normative details {
            border: 1px dashed #cbd5e1;
            border-radius: 10px;
            background: #f4f6f8;
            padding: 0.15rem 0.65rem;
            margin: 0;
        }
        .kp-normative summary {
            color: #6b7280;
            font-size: 0.86rem;
            cursor: pointer;
        }
        div[data-testid="stFileUploader"] {
            margin-top: -0.2rem;
            margin-bottom: 0.25rem;
        }
        div[data-testid="stFileUploader"] label {
            display: none !important;
        }
        div[data-testid="stFileUploader"] section {
            padding-top: 0.15rem;
            padding-bottom: 0.15rem;
        }
        [data-testid="stFileUploaderDropzoneInstructions"],
        [data-testid="stFileUploaderDropzoneInstructions"] span,
        div[data-testid="stFileUploader"] small {
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
            max-height: 0 !important;
            overflow: hidden !important;
            margin: 0 !important;
            padding: 0 !important;
            font-size: 0 !important;
            line-height: 0 !important;
        }
        [data-testid="stFileUploaderDropzone"] {
            min-height: 2.4rem !important;
            padding: 0.35rem 0.5rem !important;
        }
        [data-testid="stExpander"] {
            margin-top: 0.15rem;
            margin-bottom: 0.25rem;
        }
        [data-testid="stExpander"] details {
            border: 1px solid #eef2f7;
            border-radius: 10px;
            background: #fafbfc;
        }
        [data-testid="stExpander"] summary {
            color: #6b7280;
            font-size: 0.86rem;
        }
        div[data-testid="stButton"] {
            margin-top: 0.15rem;
        }
        .stButton > button[kind="primary"] {
            background: linear-gradient(180deg, #2563eb 0%, #1d4ed8 100%);
            border: none;
            font-size: 1rem;
            font-weight: 600;
            padding: 0.62rem 1rem;
            border-radius: 10px;
        }
        .stButton > button[kind="primary"]:hover {
            background: linear-gradient(180deg, #1d4ed8 0%, #1e40af 100%);
            border: none;
            color: #ffffff;
        }
        .kp-results {
            margin-top: 0.35rem;
        }
        .kp-results-title {
            font-size: 1.35rem;
            font-weight: 700;
            color: #111827;
            margin: 0 0 0.35rem 0;
        }
        .kp-results-summary {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            padding: 0.85rem 1rem;
            margin: 0.75rem 0 0.55rem 0;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .kp-results-summary p {
            margin: 0.15rem 0;
            color: #374151;
            line-height: 1.45;
        }
        .kp-results-summary .kp-summary-label {
            color: #6b7280;
            font-size: 0.92rem;
            margin-top: 0.55rem;
        }
        .kp-results-status {
            margin: 0.45rem 0 0.15rem 0;
        }
        .kp-normative-check {
            background: #f8fbff;
            border: 1px solid #bfdbfe;
            border-left: 4px solid #2563eb;
            border-radius: 10px;
            padding: 0.75rem 1rem 0.85rem 1rem;
            margin: 0.55rem 0 0.45rem 0;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .kp-normative-check-title {
            font-size: 1.02rem;
            font-weight: 600;
            color: #111827;
            margin: 0 0 0.2rem 0;
        }
        .kp-normative-check-lead {
            font-size: 0.9rem;
            color: #4b5563;
            margin: 0 0 0.45rem 0;
            line-height: 1.4;
        }
        .kp-normative-layer {
            font-size: 0.94rem;
            font-weight: 600;
            color: #111827;
            margin: 0.55rem 0 0.1rem 0;
        }
        .kp-normative-layer-lead {
            font-size: 0.82rem;
            color: #6b7280;
            margin: 0 0 0.2rem 0;
            line-height: 1.35;
        }
        .kp-normative-note {
            font-size: 0.9rem;
            color: #4b5563;
            margin: 0.2rem 0 0.35rem 0;
            line-height: 1.4;
        }
        .kp-normative-check-label {
            font-size: 0.92rem;
            font-weight: 600;
            margin: 0.4rem 0 0.15rem 0;
        }
        .kp-normative-check-label.ok { color: #166534; }
        .kp-normative-check-label.warn { color: #92400e; }
        .kp-normative-check-label.skip { color: #6b7280; }
        .kp-normative-check ul {
            margin: 0 0 0.15rem 1.1rem;
            padding: 0;
            color: #374151;
        }
        .kp-normative-check li {
            margin: 0.12rem 0;
            line-height: 1.4;
        }
        .kp-results-note {
            color: #6b7280;
            font-size: 0.9rem;
            margin: 0.35rem 0 0.15rem 0;
        }
        [data-testid="stFileUploaderFile"] {
            display: none !important;
        }
        .kp-uploaded-name {
            margin: 0.2rem 0 0.1rem 0;
            color: #374151;
            font-size: 0.9rem;
            line-height: 1.3;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        button[title="Удалить файл"] {
            min-width: 2rem !important;
            height: 2rem !important;
            padding: 0 !important;
            font-size: 1.15rem !important;
            line-height: 1 !important;
            color: #6b7280 !important;
            background: #ffffff !important;
            border: 1px solid #e5e7eb !important;
            border-radius: 8px !important;
        }
        button[title="Удалить файл"]:hover {
            color: #b91c1c !important;
            border-color: #fecaca !important;
            background: #fef2f2 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_group_class_fields() -> tuple[str, str, str]:
    group_col, class_col = st.columns(2)
    with group_col:
        group_number = st.text_input(
            "Группа №",
            key="group_number",
            help="Необязательно. Если не заполнить, в документе останется линия.",
        )
    with class_col:
        class_name = st.text_input(
            "Класс",
            key="class_name",
            help="Необязательно. Если не заполнить, в документе останется линия.",
        )
    teacher_name = st.text_input(
        "ФИО педагога",
        key="teacher_name",
        help="Необязательно. Если не заполнить, в документе ничего не добавится.",
    )
    return group_number or "", class_name or "", teacher_name or ""


def _peek_academic_year_resolution(
    utp_file, program_file,
) -> AcademicYearResolution:
    utp_mentions: tuple = ()
    program_mentions: tuple = ()
    if utp_file is not None:
        try:
            utp_mentions = mentions_from_utp(parse_utp(utp_file.getvalue()))
        except Exception:
            utp_mentions = ()
    if program_file is not None:
        filename = getattr(program_file, "name", "") or ""
        try:
            if filename.lower().endswith(".docx"):
                program = parse_program(program_file.getvalue(), filename)
                program_mentions = mentions_from_program(program)
                if utp_file is None:
                    try:
                        utp_mentions = mentions_from_utp(parse_utp(program_file.getvalue()))
                    except Exception:
                        pass
        except Exception:
            program_mentions = ()
    return resolve_academic_year(utp_mentions, program_mentions)


def _render_academic_year_input(utp_file, program_file) -> str:
    resolution = _peek_academic_year_resolution(utp_file, program_file)
    docs_fp = _inputs_fingerprint(utp_file, program_file)
    suggested = academic_year_start(resolution.suggested)
    if st.session_state.get("academic_year_docs_fp") != docs_fp:
        st.session_state["academic_year_start"] = (
            suggested if suggested is not None else default_academic_year_start()
        )
        st.session_state["academic_year_docs_fp"] = docs_fp
    st.session_state.setdefault("academic_year_start", default_academic_year_start())

    st.markdown(
        '<div class="kp-step-card kp-step-card-year-header">'
        '<p class="kp-step-title">4. Учебный год</p>'
        '<p class="kp-step-note">Задаётся началом Y и показывается как Y–(Y+1)</p>'
        "</div>",
        unsafe_allow_html=True,
    )
    start = int(
        st.number_input(
            "Начало учебного года",
            min_value=1990,
            max_value=2100,
            step=1,
            key="academic_year_start",
            help="Учебный год хранится как начало Y и канон Y–(Y+1). Список лет не задаётся.",
        )
    )
    academic_year = format_academic_year(start)
    st.caption(f"Учебный год: {academic_year}")
    if resolution.status is AcademicYearStatus.CONFLICT:
        st.warning(resolution.message)
    elif resolution.status in {AcademicYearStatus.AUTO, AcademicYearStatus.SINGLE}:
        st.info(resolution.message)
    elif resolution.status is AcademicYearStatus.MISSING and (utp_file or program_file):
        st.caption(resolution.message)
    return academic_year


def _render_upload_screen() -> tuple[object | None, object | None, object | None, str, str, str, str]:
    _inject_landing_styles()

    st.markdown('<div class="kp-hero-top">', unsafe_allow_html=True)
    st.title("Календарь педагога")
    st.markdown(
        '<p class="kp-hero-subtitle">Загрузите документы — приложение проверит часы, '
        "составит расписание занятий и подготовит календарный план в Word.</p>",
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(
        '<div class="kp-step-card">'
        '<p class="kp-step-title">1. Программа обучения'
        '<span class="kp-badge kp-badge-required">обязательно</span></p>'
        '<p class="kp-step-note">Документ с содержанием программы и, если есть, '
        "учебно-тематическим планом</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    program_file = _file_uploader_with_clear(
        "program",
        label="Загрузите образовательную программу",
        type=("doc", "docx"),
        help="Программа — образовательная программа, DOC/DOCX, до 10 МБ.",
    )

    utp_col, template_col = st.columns(2, gap="medium")
    with utp_col:
        st.markdown(
            '<div class="kp-step-card">'
            '<p class="kp-step-title">2. Учебно-тематический план'
            '<span class="kp-badge kp-badge-optional">необязательно</span></p>'
            '<p class="kp-step-note">Загрузите отдельно, только если УТП находится в другом файле</p>'
            "</div>",
            unsafe_allow_html=True,
        )
        utp_file = _file_uploader_with_clear(
            "utp",
            label="Загрузите УТП",
            type=("docx",),
            help="УТП — учебно-тематический план, DOCX, до 10 МБ.",
        )
    with template_col:
        st.markdown(
            '<div class="kp-step-card">'
            '<p class="kp-step-title">3. Шаблон календарного плана'
            '<span class="kp-badge kp-badge-optional">необязательно</span></p>'
            '<p class="kp-step-note">Если есть образец вашей организации — загрузите его; иначе используем стандартный</p>'
            "</div>",
            unsafe_allow_html=True,
        )
        organization_template_file = _file_uploader_with_clear(
            "template",
            label="Шаблон календарного плана вашей организации",
            type=("docx",),
            help=(
                "Шаблон — только образец календарного плана организации, "
                "DOCX, до 10 МБ."
            ),
        )

    academic_year = _render_academic_year_input(utp_file, program_file)
    group_number, class_name, teacher_name = _render_group_class_fields()

    _render_normative_panel()

    return (
        utp_file,
        program_file,
        organization_template_file,
        academic_year,
        group_number,
        class_name,
        teacher_name,
    )


def _render_normative_panel() -> None:
    normative_registry = get_builtin_normative_registry()
    registry_snapshot = normative_registry.current
    st.markdown('<div class="kp-normative">', unsafe_allow_html=True)
    with st.expander("Нормативная база", expanded=False):
        st.caption(
            "Справочник документов из реестра. Это не проверка вашей программы "
            "и на календарь автоматически не влияет."
        )
        st.write(
            f"Версия: **{registry_snapshot.registry_version}** · действует с: "
            f"**{registry_snapshot.effective_from:%d.%m.%Y}** · проверена: "
            f"**{registry_snapshot.verified_on:%d.%m.%Y}**."
        )
        st.write(f"Документов в реестре: **{len(registry_snapshot.documents)}**.")
        st.dataframe(
            [
                {
                    "Документ": document.title,
                    "Номер": document.number,
                    "Дата": document.document_date.strftime("%d.%m.%Y"),
                    "Статус": document.status.value,
                    "Что регулирует": document.regulates,
                    "Действует с": (
                        document.effective_from.strftime("%d.%m.%Y")
                        if document.effective_from
                        else ""
                    ),
                    "Действует до": (
                        document.effective_until.strftime("%d.%m.%Y")
                        if document.effective_until
                        else ""
                    ),
                    "Официальный URL": document.official_url,
                    "Проверено": (
                        document.verified_on.strftime("%d.%m.%Y")
                        if document.verified_on
                        else ""
                    ),
                }
                for document in registry_snapshot.documents
            ],
            hide_index=True,
            use_container_width=True,
        )
        saved_version = st.session_state.setdefault(
            "calendar_registry_version", registry_snapshot.registry_version
        )
        notice = get_update_notice(
            CalendarRegistryReference(saved_version), normative_registry
        )
        if notice.update_available:
            st.warning(
                f"Доступна нормативная база {notice.available_version}; "
                f"календарь использует {notice.calendar_version}."
            )
            choice_label = st.radio(
                "Выберите нормативную версию",
                ("Оставить текущую", "Применить актуальную версию"),
            )
            if st.button("Подтвердить выбор нормативной версии"):
                choice = (
                    NormativeUpdateChoice.APPLY_CURRENT
                    if choice_label == "Применить актуальную версию"
                    else NormativeUpdateChoice.KEEP_EXISTING
                )
                reference = resolve_registry_reference(
                    CalendarRegistryReference(saved_version), normative_registry, choice
                )
                st.session_state["calendar_registry_version"] = reference.registry_version
    st.markdown("</div>", unsafe_allow_html=True)


def _fact(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_TABLE_WIDTH_WARNING = (
    "Ширина таблицы не задана явно; возможны переносы на новые страницы."
)


_INTERNAL_SLOT_WARNINGS = frozenset(
    {
        _TABLE_WIDTH_WARNING,
        SLOT_PACK_WARNING,
        SLOT_CONTINUE_WARNING,
    }
)


def _teacher_generation_warnings(warnings: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        warning for warning in warnings if warning not in _INTERNAL_SLOT_WARNINGS
    )


def _collect_analysis_warnings(
    utp: UtpParseResult,
    matches: tuple[ContentMatch, ...],
    content_rows: tuple[CalendarContentRow, ...],
    lessons: tuple[LessonContentRow, ...],
) -> tuple[str, ...]:
    collected: list[str] = list(utp.warnings)
    for match in matches:
        if match.ambiguous_candidates:
            collected.append(
                f"Неоднозначное соответствие для «{match.utp_position.title}»: "
                + "; ".join(match.ambiguous_candidates)
            )
    collected.extend(warning for row in content_rows for warning in row.warnings)
    collected.extend(warning for row in lessons for warning in row.warnings)
    return tuple(dict.fromkeys(collected))


_LAYER_TITLES = (
    (NormativeLayer.FEDERAL, "Документы закона"),
    (NormativeLayer.LOCAL, "Календарь учреждения"),
    (NormativeLayer.METHODICAL, "Сверка ваших часов"),
)
_SHORT_WEEK_NOTE = (
    "1–6 сентября и 28–30 декабря — короткие недели, часы на них остаются. "
    "Даты приложение не сдвигает."
)
_METHODICAL_PASS_LINE = "Часы программы, УТП и плана совпадают."
_ATTESTATION_MISSING_UI = (
    "В программе указана аттестация, но в темах УТП она не найдена."
)
_YEAR_NO_DURATION_UI = "Срок программы не указан, сравнить год со сроком нельзя."


def _lesson_views_for_normative(
    content_rows: tuple[CalendarContentRow, ...],
) -> tuple[NormativeLessonView, ...]:
    rows = build_lesson_content_v2(content_rows)
    return tuple(
        NormativeLessonView(
            theory_hours=row.source.theory_hours,
            practice_hours=row.source.practice_hours,
            lesson_type=row.lesson_type,
            assessment_method=row.assessment_method,
            topic_title=row.source.topic_title,
        )
        for row in rows
    )


def _year_is_found(report: NormativeReport) -> bool:
    return any(
        item.check_id == "study_year_found" and item.verdict is NormativeVerdict.PASS
        for item in report.checks
    )


def _normative_teacher_text(item: NormativeCheck, report: NormativeReport) -> str:
    if item.check_id == "attestation" and "календарном плане и УТП" in item.teacher_text:
        return _ATTESTATION_MISSING_UI
    if (
        item.check_id == "year_within_duration"
        and item.verdict is NormativeVerdict.NOT_CHECKED
        and _year_is_found(report)
    ):
        return _YEAR_NO_DURATION_UI
    return item.teacher_text


def _is_short_week_warning(item: NormativeCheck) -> bool:
    return (
        item.check_id == "short_week_full_load"
        and item.verdict is NormativeVerdict.WARNING
    )


def _is_hidden_duration_warning(item: NormativeCheck, report: NormativeReport) -> bool:
    return (
        item.check_id == "duration_found"
        and item.verdict is NormativeVerdict.WARNING
        and _year_is_found(report)
        and any(
            other.check_id == "year_within_duration"
            and other.verdict is NormativeVerdict.NOT_CHECKED
            for other in report.checks
        )
    )


def _collapsed_pass_line(
    layer: NormativeLayer,
    passed: tuple[NormativeCheck, ...],
) -> str:
    if layer is NormativeLayer.METHODICAL:
        return _METHODICAL_PASS_LINE
    return passed[0].teacher_text


def _short_week_note(academic_year: str) -> str:
    if academic_year == APPROVED_ACADEMIC_YEAR:
        return _SHORT_WEEK_NOTE
    return "Короткие недели получили полную нагрузку. Даты приложение не сдвигает."


def _render_normative_report(report: NormativeReport, *, academic_year: str) -> None:
    sections: list[str] = [
        '<div class="kp-normative-check">',
        '<p class="kp-normative-check-title">Нормативная и методическая проверка</p>',
        '<p class="kp-normative-check-lead">'
        "Эта проверка не изменяет Word автоматически.</p>",
    ]
    for layer, title in _LAYER_TITLES:
        layer_checks = report.for_layer(layer)
        if not layer_checks:
            continue
        sections.append(f'<p class="kp-normative-layer">{html.escape(title)}</p>')
        passed = tuple(
            item
            for item in layer_checks
            if item.verdict is NormativeVerdict.PASS
            and item.check_id != "short_week_full_load"
        )
        warnings = tuple(
            item
            for item in layer_checks
            if item.verdict is NormativeVerdict.WARNING
            and not _is_short_week_warning(item)
            and not _is_hidden_duration_warning(item, report)
        )
        unchecked = tuple(
            item
            for item in layer_checks
            if item.verdict is NormativeVerdict.NOT_CHECKED
        )
        short_week_notes = tuple(
            item for item in layer_checks if _is_short_week_warning(item)
        )
        if passed:
            sections.append("<ul>")
            sections.append(
                f"<li>✓ {html.escape(_collapsed_pass_line(layer, passed))}</li>"
            )
            sections.append("</ul>")
        if short_week_notes:
            sections.append(
                f'<p class="kp-normative-note">{html.escape(_short_week_note(academic_year))}</p>'
            )
        if warnings:
            sections.append(
                '<p class="kp-normative-check-label warn">⚠ На что обратить внимание</p>'
            )
            sections.append("<ul>")
            sections.extend(
                f"<li>{html.escape(_normative_teacher_text(item, report))}</li>"
                for item in warnings
            )
            sections.append("</ul>")
        if unchecked:
            sections.append(
                '<p class="kp-normative-check-label skip">— Что не удалось проверить</p>'
            )
            sections.append("<ul>")
            sections.extend(
                f"<li>{html.escape(_normative_teacher_text(item, report))}</li>"
                for item in unchecked
            )
            sections.append("</ul>")
    sections.append("</div>")
    st.markdown("".join(sections), unsafe_allow_html=True)


def _render_teacher_analysis_screen(
    *,
    utp: UtpParseResult,
    program: ProgramData | None,
    schedule: ScheduleResult,
    matches: tuple[ContentMatch, ...],
    academic_year: str,
    source_utp_name: str | None = None,
    program_filename: str | None = None,
    content_rows: tuple[CalendarContentRow, ...] = (),
    after_summary: Callable[[], None] | None = None,
) -> None:
    metadata = utp.metadata
    totals = utp.table_totals
    weeks = metadata.study_weeks or len(schedule.weeks)

    st.markdown('<div class="kp-results">', unsafe_allow_html=True)
    st.markdown('<p class="kp-results-title">Документы проверены</p>', unsafe_allow_html=True)

    program_name = _fact(metadata.program_name or (program.title if program else None))
    study_year = _fact(
        study_year_label(
            metadata.study_year,
            source_utp_name,
            program_filename,
        )
    )
    student_age = _fact(
        metadata.student_age or (program.student_age if program else None)
    )
    matched = 0
    total = len(matches)
    if program and matches:
        matched = sum(
            match.status is not MatchStatus.NOT_MATCHED for match in matches
        )
    summary_lines: list[str] = []
    resolution = resolve_academic_year_from_documents(utp, program)
    if program_name:
        summary_lines.append(f"<p><strong>Программа:</strong> {html.escape(program_name)}</p>")
    summary_lines.append(
        f"<p><strong>Учебный год:</strong> {html.escape(academic_year)}</p>"
    )
    if (
        resolution.status in {AcademicYearStatus.AUTO, AcademicYearStatus.SINGLE}
        and resolution.suggested == academic_year
        and resolution.sources
    ):
        source = resolution.sources[0]
        summary_lines.append(
            "<p>Источник учебного года: "
            f"{html.escape(source.origin)} — «{html.escape(source.snippet)}»</p>"
        )
    if study_year:
        summary_lines.append(f"<p><strong>Год обучения:</strong> {html.escape(study_year)}</p>")
    if student_age:
        summary_lines.append(f"<p><strong>Возраст:</strong> {html.escape(student_age)}</p>")
    summary_lines.append(
        '<p class="kp-summary-label"><strong>Учебная нагрузка:</strong></p>'
    )
    if totals:
        summary_lines.extend(
            [
                f"<p>{weeks} недель</p>",
                f"<p>{totals.total} часов</p>",
                f"<p>{totals.theory} ч теория</p>",
                f"<p>{totals.practice} ч практика</p>",
                (
                    f"<p>{totals.total} часа распределены на "
                    f"{len(schedule.weeks)} учебных недель.</p>"
                ),
            ]
        )
    else:
        summary_lines.append(f"<p>{weeks} недель</p>")
    if program and matches and matched == total:
        summary_lines.append(f"<p>Все {total} тем найдены в программе.</p>")

    st.markdown(
        '<div class="kp-results-summary">' + "".join(summary_lines) + "</div>",
        unsafe_allow_html=True,
    )
    for warning in schedule.warnings:
        st.info(warning)
    if after_summary is not None:
        after_summary()
    _render_normative_report(
        evaluate_normative_mvp(
            utp,
            program,
            academic_year=academic_year,
            study_year_hints=(source_utp_name, program_filename),
            schedule=schedule,
            lessons=_lesson_views_for_normative(content_rows),
        ),
        academic_year=academic_year,
    )

    if program and matches and matched != total:
        st.warning(f"Найдено {matched} из {total} тем в программе")
    elif program is None:
        st.info("Образовательная программа не загружена.")

    st.markdown(
        '<p class="kp-results-note">Недостающие сведения не мешают '
        "сформировать календарный план.</p>",
        unsafe_allow_html=True,
    )

    st.markdown("</div>", unsafe_allow_html=True)


def _store_analysis_context(
    *,
    validated_utp: ValidatedUpload,
    validated_program: ValidatedUpload | None,
    template_selection: CalendarTemplateSelection,
    academic_year: str,
) -> None:
    st.session_state["calendar_context"] = {
        "validated_utp": validated_utp,
        "validated_program": validated_program,
        "template_selection": template_selection,
        "academic_year": academic_year,
    }


def _show_generation_controls(
    *,
    validated_utp: ValidatedUpload,
    validated_program: ValidatedUpload | None,
    template_selection: CalendarTemplateSelection,
    academic_year: str,
    group_number: str,
    class_name: str,
    teacher_name: str,
) -> None:
    if _generator_revision() != _LOADED_GENERATOR_REVISION:
        st.warning("Код приложения обновлён. Перезапустите приложение на localhost:8501 и сформируйте план заново.")
        _show_generation_result()
        return

    generation_pending = bool(st.session_state.get("calendar_generation_pending"))
    generation_requested = st.button(
        "Сформировать календарный план",
        type="primary",
        use_container_width=True,
        disabled=generation_pending,
    )
    if generation_requested:
        st.session_state.pop("calendar_generation_invalidated", None)
        st.session_state["calendar_generation_pending"] = True
        st.session_state.pop("calendar_generation_error", None)
        st.session_state.pop("calendar_generation_succeeded", None)
        st.session_state.pop("calendar_download", None)
        st.session_state.pop("calendar_warnings", None)
        st.session_state.pop("calendar_ai_usage", None)
        st.rerun()

    if generation_pending:
        utp = validated_utp.parsed
        assert isinstance(utp, UtpParseResult)
        program = None
        if validated_program is not None:
            program = validated_program.parsed
            assert isinstance(program, ProgramData)

        try:
            with st.spinner("Формируем календарный план…"):
                with TransientDocumentSession() as operation:
                    result = run_calendar_pipeline(
                        utp,
                        program,
                        academic_year=academic_year,
                        template=template_selection,
                        source_utp_name=validated_utp.filename,
                        use_ai=False,
                        program_filename=(
                            validated_program.filename
                            if validated_program is not None
                            else None
                        ),
                        group_number=group_number,
                        class_name=class_name,
                        teacher_name=teacher_name,
                    )
                    operation.publish_result(result.filename, result.content)
                    st.session_state["calendar_download"] = operation.take_result_for_download()
                    st.session_state["calendar_warnings"] = result.warnings
        except (PipelineError, ScheduleValidationError, ValueError) as error:
            st.session_state["calendar_generation_error"] = str(error)
        else:
            st.session_state["calendar_generation_succeeded"] = True
        finally:
            st.session_state["calendar_generation_pending"] = False
        st.rerun()

    _show_generation_result()


@st.fragment(run_every="2s")
def _show_generation_result() -> None:
    inputs = st.session_state.get("calendar_generation_inputs", "")
    if _sync_generation_fingerprint((inputs, _generator_revision())):
        _reset_analysis_state()
    if st.session_state.get("calendar_generation_invalidated"):
        st.info("Данные или версия приложения изменились. Сформируйте календарный план заново.")
    generation_error = st.session_state.get("calendar_generation_error")
    if generation_error:
        st.error(f"Не удалось сформировать календарный план: {generation_error}")

    if st.session_state.get("calendar_generation_succeeded"):
        st.success("Календарный план готов")
        for warning in _teacher_generation_warnings(
            tuple(st.session_state.get("calendar_warnings", ()))
        ):
            st.warning(warning)

    download = st.session_state.get("calendar_download")
    if download is not None:
        st.download_button(
            "Скачать календарный план",
            data=download.content,
            file_name=download.filename,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True,
        )


def run_app() -> None:
    """Показать экран загрузки, анализа и формирования календарного плана."""
    st.set_page_config(page_title="Календарь педагога", page_icon="📅", layout="centered")

    (
        utp_file,
        program_file,
        organization_template_file,
        academic_year,
        group_number,
        class_name,
        teacher_name,
    ) = _render_upload_screen()

    _refresh_generation_inputs(
        utp_file, program_file, organization_template_file,
        academic_year, group_number, class_name, teacher_name,
    )
    if st.session_state.get("calendar_generation_invalidated") and not st.session_state.get("analysis_ready"):
        st.info("План устарел. Нажмите «Проверить документы» и сформируйте календарный план заново.")

    if st.button("Проверить документы", type="primary", use_container_width=True):
        if program_file is None:
            st.error("Загрузите программу обучения.")
            return

        with TransientDocumentSession() as uploads:
            uploads.replace(
                UploadPurpose.PROGRAM,
                program_file.name,
                program_file.getvalue(),
            )
            if utp_file is not None:
                uploads.replace(UploadPurpose.UTP, utp_file.name, utp_file.getvalue())
            if organization_template_file is not None:
                uploads.replace(
                    UploadPurpose.CALENDAR_TEMPLATE,
                    organization_template_file.name,
                    organization_template_file.getvalue(),
                )
            try:
                transient_program = uploads.get(UploadPurpose.PROGRAM)
                assert transient_program is not None
                validated_program = validate_upload(
                    UploadPurpose.PROGRAM,
                    transient_program.filename,
                    transient_program.content,
                )
                transient_utp = uploads.get(UploadPurpose.UTP)
                validated_utp_upload = (
                    validate_upload(
                        UploadPurpose.UTP,
                        transient_utp.filename,
                        transient_utp.content,
                    )
                    if transient_utp is not None
                    else None
                )
                transient_template = uploads.get(UploadPurpose.CALENDAR_TEMPLATE)
                validated_template = (
                    validate_upload(
                        UploadPurpose.CALENDAR_TEMPLATE,
                        transient_template.filename,
                        transient_template.content,
                    )
                    if transient_template is not None
                    else None
                )
                resolved_utp = resolve_utp(validated_utp_upload, validated_program)
            except UploadValidationError as error:
                st.error(str(error))
                return
            except UtpResolutionError as error:
                st.error(str(error))
                return

        template_selection = select_calendar_template()
        if validated_template is not None:
            try:
                template_selection = select_calendar_template(
                    validated_template.filename,
                    validated_template.content,
                )
            except OrganizationTemplateError:
                st.error(ORG_TEMPLATE_UNSUPPORTED_MESSAGE)
                return

        validated_utp = ValidatedUpload(
            UploadPurpose.UTP,
            (
                validated_utp_upload.filename
                if validated_utp_upload is not None
                else f"УТП из файла «{validated_program.filename}»"
            ),
            (
                validated_utp_upload.content
                if validated_utp_upload is not None
                else validated_program.content
            ),
            resolved_utp,
        )
        program = parse_program(
            validated_program.content,
            validated_program.filename,
            study_year=infer_study_year_number(resolved_utp.metadata.study_year),
        )
        validated_program = ValidatedUpload(
            validated_program.purpose,
            validated_program.filename,
            validated_program.content,
            program,
        )
        utp = resolved_utp

        try:
            build_schedule(utp, academic_year)
        except (ScheduleValidationError, ValueError) as error:
            st.error(f"Не удалось построить календарное распределение: {error}")
            return

        _store_analysis_context(
            validated_utp=validated_utp,
            validated_program=validated_program,
            template_selection=template_selection,
            academic_year=academic_year,
        )
        st.session_state["analysis_ready"] = True
        st.session_state.pop("analysis_warnings", None)
        st.session_state.pop("calendar_download", None)
        st.session_state.pop("calendar_warnings", None)
        st.session_state.pop("calendar_ai_usage", None)
        st.session_state.pop("calendar_generation_pending", None)
        st.session_state.pop("calendar_generation_error", None)
        st.session_state.pop("calendar_generation_succeeded", None)

    if st.session_state.get("analysis_ready") and "calendar_context" in st.session_state:
        context = st.session_state["calendar_context"]
        validated_utp = context["validated_utp"]
        validated_program = context["validated_program"]
        template_selection = context["template_selection"]
        academic_year = context["academic_year"]

        utp = validated_utp.parsed
        assert isinstance(utp, UtpParseResult)
        program = None
        if validated_program is not None:
            program = validated_program.parsed
            assert isinstance(program, ProgramData)

        try:
            schedule = build_schedule(utp, academic_year)
            content_rows = build_content_model(
                schedule,
                utp,
                program,
                validated_utp.filename,
            )
            lessons = build_lesson_content(content_rows)
            matches = (
                tuple(match_utp_to_program(utp.topics, program.content_items))
                if program is not None
                else ()
            )
        except (ScheduleValidationError, ValueError) as error:
            st.error(f"Не удалось построить календарное распределение: {error}")
            st.session_state["analysis_ready"] = False
            return

        detail_warnings = st.session_state.get("analysis_warnings")
        if detail_warnings is None:
            detail_warnings = _collect_analysis_warnings(
                utp,
                matches,
                content_rows,
                lessons,
            )
            st.session_state["analysis_warnings"] = detail_warnings

        st.markdown(
            '<p class="kp-results-note">Чтобы заменить документы, измените файлы '
            "выше и нажмите «Проверить документы» снова.</p>",
            unsafe_allow_html=True,
        )

        if validated_utp.filename.startswith("УТП из файла"):
            st.info("Учебно-тематический план найден внутри программы обучения.")

        _render_teacher_analysis_screen(
            utp=utp,
            program=program,
            schedule=schedule,
            matches=matches,
            academic_year=academic_year,
            source_utp_name=validated_utp.filename,
            program_filename=(
                validated_program.filename if validated_program is not None else None
            ),
            content_rows=content_rows,
            after_summary=lambda: _show_generation_controls(
                validated_utp=validated_utp,
                validated_program=validated_program,
                template_selection=template_selection,
                academic_year=academic_year,
                group_number=group_number,
                class_name=class_name,
                teacher_name=teacher_name,
            ),
        )
