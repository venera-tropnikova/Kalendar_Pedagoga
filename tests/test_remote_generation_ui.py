import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from calendar_pedagoga import ui
from calendar_pedagoga.pipeline import CalendarDocumentStatus, PipelineError, PipelineResult
from calendar_pedagoga.remote_generation import (
    DEFAULT_WAIT_TIMEOUT_SECONDS,
    REMOTE_JOB_EXPIRED_MESSAGE,
    REMOTE_JOB_LOST_MESSAGE,
    REMOTE_JOB_TIMEOUT_MESSAGE,
)


class _Plan:
    pass


class _Program:
    pass


def _handle(**overrides):
    payload = {
        "job_id": "abc",
        "started_at": time.time(),
        "fingerprint": ("inputs", "revision"),
        "job_state": "QUEUED",
        "phase": None,
    }
    payload.update(overrides)
    return payload


def _state_with_job(handle=None, **overrides):
    state = {
        "calendar_generation_inputs": "inputs",
        "calendar_generation_fingerprint": ("inputs", "revision"),
        "calendar_remote_job": handle if handle is not None else _handle(),
        "calendar_busy": True,
        "calendar_work_status": "Формируем календарный план…",
        "calendar_context": {"academic_year": "2026–2027"},
        "semantic_review_issues": {},
    }
    state.update(overrides)
    return state


def _job_status(*, job_state, phase=None, pipeline_status=None, **extra):
    payload = {
        "job_id": "abc",
        "job_state": job_state,
        "phase": phase,
        "pipeline_status": pipeline_status,
        "review_cases": [],
        "confirmation_errors": [],
        "warnings": [],
        "docx_available": extra.pop("docx_available", pipeline_status is not None),
        "filename": extra.pop("filename", "Plan.docx"),
        "error": extra.pop("error", None),
    }
    payload.update(extra)
    return payload


def _as_side_effect(value):
    if callable(value):
        return value
    return lambda *_args, **_kwargs: value


def _fragment(state, **patches):
    revision = patches.pop("_generator_revision", "revision")
    fetch = patches.pop("fetch_remote_calendar_job", _job_status(job_state="QUEUED"))
    download = patches.pop(
        "download_remote_calendar_document", ("Plan.docx", b"PK\x03\x04docx")
    )
    delete = patches.pop("delete_remote_calendar_job", None)
    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui, "_generator_revision", return_value=revision),
        patch.object(ui, "fetch_remote_calendar_job", side_effect=_as_side_effect(fetch)),
        patch.object(
            ui,
            "download_remote_calendar_document",
            side_effect=_as_side_effect(download),
        ),
        patch.object(
            ui, "delete_remote_calendar_job", side_effect=_as_side_effect(delete)
        ),
        patch.object(ui.st, "info") as info_mock,
        patch.object(ui.st, "error") as error_mock,
        patch.object(ui.st, "warning") as warning_mock,
        patch.object(ui.st, "download_button") as download_mock,
        patch.object(ui.st, "markdown") as markdown_mock,
        patch.object(ui.st, "button", return_value=False) as button_mock,
        patch.object(ui.st, "rerun"),
    ):
        ui._render_generation_result()
        return SimpleNamespace(
            info=info_mock,
            error=error_mock,
            warning=warning_mock,
            download_button=download_mock,
            markdown=markdown_mock,
            button=button_mock,
        )


