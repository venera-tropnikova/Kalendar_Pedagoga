"""Пользовательский интерфейс формирования календарного плана."""

from __future__ import annotations

import html

import streamlit as st

from calendar_pedagoga.content_generation import CalendarContentRow, build_content_model
from calendar_pedagoga.lesson_content import LessonContentRow, build_lesson_content
from calendar_pedagoga.normative_engine import NormativeReport, evaluate_normative_mvp
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
from calendar_pedagoga.resolve_utp import UtpResolutionError, resolve_utp
from calendar_pedagoga.transient_documents import TransientDocumentSession
from calendar_pedagoga.upload_validation import (
    UploadPurpose,
    UploadValidationError,
    ValidatedUpload,
    validate_upload,
)
from calendar_pedagoga.parsing import UtpParseResult
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


ACADEMIC_YEARS: tuple[str, ...] = ("2026–2027",)
SUPPORTED_ACADEMIC_YEAR = ACADEMIC_YEARS[0]


def _reset_analysis_state() -> None:
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
            border: 1px solid #eef2f7;
            border-radius: 10px;
            background: #fafbfc;
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
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            padding: 0.75rem 1rem 0.85rem 1rem;
            margin: 0.55rem 0 0.45rem 0;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .kp-normative-check-title {
            font-size: 1.02rem;
            font-weight: 600;
            color: #111827;
            margin: 0 0 0.45rem 0;
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


def _render_group_class_fields() -> tuple[str, str]:
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
    return group_number or "", class_name or ""


def _render_upload_screen() -> tuple[object | None, object | None, object | None, str, str, str]:
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
        '<div class="kp-step-card kp-step-card-year-header">'
        '<p class="kp-step-title">1. Учебный год</p>'
        '<p class="kp-step-note">Выберите год, на который составляется календарный план</p>'
        "</div>",
        unsafe_allow_html=True,
    )
    academic_year = st.selectbox(
        "Учебный год",
        options=ACADEMIC_YEARS,
        index=0,
        help="Расписание поддерживает учебный год 2026–2027 (36 недель).",
        label_visibility="collapsed",
    )
    group_number, class_name = _render_group_class_fields()

    st.markdown(
        '<div class="kp-step-card">'
        '<p class="kp-step-title">2. Программа обучения'
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
            '<p class="kp-step-title">3. Учебно-тематический план'
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
            '<p class="kp-step-title">4. Шаблон календарного плана'
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

    _render_normative_panel()

    return (
        utp_file,
        program_file,
        organization_template_file,
        academic_year,
        group_number,
        class_name,
    )


def _render_normative_panel() -> None:
    normative_registry = get_builtin_normative_registry()
    registry_snapshot = normative_registry.current
    st.markdown('<div class="kp-normative">', unsafe_allow_html=True)
    with st.expander("Нормативная база", expanded=False):
        st.caption(
            "Справочная информация; на календарь автоматически не влияет."
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


def _value(value: object | None) -> str:
    return str(value) if value is not None else "Не найдено"


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


def _render_normative_report(report: NormativeReport) -> None:
    sections: list[str] = [
        '<div class="kp-normative-check">',
        '<p class="kp-normative-check-title">Нормативная проверка</p>',
    ]
    groups = (
        (report.passed, "ok", "Что в порядке"),
        (report.warnings, "warn", "На что обратить внимание"),
        (report.unchecked, "skip", "Что не удалось проверить"),
    )
    for checks, css, title in groups:
        if not checks:
            continue
        mark = {"ok": "✓", "warn": "⚠", "skip": "—"}[css]
        sections.append(
            f'<p class="kp-normative-check-label {css}">{mark} {html.escape(title)}</p>'
        )
        sections.append("<ul>")
        sections.extend(
            f"<li>{html.escape(item.teacher_text)}</li>" for item in checks
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
    detail_warnings: tuple[str, ...],
    academic_year: str,
    source_utp_name: str | None = None,
    program_filename: str | None = None,
) -> None:
    metadata = utp.metadata
    totals = utp.table_totals
    weeks = metadata.study_weeks or len(schedule.weeks)

    st.markdown('<div class="kp-results">', unsafe_allow_html=True)
    st.markdown('<p class="kp-results-title">Документы проверены</p>', unsafe_allow_html=True)
    st.success("Данные успешно прочитаны. Можно формировать календарный план.")

    program_name = metadata.program_name or (program.title if program else None)
    study_year = study_year_label(
        metadata.study_year,
        source_utp_name,
        program_filename,
    )
    student_age = metadata.student_age or (program.student_age if program else None)
    summary_lines = [
        f"<p><strong>Программа:</strong> {_value(program_name)}</p>",
        f"<p><strong>Год обучения:</strong> {_value(study_year)}</p>",
        f"<p><strong>Возраст:</strong> {_value(student_age)}</p>",
        '<p class="kp-summary-label"><strong>Учебная нагрузка:</strong></p>',
    ]
    if totals:
        summary_lines.extend(
            [
                f"<p>{weeks} недель</p>",
                f"<p>{totals.total} часов</p>",
                f"<p>{totals.theory} ч теория</p>",
                f"<p>{totals.practice} ч практика</p>",
            ]
        )
    else:
        summary_lines.append(f"<p>{weeks} недель</p>")

    st.markdown(
        '<div class="kp-results-summary">' + "".join(summary_lines) + "</div>",
        unsafe_allow_html=True,
    )
    _render_normative_report(
        evaluate_normative_mvp(
            utp,
            program,
            academic_year=academic_year,
            study_year_hints=(source_utp_name, program_filename),
        )
    )

    if program and matches:
        matched = sum(
            match.status is not MatchStatus.NOT_MATCHED for match in matches
        )
        total = len(matches)
        if matched == total:
            st.markdown(
                '<div class="kp-results-status">',
                unsafe_allow_html=True,
            )
            st.success(f"Все {total} тем найдены в программе")
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.warning(f"Найдено {matched} из {total} тем в программе")
    elif program is None:
        st.info("Образовательная программа не загружена.")

    if totals:
        st.markdown(
            '<div class="kp-results-status">',
            unsafe_allow_html=True,
        )
        st.success(
            f"{totals.total} часа распределены на {len(schedule.weeks)} учебных недель."
        )
        st.markdown("</div>", unsafe_allow_html=True)

    if detail_warnings:
        st.info(
            "Некоторые поля отсутствуют в исходных документах. "
            "Приложение сможет дополнить их при формировании плана."
        )
        with st.expander("Подробнее"):
            for warning in detail_warnings:
                st.write(f"• {warning}")

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
) -> None:
    st.subheader("Формирование календарного плана")

    generation_pending = bool(st.session_state.get("calendar_generation_pending"))
    generation_requested = st.button(
        "Сформировать календарный план",
        type="primary",
        use_container_width=True,
        disabled=generation_pending,
    )
    if generation_requested:
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

    generation_error = st.session_state.get("calendar_generation_error")
    if generation_error:
        st.error(f"Не удалось сформировать календарный план: {generation_error}")

    if st.session_state.get("calendar_generation_succeeded"):
        st.success("Календарный план готов")
        for warning in st.session_state.get("calendar_warnings", ()):
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
    ) = _render_upload_screen()

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
        if st.button("Заменить документы", key="replace_documents"):
            _reset_analysis_state()
            st.rerun()

        if validated_utp.filename.startswith("УТП из файла"):
            st.info("Учебно-тематический план найден внутри программы обучения.")

        _render_teacher_analysis_screen(
            utp=utp,
            program=program,
            schedule=schedule,
            matches=matches,
            detail_warnings=tuple(detail_warnings),
            academic_year=academic_year,
            source_utp_name=validated_utp.filename,
            program_filename=(
                validated_program.filename if validated_program is not None else None
            ),
        )
        _show_generation_controls(
            validated_utp=validated_utp,
            validated_program=validated_program,
            template_selection=template_selection,
            academic_year=academic_year,
            group_number=group_number,
            class_name=class_name,
        )
