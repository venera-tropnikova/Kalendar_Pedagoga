from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from calendar_pedagoga import ui
from calendar_pedagoga.content_engine_v2 import (
    LessonContentV2Row,
    REQUIRED_ACTION,
    derive_fields_v2,
)
from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.semantic_review import (
    ManualSemanticConfirmation,
    build_semantic_review_cases,
)


def _source(week: int, text: str = "Выполнение упражнения.") -> CalendarContentRow:
    return CalendarContentRow(
        week_number=week,
        date_range="01–07.09",
        month="Сентябрь",
        section="Раздел",
        topic_number=str(week),
        topic_title=f"Тема {week}",
        source_topic_title=f"Тема {week}",
        theory_hours=0,
        practice_hours=2,
        total_hours=2,
        match_status=MatchStatus.EXACT,
        program_section="Раздел",
        program_topic=f"Тема {week}",
        program_content_full=text,
        program_content_preview=text,
        source_program_name="Программа",
        source_utp_name="utp.docx",
    )


def _review_row(week: int = 1) -> LessonContentV2Row:
    source = _source(week)
    derived = derive_fields_v2(
        topic_title=source.topic_title,
        theory_text="",
        practice_text=source.program_content_full,
        program_content=source.program_content_full,
        theory_hours=0,
        practice_hours=2,
    )
    clause = "Выполнение упражнения"
    return LessonContentV2Row(
        source=source,
        theory_text=derived.theory_text,
        practice_text=derived.practice_text,
        lesson_type=derived.lesson_type,
        planned_result=derived.planned_result,
        assessment_method=derived.assessment_method,
        action=derived.frame.action,
        object=derived.frame.object,
        conditions=derived.frame.conditions,
        warnings=(f"NEEDS_REVIEW: {clause}",),
        clause_coverage=((clause, "NEEDS_REVIEW"),),
        clause_roles=((clause, REQUIRED_ACTION),),
    )


def _valid_text() -> tuple[str, str]:
    return (
        "Выполняет упражнение.",
        "педагогическое наблюдение за выполнением упражнения",
    )


def _empty_review_row(week: int = 1) -> LessonContentV2Row:
    return replace(_review_row(week), planned_result="", assessment_method="")


def test_review_required_renders_only_blocked_weeks() -> None:
    blocked = _empty_review_row(1)
    covered = replace(
        _review_row(2),
        clause_coverage=(("Выполнение упражнения", "COVERED"),),
        warnings=(),
    )
    cases = build_semantic_review_cases(
        (blocked, covered), context_fingerprint="scope"
    )
    markdown: list[str] = []
    codes: list[str] = []
    text_areas: list[str] = []
    buttons: list[str] = []
    state: dict = {}
    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui.st, "markdown", side_effect=lambda text, **_: markdown.append(text)),
        patch.object(ui.st, "write", lambda *_, **__: None),
        patch.object(ui.st, "code", side_effect=lambda text, **_: codes.append(text)),
        patch.object(
            ui.st,
            "text_area",
            side_effect=lambda label, **_: text_areas.append(label) or "",
        ),
        patch.object(
            ui.st,
            "button",
            side_effect=lambda label, **_: buttons.append(label) or False,
        ),
        patch.object(ui.st, "error"),
        patch.object(ui.st, "success"),
    ):
        blocked_for_docx = ui._render_semantic_review_section(
            scope="scope", cases=cases, rows=(blocked, covered)
        )
    text = "\n".join(markdown)
    assert blocked_for_docx
    assert "Требуется заполнить: 1 неделя" in text
    assert "Требуется подтверждение содержания" not in text
    assert "Неделя №1" in text
    assert "Неделя №2" not in text
    assert "Тема 1" in text
    assert "Причины NEEDS_REVIEW" in text
    assert "Предложенный RESULT" in text
    assert "Предложенный CONTROL" in text
    assert text_areas == ["Результат педагога", "Контроль педагога"]
    assert buttons == ["Подтвердить"]


def test_filled_review_weeks_are_notices_without_manual_input() -> None:
    filled = _review_row(3)
    empty = _empty_review_row(17)
    cases = build_semantic_review_cases(
        (filled, empty), context_fingerprint="scope"
    )
    markdown: list[str] = []
    text_areas: list[str] = []
    buttons: list[str] = []
    writes: list[str] = []
    state: dict = {}
    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui.st, "markdown", side_effect=lambda text, **_: markdown.append(text)),
        patch.object(ui.st, "write", side_effect=lambda text, **_: writes.append(str(text))),
        patch.object(ui.st, "code"),
        patch.object(
            ui.st,
            "text_area",
            side_effect=lambda label, **_: text_areas.append(label) or "",
        ),
        patch.object(
            ui.st,
            "button",
            side_effect=lambda label, **_: buttons.append(label) or False,
        ),
        patch.object(ui.st, "error"),
        patch.object(ui.st, "success"),
    ):
        still_empty = ui._render_semantic_review_section(
            scope="scope", cases=cases, rows=(filled, empty)
        )
    text = "\n".join(markdown)
    assert still_empty
    assert "Есть замечания: 1 неделя" in text
    assert "Требуется заполнить: 1 неделя" in text
    assert "Неделя №3" in text
    assert "Неделя №17" in text
    assert "Ручное подтверждение не требуется." in "\n".join(writes)
    assert text_areas == ["Результат педагога", "Контроль педагога"]
    assert buttons == ["Изменить формулировку", "Подтвердить"]