def test_execute_submit_returns_without_polling(monkeypatch) -> None:
    monkeypatch.setenv("CALENDAR_GENERATION_API_URL", "http://generation.test")
    monkeypatch.setenv("CALENDAR_GENERATION_API_TOKEN", "token")
    monkeypatch.delenv("RENDER", raising=False)
    fetched: list[bool] = []

    def fetch(*_args, **_kwargs):
        fetched.append(True)
        raise AssertionError("submit must not poll")

    state = {
        "calendar_generation_fingerprint": ("inputs", "revision"),
        "calendar_work_status": "",
    }
    validated_utp = SimpleNamespace(filename="plan.docx", parsed=_Plan())
    validated_program = SimpleNamespace(
        filename="program.docx", content=b"program", parsed=_Program()
    )
    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui, "_generator_revision", return_value="revision"),
        patch.object(
            ui,
            "submit_remote_calendar_job",
            return_value={"job_id": "abc", "job_state": "QUEUED", "phase": None},
        ) as submit,
        patch.object(ui, "fetch_remote_calendar_job", side_effect=fetch),
        patch.object(ui, "_work_status_block") as status_block,
        patch.object(ui, "ConfirmedStudyPlan", _Plan),
        patch.object(ui, "UtpParseResult", _Plan),
        patch.object(ui, "ProgramData", _Program),
    ):
        widget = SimpleNamespace(update=lambda **_kwargs: None)
        status_block.return_value.__enter__.return_value = widget
        status_block.return_value.__exit__.return_value = None
        ui._execute_calendar_generation(
            validated_utp=validated_utp,
            validated_program=validated_program,
            template_selection=object(),
            academic_year="2026–2027",
            group_number="",
            class_name="",
            teacher_name="",
            reviews={},
        )
    submit.assert_called_once()
    assert fetched == []
    assert state["calendar_remote_job"]["job_id"] == "abc"
    assert state["calendar_remote_job"]["fingerprint"] == ("inputs", "revision")
    assert "started_at" in state["calendar_remote_job"]
    assert "calendar_download" not in state
    assert "calendar_generation_succeeded" not in state


def test_rerun_does_not_create_second_job() -> None:
    state = {
        "calendar_generate_after_check": True,
        "calendar_generation_inputs": "inputs",
        "calendar_generation_fingerprint": ("inputs", "revision"),
        "calendar_remote_job": _handle(),
        "calendar_busy": True,
    }
    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui, "_generator_revision", return_value="revision"),
        patch.object(ui, "_LOADED_GENERATOR_REVISION", "revision"),
        patch.object(ui, "_execute_calendar_generation") as execute,
        patch.object(ui, "_render_generation_result"),
        patch.object(ui, "_show_generation_result"),
        patch.object(ui, "unresolved_disputed", return_value=False),
    ):
        ui._show_generation_controls(
            validated_utp=object(),
            validated_program=None,
            template_selection=object(),
            academic_year="2026–2027",
            group_number="",
            class_name="",
            teacher_name="",
        )
    execute.assert_not_called()
    assert state["calendar_remote_job"]["job_id"] == "abc"


def test_fragment_polls_queued_running_succeeded() -> None:
    state = _state_with_job()
    statuses = [
        _job_status(job_state="QUEUED"),
        _job_status(job_state="RUNNING", phase="SEMANTIC"),
        _job_status(
            job_state="SUCCEEDED",
            phase="DOCX",
            pipeline_status=CalendarDocumentStatus.FINAL_READY.value,
            docx_available=True,
            filename="Plan.docx",
            warnings=["ok"],
        ),
    ]

    def fetch(_job_id, **_kwargs):
        return statuses.pop(0)

    widgets = _fragment(state, fetch_remote_calendar_job=fetch)
    assert state["calendar_remote_job"]["job_state"] == "QUEUED"
    widgets.download_button.assert_not_called()

    _fragment(state, fetch_remote_calendar_job=fetch)
    assert state["calendar_remote_job"]["job_state"] == "RUNNING"
    assert state["calendar_remote_job"]["phase"] == "SEMANTIC"

    widgets = _fragment(state, fetch_remote_calendar_job=fetch)
    assert "calendar_remote_job" not in state
    assert state["calendar_generation_succeeded"] is True
    assert state["calendar_document_status"] == CalendarDocumentStatus.FINAL_READY.value
    assert state["calendar_download"].content == b"PK\x03\x04docx"
    widgets.download_button.assert_called_once()
    assert widgets.download_button.call_args.args[0] == (
        "Скачать календарный план за 2026–2027 учебный год"
    )


