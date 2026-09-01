"""Пользовательский интерфейс формирования календарного плана."""

from __future__ import annotations

import os

import streamlit as st

from calendar_pedagoga.ai_provider import AIProviderError
from calendar_pedagoga.content_generation import CalendarContentRow, build_content_model
from calendar_pedagoga.lesson_content import build_lesson_content, calculate_fill_metrics
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
from calendar_pedagoga.transient_documents import TransientDocumentSession
from calendar_pedagoga.upload_validation import (
    MAX_UPLOAD_BYTES,
    UploadPurpose,
    UploadValidationError,
    ValidatedUpload,
    validate_upload,
)
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.matching import MatchStatus, match_utp_to_program
from calendar_pedagoga.program_parsing import ProgramData
from calendar_pedagoga.scheduling import (
    ScheduleResult,
    ScheduleValidationError,
    build_schedule,
)


SUPPORTED_ACADEMIC_YEAR = "2026–2027"


def _value(value: object | None) -> str:
    return str(value) if value is not None else "Не найдено"


def _show_utp(result: UtpParseResult) -> None:
    metadata = result.metadata
    st.subheader("Что найдено в УТП")
    st.write(
        {
            "Название программы": _value(metadata.program_name),
            "Учебный год": _value(metadata.academic_year),
            "Год обучения": _value(metadata.study_year),
            "Возраст обучающихся": _value(metadata.student_age),
            "Часов в неделю": _value(metadata.hours_per_week),
            "Часов в год (информационная справка)": _value(
                metadata.hours_per_year
            ),
            "Учебных недель": _value(metadata.study_weeks),
            "Педагог": _value(metadata.teacher_name),
            "Количество разделов": len(result.sections),
            "Количество учебных тем/позиций": len(result.topics),
        }
    )

    if result.sections:
        st.markdown("**Разделы**")
        st.table(
            [
                {
                    "№": section.number or "",
                    "Раздел": section.title,
                    "Всего": section.hours.total,
                    "Теория": section.hours.theory,
                    "Практика": section.hours.practice,
                    "Тип": (
                        "Самостоятельная учебная позиция"
                        if section.is_standalone_position
                        else "Раздел с дочерними темами"
                    ),
                }
                for section in result.sections
            ]
        )

    st.markdown("**Учебные темы/позиции для календаря**")
    st.dataframe(
        [
            {
                "№": topic.number or "",
                "Тема": topic.title,
                "Родительский раздел": topic.parent_section or "",
                "Тип": (
                    "Самостоятельная позиция раздела"
                    if topic.is_standalone_section
                    else "Дочерняя тема"
                ),
                "Всего": topic.hours.total,
                "Теория": topic.hours.theory,
                "Практика": topic.hours.practice,
            }
            for topic in result.topics
        ],
        hide_index=True,
        use_container_width=True,
    )

    totals = result.table_totals
    st.markdown("**Контрольные суммы по итоговой строке УТП**")
    if totals is None:
        st.warning("Итоговая строка УТП не найдена.")
    else:
        st.write(
            f"Всего: **{totals.total}** · Теория: **{totals.theory}** · "
            f"Практика: **{totals.practice}**"
        )
    for warning in result.warnings:
        st.warning(warning)


def _preview(value: str, limit: int = 320) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _show_program(program: ProgramData, utp: UtpParseResult) -> None:
    st.subheader("Что найдено в образовательной программе")
    st.write(
        {
            "Название программы": _value(program.title),
            "Срок реализации": _value(program.duration),
            "Возраст обучающихся": _value(program.student_age),
            "Цель": _value(program.goal),
            "Задачи": list(program.tasks) or ["Не найдены"],
            "Формы организации занятий": list(program.lesson_forms)
            or ["Не найдены"],
            "Методы обучения": list(program.teaching_methods) or ["Не найдены"],
            "Ожидаемые результаты": list(program.expected_results)
            or ["Не найдены"],
        }
    )
    matches = match_utp_to_program(utp.topics, program.content_items)
    st.subheader("Сопоставление УТП и программы")
    st.dataframe(
        [
            {
                "Раздел УТП": match.utp_position.parent_section or "",
                "Тема УТП": match.utp_position.title,
                "Часы": match.utp_position.hours.total,
                "Раздел/тема программы": (
                    match.program_item.title if match.program_item else ""
                ),
                "Содержание": (
                    _preview(match.program_item.content)
                    if match.program_item
                    else ""
                ),
                "Статус": match.status.value,
                "Уверенность": f"{match.confidence:.0%}",
            }
            for match in matches
        ],
        hide_index=True,
        use_container_width=True,
    )
    matched = sum(match.status is not MatchStatus.NOT_MATCHED for match in matches)
    st.write(f"Сопоставлено: **{matched} из {len(matches)}**.")
    ambiguous = [match for match in matches if match.ambiguous_candidates]
    if ambiguous:
        for match in ambiguous:
            st.warning(
                f"Неоднозначное соответствие для «{match.utp_position.title}»: "
                + "; ".join(match.ambiguous_candidates)
            )