def test_valid_confirmation_is_saved_and_revalidated() -> None:
    row = _review_row()
    case = build_semantic_review_cases((row,), context_fingerprint="scope")[0]
    result, control = _valid_text()
    state: dict = {}
    with patch.object(ui.st, "session_state", state):
        issues = ui._store_semantic_confirmation(
            scope="scope",
            case=case,
            row=row,
            planned_result=result,
            assessment_method=control,
        )
    assert issues == ()
    stored = state["semantic_review_confirmations"][case.review_id]
    assert stored.planned_result == result
    assert stored.assessment_method == control
    assert state["calendar_generate_after_check"] is True


def test_valid_card_is_read_only_and_offers_change() -> None:
    row = _empty_review_row()
    case = build_semantic_review_cases((row,), context_fingerprint="scope")[0]
    result, control = _valid_text()
    confirmation = ManualSemanticConfirmation(
        case.review_id,
        case.source_fingerprint,
        result,
        control,
    )
    state = {
        "semantic_review_scope": "scope",
        "semantic_review_confirmations": {case.review_id: confirmation},
        "semantic_review_issues": {},
    }
    areas: list[tuple[str, bool]] = []
    buttons: list[str] = []
    progress: list[str] = []
    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui.st, "markdown"),
        patch.object(ui.st, "write", side_effect=lambda text, **_: progress.append(text)),
        patch.object(ui.st, "code"),
        patch.object(
            ui.st,
            "text_area",
            side_effect=lambda label, **kwargs: areas.append(
                (label, bool(kwargs.get("disabled")))
            ) or kwargs.get("value", ""),
        ),
        patch.object(
            ui.st,
            "button",
            side_effect=lambda label, **_: buttons.append(label) or False,
        ),
        patch.object(ui.st, "success"),
        patch.object(ui.st, "error"),
    ):
        assert not ui._render_semantic_review_section(
            scope="scope", cases=(case,), rows=(row,)
        )
    assert progress == ["Подтверждено 1 из 1"]
    assert areas == [
        ("Результат педагога", True),
        ("Контроль педагога", True),
    ]
    assert buttons == ["Изменить формулировку"]


def test_invalid_confirmation_remains_blocked_with_issues() -> None:
    row = _review_row()
    cases = build_semantic_review_cases((row,), context_fingerprint="scope")
    state: dict = {}
    with patch.object(ui.st, "session_state", state):
        issues = ui._store_semantic_confirmation(
            scope="scope",
            case=cases[0],
            row=row,
            planned_result="",
            assessment_method="",
        )
        valid, pending = ui._validated_semantic_confirmations(
            cases=cases,
            rows=(row,),
            confirmations=state["semantic_review_confirmations"],
        )
    assert "RESULT не заполнен" in issues
    assert valid == {}
    assert pending == cases


def test_one_confirmation_does_not_unlock_other_week() -> None:
    rows = (_review_row(1), _review_row(2))
    cases = build_semantic_review_cases(rows, context_fingerprint="scope")
    result, control = _valid_text()
    confirmation = ManualSemanticConfirmation(
        review_id=cases[0].review_id,
        source_fingerprint=cases[0].source_fingerprint,
        planned_result=result,
        assessment_method=control,
    )
    valid, pending = ui._validated_semantic_confirmations(
        cases=cases,
        rows=rows,
        confirmations={cases[0].review_id: confirmation},
    )
    assert tuple(valid) == (cases[0].review_id,)
    assert [case.week_number for case in pending] == [2]


def test_changed_scope_drops_stale_confirmations() -> None:
    state: dict = {}
    with patch.object(ui.st, "session_state", state):
        first = ui._semantic_review_confirmations_for_scope("old")
        first["old-id"] = object()
        state["semantic_review_issues"]["old-id"] = ("old",)
        second = ui._semantic_review_confirmations_for_scope("new")
    assert second == {}
    assert state["semantic_review_scope"] == "new"
    assert state["semantic_review_issues"] == {}