def test_fragment_draft_ready_shows_plain_download_and_review_notes() -> None:
    state = _state_with_job()
    review_cases = [
        {
            "review_id": f"r{week}",
            "source_fingerprint": "s",
            "week_number": week,
            "topic_title": "Тема",
            "program_source": "SOURCE",
            "required_clauses": ["A"],
            "proposed_result": "Результат.",
            "proposed_control": "Контроль.",
            "reasons": ["Причина"],
            "status": "REVIEW_REQUIRED",
        }
        for week in range(1, 19)
    ]
    widgets = _fragment(
        state,
        fetch_remote_calendar_job=_job_status(
            job_state="SUCCEEDED",
            phase="DOCX",
            pipeline_status=CalendarDocumentStatus.DRAFT_READY.value,
            docx_available=True,
            filename="Draft.docx",
            review_cases=review_cases,
        ),
        download_remote_calendar_document=("Draft.docx", b"PK\x03\x04draft"),
    )
    assert state["calendar_document_status"] == CalendarDocumentStatus.DRAFT_READY.value
    assert state["calendar_download"].content == b"PK\x03\x04draft"
    widgets.warning.assert_not_called()
    assert widgets.download_button.call_args.args[0] == (
        "Скачать черновик календарного плана за 2026–2027 учебный год"
    )
    assert any(
        "Календарный план не готов к выдаче. Есть замечания: 18 недель" in str(call.args[0])
        for call in widgets.markdown.call_args_list
    )
    widgets.button.assert_called()
    assert widgets.button.call_args.args[0] == "Проверить замечания"


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (
            _job_status(
                job_state="FAILED",
                phase="DOCX",
                error={"code": "PIPE", "message": "Генерация документа не удалась."},
            ),
            "Генерация документа не удалась.",
        ),
        (
            _job_status(
                job_state="EXPIRED",
                error={"code": "JOB_EXPIRED", "message": REMOTE_JOB_EXPIRED_MESSAGE},
            ),
            REMOTE_JOB_EXPIRED_MESSAGE,
        ),
        (
            _job_status(
                job_state="FAILED",
                error={"code": "JOB_LOST", "message": REMOTE_JOB_LOST_MESSAGE},
            ),
            REMOTE_JOB_LOST_MESSAGE,
        ),
    ],
)
def test_fragment_shows_failed_expired_job_lost(status, message) -> None:
    state = _state_with_job()
    widgets = _fragment(state, fetch_remote_calendar_job=status)
    assert "calendar_remote_job" not in state
    assert state["calendar_generation_error"] == message
    assert "calendar_download" not in state
    widgets.error.assert_called()
    assert message in widgets.error.call_args.args[0]


def test_fragment_timeout_uses_persisted_started_at() -> None:
    state = _state_with_job(
        _handle(started_at=time.time() - DEFAULT_WAIT_TIMEOUT_SECONDS - 1)
    )
    widgets = _fragment(
        state,
        fetch_remote_calendar_job=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("timeout must not GET")
        ),
    )
    assert "calendar_remote_job" not in state
    assert state["calendar_generation_error"] == REMOTE_JOB_TIMEOUT_MESSAGE
    assert REMOTE_JOB_TIMEOUT_MESSAGE in widgets.error.call_args.args[0]


def test_stale_fingerprint_does_not_apply_remote_result() -> None:
    succeeded = _job_status(
        job_state="SUCCEEDED",
        phase="DOCX",
        pipeline_status=CalendarDocumentStatus.FINAL_READY.value,
        docx_available=True,
    )
    state = _state_with_job(_handle(fingerprint=("old-program", "revision")))
    widgets = _fragment(state, fetch_remote_calendar_job=succeeded)
    assert "calendar_remote_job" not in state
    assert "calendar_download" not in state
    assert "calendar_generation_succeeded" not in state
    widgets.download_button.assert_not_called()
    widgets.error.assert_not_called()


def test_busy_active_job_still_polls() -> None:
    fetches: list[str] = []

    def fetch(job_id, **_kwargs):
        fetches.append(job_id)
        return _job_status(job_state="RUNNING", phase="DOCX")

    state = _state_with_job()
    state["calendar_busy"] = True
    started_at = state["calendar_remote_job"]["started_at"]
    _fragment(state, fetch_remote_calendar_job=fetch)
    assert fetches == ["abc"]
    assert state["calendar_remote_job"]["job_id"] == "abc"
    assert state["calendar_remote_job"]["started_at"] == started_at
    assert state["calendar_busy"] is True
    assert state["calendar_work_status"] == "Формируем календарный план…"


def test_started_at_unchanged_across_fragment_reruns() -> None:
    started_at = time.time() - 5
    state = _state_with_job(_handle(started_at=started_at))
    state["calendar_remote_started_at"] = started_at
    for _ in range(3):
        _fragment(
            state,
            fetch_remote_calendar_job=_job_status(
                job_state="RUNNING",
                phase="SEMANTIC",
            ),
        )
    assert state["calendar_remote_job"]["job_id"] == "abc"
    assert state["calendar_remote_job"]["started_at"] == started_at
    assert state["calendar_remote_started_at"] == started_at


