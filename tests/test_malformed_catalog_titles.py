"""Recover work titles from a damaged quoted catalog without inventing new ones."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from calendar_pedagoga.confirmed_slot_allocation import (
    recover_quoted_catalog_works,
    split_confirmed_source,
)
from calendar_pedagoga.content_engine_v2 import (
    PROVENANCE_GENERIC_ONLY,
    PROVENANCE_SENTENCE_FRAME_CLOSED,
    REQUIRED_ACTION,
)
from calendar_pedagoga.pipeline import (
    CalendarDocumentStatus,
    _build_pipeline_lesson_content_outcome,
)
from calendar_pedagoga.production_readiness import (
    GENERIC_ONLY,
    row_blocks_final_delivery,
)
from calendar_pedagoga.sentence_frame import frames_for_confirmed_part
from calendar_pedagoga import ui
from tests.test_production_final_ready_gate import _outcome, _row
from tests.test_sentence_frame import _build, _part


HOLDOUT_SOURCE = (
    "Практические работы: «Роспись на камне», "
    "«Украшения в технике «папье-маше», «Украшения из ракушек»."
)
RECOVERED = (
    "«Роспись на камне»",
    "«Украшения в технике папье-маше»",
    "«Украшения из ракушек»",
)
NAMED_RESULT = "Выполняет практическую работу «Украшения в технике папье-маше»."


def test_holdout_source_recovers_three_work_titles() -> None:
    assert recover_quoted_catalog_works(
        "«Роспись на камне», «Украшения в технике «папье-маше», "
        "«Украшения из ракушек»."
    ) == RECOVERED
    split = split_confirmed_source(
        HOLDOUT_SOURCE,
        topic_theory_hours=0,
        topic_practice_hours=9,
    )
    assert split.practice_units == RECOVERED
    assert split.unresolved_reason is None


def test_balanced_quoted_catalog_stays_intact() -> None:
    source = "Практические работы: «План участка», «Профиль болота»."
    split = split_confirmed_source(
        source,
        topic_theory_hours=0,
        topic_practice_hours=4,
    )
    assert split.practice_units == ("«План участка»", "«Профиль болота»")


def test_period_inside_balanced_quotes_stays_one_title() -> None:
    source = "Практические работы: «Плетение из бумаги», «Изонить. Открытка»."
    split = split_confirmed_source(
        source,
        topic_theory_hours=0,
        topic_practice_hours=6,
    )
    assert split.practice_units == (
        "«Плетение из бумаги»",
        "«Изонить. Открытка»",
    )


def test_unclosed_title_without_boundary_is_not_a_closed_work() -> None:
    damaged = "Практические работы: «Украшения в технике «папье-маше»."
    assert recover_quoted_catalog_works(
        "«Украшения в технике «папье-маше»."
    ) is None
    split = split_confirmed_source(
        damaged,
        topic_theory_hours=0,
        topic_practice_hours=3,
    )
    assert "«Украшения в технике папье-маше»" not in split.practice_units
    row = _build(
        _part(
            topic_title="Декоративные украшения",
            practice_hours=3,
            content=damaged,
        )
    )
    assert row.planned_result != NAMED_RESULT
    assert PROVENANCE_GENERIC_ONLY in row.provenance_codes


def test_descriptive_source_is_not_recovered_as_work_titles() -> None:
    prose = "Понятие «форма» и свойство «фактура» изучаются на образцах."
    assert recover_quoted_catalog_works(prose) is None
    short = "«форма», «цвет»."
    assert recover_quoted_catalog_works(short) is None


def test_recovered_title_builds_named_work_frame() -> None:
    frames = frames_for_confirmed_part(
        _part(
            topic_title="Декоративные украшения",
            practice_hours=3,
            content="Практические работы: «Украшения в технике папье-маше».",
        ),
        topic="Декоративные украшения",
    )
    assert len(frames) == 1
    assert frames[0].proven
    assert frames[0].object == (
        "практическую работу «Украшения в технике папье-маше»"
    )


def test_filled_closed_notice_does_not_block_final_ready(monkeypatch) -> None:
    row = _row(
        planned_result="Выполняет практическую работу «Изонить. Открытка».",
        assessment_method=(
            "Педагогическое наблюдение за выполнением практической работы "
            "«Изонить. Открытка»."
        ),
        provenance_codes=(PROVENANCE_SENTENCE_FRAME_CLOSED,),
        clause_coverage=(("Открытка»", "NEEDS_REVIEW"),),
        clause_roles=(("Открытка»", REQUIRED_ACTION),),
        warnings=("NEEDS_REVIEW: Открытка»",),
    )
    assert not row_blocks_final_delivery(row)
    outcome = _outcome(row, monkeypatch)
    assert outcome.status is CalendarDocumentStatus.FINAL_READY
    assert outcome.review_cases
    assert outcome.review_cases[0].blocks_delivery is False
    assert outcome.review_cases[0].week_number == 1


def test_generic_only_stays_a_blocker(monkeypatch) -> None:
    row = _row(
        planned_result="Выполняет практическую работу по теме «Декоративные украшения».",
        assessment_method=(
            "Педагогическое наблюдение в ходе выполнения задания по теме "
            "«Декоративные украшения»."
        ),
        provenance_codes=(PROVENANCE_GENERIC_ONLY, PROVENANCE_SENTENCE_FRAME_CLOSED),
        clause_coverage=(),
        clause_roles=(),
    )
    assert row_blocks_final_delivery(row)
    outcome = _outcome(row, monkeypatch)
    assert outcome.status is CalendarDocumentStatus.DRAFT_READY
    assert GENERIC_ONLY in outcome.review_cases[0].reasons
    assert outcome.review_cases[0].blocks_delivery is True


def test_empty_result_stays_a_blocker(monkeypatch) -> None:
    row = _row(planned_result="")
    assert row_blocks_final_delivery(row)
    outcome = _outcome(row, monkeypatch)
    assert outcome.status is CalendarDocumentStatus.DRAFT_READY


def test_notices_keep_final_download_button() -> None:
    notice = SimpleNamespace(
        week_number=23,
        blocks_delivery=False,
        proposed_result="Выполняет практическую работу «Изонить. Открытка».",
        proposed_control="Педагогическое наблюдение.",
        reasons=("Открытка»",),
    )
    markdown: list[str] = []
    labels: list[str] = []
    buttons: list[str] = []
    state = {
        "calendar_download": SimpleNamespace(content=b"docx", filename="plan.docx"),
        "calendar_context": {"academic_year": "2026–2027"},
        "calendar_document_status": CalendarDocumentStatus.FINAL_READY.value,
        "semantic_review_pipeline_cases": (notice,),
        "calendar_generation_invalidated": False,
        "calendar_generation_error": None,
    }
    with (
        patch.object(ui, "_active_remote_job_handle", return_value=object()),
        patch.object(ui, "_poll_active_remote_job"),
        patch.object(ui.st, "session_state", state),
        patch.object(ui.st, "markdown", side_effect=lambda text, **_: markdown.append(text)),
        patch.object(
            ui.st,
            "download_button",
            side_effect=lambda label, **_: labels.append(label) or False,
        ),
        patch.object(
            ui.st,
            "button",
            side_effect=lambda label, **_: buttons.append(label) or False,
        ),
        patch.object(ui.st, "info"),
        patch.object(ui.st, "error"),
    ):
        ui._render_generation_result(show_status=False)
    text = "\n".join(markdown)
    assert "Календарный план готов" in text
    assert "не готов" not in text
    assert "Ручное подтверждение не требуется." in text
    assert labels == ["Скачать календарный план за 2026–2027 учебный год"]
    assert buttons == ["Показать замечания"]
    assert ui._blocking_review_week_count((notice,)) == 0
    assert ui._notice_review_week_count((notice,)) == 1


def test_blocking_week_keeps_draft_button() -> None:
    blocker = SimpleNamespace(
        week_number=15,
        blocks_delivery=True,
        proposed_result="Выполняет практическую работу по теме «Декоративные украшения».",
        proposed_control="Педагогическое наблюдение.",
        reasons=(GENERIC_ONLY,),
    )
    assert ui._blocking_review_week_count((blocker,)) == 1
    assert ui._ready_plan_message(1).startswith("Календарный план не готов")