def test_all_confirmed_allows_generation_and_passes_mapping() -> None:
    confirmation = ManualSemanticConfirmation("id", "fingerprint", "R", "C")
    state = {
        "calendar_generate_after_check": True,
        "calendar_generation_inputs": "inputs",
        "calendar_busy": False,
    }
    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui, "_generator_revision", return_value="revision"),
        patch.object(ui, "_LOADED_GENERATOR_REVISION", "revision"),
        patch.object(ui, "_execute_calendar_generation") as execute,
        patch.object(ui, "_render_generation_result"),
        patch.object(ui, "_show_generation_result"),
        patch.object(ui.st, "rerun"),
    ):
        ui._show_generation_controls(
            validated_utp=object(),
            validated_program=None,
            template_selection=object(),
            academic_year="2026–2027",
            group_number="",
            class_name="",
            teacher_name="",
            semantic_review_blocked=False,
            manual_confirmations={"id": confirmation},
        )
    assert execute.call_count == 1
    assert execute.call_args.kwargs["manual_confirmations"] == {"id": confirmation}


def test_pending_review_allows_draft_docx_generation() -> None:
    state = {
        "calendar_generate_after_check": True,
        "calendar_generation_inputs": "inputs",
        "calendar_busy": False,
    }
    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui, "_generator_revision", return_value="revision"),
        patch.object(ui, "_LOADED_GENERATOR_REVISION", "revision"),
        patch.object(ui, "_execute_calendar_generation") as execute,
        patch.object(ui, "_render_generation_result"),
        patch.object(ui, "_show_generation_result"),
        patch.object(ui.st, "rerun"),
    ):
        ui._show_generation_controls(
            validated_utp=object(),
            validated_program=None,
            template_selection=object(),
            academic_year="2026–2027",
            group_number="",
            class_name="",
            teacher_name="",
            semantic_review_blocked=True,
            manual_confirmations={},
        )
    execute.assert_called_once()


def test_ready_plan_message_keeps_review_count_in_ui() -> None:
    assert ui._ready_plan_message(0) == "Календарный план готов"
    assert ui._ready_plan_message(1) == (
        "Календарный план не готов к выдаче. Есть замечания: 1 неделя"
    )
    assert ui._ready_plan_message(18) == (
        "Календарный план не готов к выдаче. Есть замечания: 18 недель"
    )
    assert ui._fill_required_message(1) == "Требуется заполнить: 1 неделя"
    assert ui._fill_required_message(2) == "Требуется заполнить: 2 недели"


def test_resolved_empty_cells_require_fill() -> None:
    row = _review_row(17)
    cases = build_semantic_review_cases((row,), context_fingerprint="scope")
    resolved = SimpleNamespace(
        source=SimpleNamespace(source=SimpleNamespace(week_number=17)),
        planned_result="",
        assessment_method="",
    )
    markdown: list[str] = []
    text_areas: list[str] = []
    state = {"calendar_resolved_lessons": (resolved,)}
    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui.st, "markdown", side_effect=lambda text, **_: markdown.append(text)),
        patch.object(ui.st, "write", lambda *_, **__: None),
        patch.object(ui.st, "code"),
        patch.object(
            ui.st,
            "text_area",
            side_effect=lambda label, **_: text_areas.append(label) or "",
        ),
        patch.object(ui.st, "button", return_value=False),
        patch.object(ui.st, "error"),
        patch.object(ui.st, "success"),
    ):
        assert ui._render_semantic_review_section(
            scope="scope", cases=cases, rows=(row,)
        )
    assert "Требуется заполнить: 1 неделя" in "\n".join(markdown)
    assert text_areas == ["Результат педагога", "Контроль педагога"]


def test_no_cases_do_not_render_review_ui() -> None:
    state: dict = {}
    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui.st, "markdown") as markdown,
    ):
        assert not ui._render_semantic_review_section(
            scope="climb", cases=(), rows=()
        )
    markdown.assert_not_called()


def test_readiness_block_shows_codes_missing_fields_and_action() -> None:
    source = replace(
        _source(4, ""),
        match_status=MatchStatus.NOT_MATCHED,
        program_content_full="",
        program_content_preview="",
    )
    row = replace(
        _empty_review_row(4),
        source=source,
        theory_text="",
        practice_text="",
        warnings=("Недостаточно данных источника; использован безопасный fallback.",),
        clause_coverage=(),
        clause_roles=(),
    )
    cases = build_semantic_review_cases((row,), context_fingerprint="scope")
    markdown: list[str] = []
    with (
        patch.object(ui.st, "session_state", {}),
        patch.object(ui.st, "markdown", side_effect=lambda text, **_: markdown.append(text)),
        patch.object(ui.st, "write"),
        patch.object(ui.st, "code"),
        patch.object(ui.st, "text_area", return_value=""),
        patch.object(ui.st, "button", return_value=False),
        patch.object(ui.st, "error"),
        patch.object(ui.st, "success"),
    ):
        ui._render_semantic_review_section(scope="scope", cases=cases, rows=(row,))
    text = "\n".join(markdown)
    assert "Неделя №4" in text
    assert "Неделя заблокирована" in text
    assert "SOURCE_NOT_MATCHED" in text
    assert "EMPTY_RESULT" in text
    assert "Отсутствует:" in text
    assert "SOURCE" in text
    assert "Что сделать:" in text
    assert "Сопоставьте фрагмент содержания программы" in text