def _show_schedule(utp: UtpParseResult, academic_year: str) -> ScheduleResult:
    schedule = build_schedule(utp, academic_year)
    rows: list[dict[str, object]] = []
    row_keys: dict[tuple[int, str | None, str], int] = {}
    for element in schedule.elements:
        key = (element.week.number, element.topic_number, element.topic)
        if key not in row_keys:
            row_keys[key] = len(rows)
            rows.append(
                {
                    "Неделя": element.week.number,
                    "Даты": element.week.date_range,
                    "Месяц": element.week.month,
                    "Раздел": element.section,
                    "Тема": element.topic,
                    "Теория": 0,
                    "Практика": 0,
                    "Всего часов": 0,
                }
            )
        row = rows[row_keys[key]]
        column = "Теория" if element.part_type == "theory" else "Практика"
        row[column] = int(row[column]) + element.hours
        row["Всего часов"] = int(row["Всего часов"]) + element.hours
    st.subheader("Календарное распределение")
    st.dataframe(rows, hide_index=True, use_container_width=True)
    totals = utp.table_totals
    if totals:
        st.write(
            f"Распределено: **{totals.total} ч.** · теория: "
            f"**{totals.theory} ч.** · практика: **{totals.practice} ч.** · "
            f"учебных недель: **{len(schedule.weeks)}**."
        )
    return schedule


def _show_content_sources(
    schedule: ScheduleResult,
    utp: UtpParseResult,
    program: ProgramData | None,
    source_utp_name: str,
) -> tuple[CalendarContentRow, ...]:
    rows = build_content_model(schedule, utp, program, source_utp_name)
    st.subheader("Источники содержания календаря")
    st.dataframe(
        [
            {
                "Неделя": row.week_number,
                "Даты": row.date_range,
                "Тема УТП": row.topic_title,
                "Теория": row.theory_hours,
                "Практика": row.practice_hours,
                "Тема программы": row.program_topic,
                "Статус": row.match_status.value,
                "Содержание программы": row.program_content_preview,
            }
            for row in rows
        ],
        hide_index=True,
        use_container_width=True,
    )
    warnings = sorted({warning for row in rows for warning in row.warnings})
    for warning in warnings:
        st.warning(warning)
    return rows