def test_timeout_reached_while_busy_after_pending_polls() -> None:
    started_at = time.time() - 10
    state = _state_with_job(_handle(started_at=started_at))
    state["calendar_busy"] = True
    state["calendar_remote_started_at"] = started_at
    fetches: list[str] = []

    def fetch(job_id, **_kwargs):
        fetches.append(job_id)
        return _job_status(job_state="RUNNING", phase="SEMANTIC")

    widgets = _fragment(state, fetch_remote_calendar_job=fetch)
    assert fetches == ["abc"]
    assert state["calendar_remote_job"]["started_at"] == started_at

    state["calendar_remote_started_at"] = time.time() - DEFAULT_WAIT_TIMEOUT_SECONDS - 1
    state["calendar_remote_job"]["started_at"] = state["calendar_remote_started_at"]
    widgets = _fragment(
        state,
        fetch_remote_calendar_job=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("timeout must not GET")
        ),
    )
    assert len(fetches) == 1
    assert "calendar_remote_job" not in state
    assert "calendar_remote_started_at" not in state
    assert state["calendar_generation_error"] == REMOTE_JOB_TIMEOUT_MESSAGE
    assert REMOTE_JOB_TIMEOUT_MESSAGE in widgets.error.call_args.args[0]
    assert state["calendar_busy"] is False


def test_clear_work_busy_keeps_in_flight_remote_job() -> None:
    state = _state_with_job()
    with patch.object(ui.st, "session_state", state):
        ui._clear_work_busy()
    assert state["calendar_remote_job"]["job_id"] == "abc"
    assert state["calendar_busy"] is True


def test_in_flight_job_skips_fingerprint_rehash() -> None:
    state = _state_with_job()
    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui, "_generator_revision") as revision,
        patch.object(
            ui,
            "fetch_remote_calendar_job",
            return_value=_job_status(job_state="QUEUED"),
        ),
        patch.object(ui.st, "markdown"),
        patch.object(ui.st, "error"),
        patch.object(ui.st, "info"),
        patch.object(ui.st, "warning"),
        patch.object(ui.st, "download_button"),
    ):
        ui._render_generation_result()
    revision.assert_not_called()
    assert state["calendar_remote_job"]["job_id"] == "abc"


def _run_execute(state, *, pipeline):
    validated_utp = SimpleNamespace(filename="plan.docx", parsed=_Plan())
    validated_program = SimpleNamespace(
        filename="program.docx", content=b"original-doc", parsed=_Program()
    )
    template = object()
    reviews = {("1", "Тема", None): {"decision": "USER_CONFIRMED"}}
    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui, "_generator_revision", return_value="revision"),
        patch.object(ui, "run_calendar_pipeline", side_effect=pipeline) as pipeline_mock,
        patch.object(ui, "submit_remote_calendar_job") as submit,
        patch.object(ui, "parse_program", side_effect=AssertionError("reparse")),
        patch.object(ui, "_work_status_block") as status_block,
        patch.object(ui, "ConfirmedStudyPlan", _Plan),
        patch.object(ui, "UtpParseResult", _Plan),
        patch.object(ui, "ProgramData", _Program),
    ):
        widget = SimpleNamespace(update=lambda **_kwargs: None)
        status_block.return_value.__enter__.return_value = widget
        status_block.return_value.__exit__.return_value = None
        ui._execute_calendar_generation(
            validated_utp=validated_utp,
            validated_program=validated_program,
            template_selection=template,
            academic_year="2026–2027",
            group_number="1",
            class_name="А",
            teacher_name="Иванова",
            reviews=reviews,
        )
    return pipeline_mock, submit, validated_utp, validated_program, template, reviews