def _show_lesson_content(rows: tuple[CalendarContentRow, ...]) -> None:
    lessons = build_lesson_content(rows)
    st.subheader("Поля календарного плана — rule-based")
    st.dataframe(
        [
            {
                "Неделя": row.source.week_number,
                "Тема": row.source.topic_title,
                "Теоретические занятия": _preview(row.theory_text),
                "Практические занятия": _preview(row.practice_text),
                "Тип занятия": row.lesson_type,
                "Планируемый результат": row.planned_result,
                "Вид контроля": row.assessment_method,
            }
            for row in lessons
        ],
        hide_index=True,
        use_container_width=True,
    )
    metrics = calculate_fill_metrics(lessons)
    st.write(
        f"Заполненность: теория **{metrics.theory_percent:.1f}%**, "
        f"практика **{metrics.practice_percent:.1f}%**, "
        f"тип занятия **{metrics.lesson_type_percent:.1f}%**, "
        f"результат **{metrics.planned_result_percent:.1f}%**, "
        f"контроль **{metrics.assessment_method_percent:.1f}%**."
    )
    warnings = sorted({warning for row in lessons for warning in row.warnings})
    for warning in warnings:
        st.warning(warning)


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
) -> None:
    st.subheader("Формирование календарного плана")
    ai_available = bool(os.getenv("OPENAI_API_KEY"))
    use_ai = False
    if validated_program is None:
        st.info(
            "Без образовательной программы календарь формируется с пустым "
            "содержанием занятий; AI недоступен."
        )
    elif ai_available:
        use_ai = st.checkbox(
            "Дополнить поля занятий через OpenAI",
            help=(
                "AI заполняет тип занятия, результат и контроль только из "
                "данных программы. Ключ API берётся из OPENAI_API_KEY."
            ),
        )
    else:
        st.caption(
            "OpenAI API не настроен (OPENAI_API_KEY). "
            "Будут использованы только rule-based поля."
        )

    if st.button("Сформировать календарный план", type="primary", use_container_width=True):
        utp = validated_utp.parsed
        assert isinstance(utp, UtpParseResult)
        program = None
        if validated_program is not None:
            program = validated_program.parsed
            assert isinstance(program, ProgramData)

        with TransientDocumentSession() as operation:
            try:
                result = run_calendar_pipeline(
                    utp,
                    program,
                    academic_year=academic_year,
                    template=template_selection,
                    source_utp_name=validated_utp.filename,
                    use_ai=use_ai,
                )
            except (PipelineError, ScheduleValidationError, ValueError, AIProviderError) as error:
                st.error(f"Не удалось сформировать календарный план: {error}")
                return

            operation.publish_result(result.filename, result.content)
            st.session_state["calendar_download"] = operation.take_result_for_download()
            st.session_state["calendar_warnings"] = result.warnings
            if result.ai_usage:
                st.session_state["calendar_ai_usage"] = {
                    "tokens": result.ai_usage.total_tokens,
                    "cost": result.ai_usage.estimated_cost_usd,
                }

        st.success("Календарный план сформирован и прошёл QA.")
        for warning in st.session_state.get("calendar_warnings", ()):
            st.warning(warning)
        usage = st.session_state.get("calendar_ai_usage")
        if usage:
            st.caption(
                f"AI: {usage['tokens']} токенов, "
                f"≈ ${usage['cost']:.4f}."
            )

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
    st.set_page_config(page_title="Календарь педагога", page_icon="📅")

    st.title("Календарь педагога")
    st.write(
        "Загрузите учебно-тематический план и образовательную программу, "
        "проверьте данные и сформируйте календарный план DOCX."
    )

    utp_file = st.file_uploader(
        "Загрузите УТП",
        type=("docx",),
        help="УТП — учебно-тематический план, DOCX, до 10 МБ.",
    )
    program_file = st.file_uploader(
        "Загрузите образовательную программу",
        type=("doc", "docx"),
        help="Программа — образовательная программа, DOC/DOCX, до 10 МБ.",
    )
    organization_template_file = st.file_uploader(
        "Шаблон календарного плана вашей организации",
        type=("docx",),
        help=(
            "Шаблон — только образец календарного плана организации, "
            "DOCX, до 10 МБ."
        ),
    )
    academic_year = st.selectbox(
        "Учебный год",
        options=(SUPPORTED_ACADEMIC_YEAR,),
        index=0,
        help="Расписание поддерживает учебный год 2026–2027 (36 недель).",
    )
    normative_registry = get_builtin_normative_registry()
    registry_snapshot = normative_registry.current
    with st.expander("Нормативная база"):
        st.caption(
            "Нормативная база носит справочный характер и не меняет "
            "календарь автоматически."
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

    if st.button("Проверить документы", type="primary", use_container_width=True):
        if utp_file is None:
            st.warning("Загрузите УТП.")
            return

        with TransientDocumentSession() as uploads:
            uploads.replace(UploadPurpose.UTP, utp_file.name, utp_file.getvalue())
            if program_file is not None:
                uploads.replace(
                    UploadPurpose.PROGRAM,
                    program_file.name,
                    program_file.getvalue(),
                )
            if organization_template_file is not None:
                uploads.replace(
                    UploadPurpose.CALENDAR_TEMPLATE,
                    organization_template_file.name,
                    organization_template_file.getvalue(),
                )
            try:
                transient_utp = uploads.get(UploadPurpose.UTP)
                assert transient_utp is not None
                validated_utp = validate_upload(
                    UploadPurpose.UTP,
                    transient_utp.filename,
                    transient_utp.content,
                )
                transient_program = uploads.get(UploadPurpose.PROGRAM)
                validated_program = (
                    validate_upload(
                        UploadPurpose.PROGRAM,
                        transient_program.filename,
                        transient_program.content,
                    )
                    if transient_program is not None
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
            except UploadValidationError as error:
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
        st.success("Документы проверены и готовы к анализу.")
        st.write(f"**УТП:** {validated_utp.filename}")
        st.write(
            f"**Образовательная программа:** "
            f"{validated_program.filename if validated_program else 'не загружена'}"
        )
        st.write(f"**Учебный год:** {academic_year}")
        st.write(
            "**Шаблон календарного плана:** "
            + (
                template_selection.filename or "шаблон организации"
                if template_selection.uses_organization_template
                else "стандартный шаблон приложения"
            )
        )
        st.caption(
            f"Загрузки проверяются в памяти и не сохраняются на сервере. "
            f"Лимит: {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ."
        )
        utp = validated_utp.parsed
        assert isinstance(utp, UtpParseResult)
        _show_utp(utp)
        program = None
        if validated_program is not None:
            program = validated_program.parsed
            assert isinstance(program, ProgramData)
            _show_program(program, utp)
        try:
            schedule = _show_schedule(utp, academic_year)
            content_rows = _show_content_sources(schedule, utp, program, utp_file.name)
            _show_lesson_content(content_rows)
            _store_analysis_context(
                validated_utp=validated_utp,
                validated_program=validated_program,
                template_selection=template_selection,
                academic_year=academic_year,
            )
            _show_generation_controls(
                validated_utp=validated_utp,
                validated_program=validated_program,
                template_selection=template_selection,
                academic_year=academic_year,
            )
        except (ScheduleValidationError, ValueError) as error:
            st.error(f"Не удалось построить календарное распределение: {error}")