def test_render_without_credentials_runs_in_process_without_http(monkeypatch) -> None:
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("CALENDAR_GENERATION_API_URL", raising=False)
    monkeypatch.delenv("CALENDAR_GENERATION_API_TOKEN", raising=False)
    state = {"calendar_generation_fingerprint": ("inputs", "revision"), "calendar_work_status": ""}

    def pipeline(*_args, **_kwargs):
        return PipelineResult(
            filename="plan.docx",
            content=b"docx-bytes",
            warnings=(),
            resolved_lessons=(),
        )

    pipeline_mock, submit, plan, program, template, reviews = _run_execute(state, pipeline=pipeline)
    submit.assert_not_called()
    pipeline_mock.assert_called_once()
    assert pipeline_mock.call_args.args[0] is plan.parsed
    assert pipeline_mock.call_args.args[1] is program.parsed
    assert pipeline_mock.call_args.kwargs["template"] is template
    assert pipeline_mock.call_args.kwargs["match_reviews"] is reviews
    assert pipeline_mock.call_args.kwargs["match_reviews"][("1", "Тема", None)]["decision"] == "USER_CONFIRMED"
    assert pipeline_mock.call_args.kwargs["use_ai"] is False
    assert "program_content" not in pipeline_mock.call_args.kwargs
    assert state["calendar_download"].content == b"docx-bytes"
    assert state["calendar_generation_succeeded"] is True
    assert "calendar_remote_job" not in state


def test_explicit_credentials_keep_remote_submit(monkeypatch) -> None:
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("CALENDAR_GENERATION_API_URL", "https://generation.example")
    monkeypatch.setenv("CALENDAR_GENERATION_API_TOKEN", "token")
    state = {"calendar_generation_fingerprint": ("inputs", "revision"), "calendar_work_status": ""}

    def pipeline(*_args, **_kwargs):
        raise AssertionError("in-process pipeline must not run")

    with (
        patch.object(ui.st, "session_state", state),
        patch.object(ui, "_generator_revision", return_value="revision"),
        patch.object(ui, "run_calendar_pipeline", side_effect=pipeline),
        patch.object(
            ui,
            "submit_remote_calendar_job",
            return_value={"job_id": "abc", "job_state": "QUEUED", "phase": None},
        ) as submit,
        patch.object(ui, "_work_status_block") as status_block,
        patch.object(ui, "ConfirmedStudyPlan", _Plan),
        patch.object(ui, "UtpParseResult", _Plan),
        patch.object(ui, "ProgramData", _Program),
    ):
        widget = SimpleNamespace(update=lambda **_kwargs: None)
        status_block.return_value.__enter__.return_value = widget
        status_block.return_value.__exit__.return_value = None
        ui._execute_calendar_generation(
            validated_utp=SimpleNamespace(filename="plan.docx", parsed=_Plan()),
            validated_program=SimpleNamespace(
                filename="program.docx", content=b"original-doc", parsed=_Program()
            ),
            template_selection=object(),
            academic_year="2026–2027",
            group_number="",
            class_name="",
            teacher_name="",
            reviews={},
        )
    submit.assert_called_once()
    assert state["calendar_remote_job"]["job_id"] == "abc"
    assert "calendar_download" not in state


def test_partial_configuration_is_shown_and_does_not_generate(monkeypatch) -> None:
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("CALENDAR_GENERATION_API_URL", "https://generation.example")
    monkeypatch.delenv("CALENDAR_GENERATION_API_TOKEN", raising=False)
    state = {
        "calendar_generation_fingerprint": ("inputs", "revision"),
        "calendar_work_status": "",
        "calendar_download": SimpleNamespace(content=b"old"),
        "calendar_generation_succeeded": True,
    }

    def pipeline(*_args, **_kwargs):
        raise AssertionError("pipeline must not run")

    _pipeline_mock, submit, *_rest = _run_execute(state, pipeline=pipeline)
    submit.assert_not_called()
    assert "CALENDAR_GENERATION_API_URL" in state["calendar_generation_error"]
    assert "calendar_download" not in state
    assert "calendar_generation_succeeded" not in state


def test_in_process_pipeline_error_does_not_create_download(monkeypatch) -> None:
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("CALENDAR_GENERATION_API_URL", raising=False)
    monkeypatch.delenv("CALENDAR_GENERATION_API_TOKEN", raising=False)
    state = {"calendar_generation_fingerprint": ("inputs", "revision"), "calendar_work_status": ""}

    def pipeline(*_args, **_kwargs):
        raise PipelineError("сбой конвейера")

    pipeline_mock, submit, *_rest = _run_execute(state, pipeline=pipeline)
    pipeline_mock.assert_called_once()
    submit.assert_not_called()
    assert state["calendar_generation_error"] == "сбой конвейера"
    assert "calendar_download" not in state
    assert "calendar_generation_succeeded" not in state

