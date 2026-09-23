import base64
from urllib.parse import urlparse

from fastapi.testclient import TestClient
import pytest

from calendar_pedagoga.confirmed_study_plan import confirmed_plan_from_external_utp
from calendar_pedagoga.generation_api import GENERATION_API_TOKEN_ENV, create_app
from calendar_pedagoga.generation_contract import PIPELINE_CONTRACT
from calendar_pedagoga.generation_service import GenerationService
from calendar_pedagoga.organization_template import (
    CalendarTemplateSelection,
    CalendarTemplateSource,
)
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.pipeline import CalendarDocumentStatus, PipelineError, PipelineResult
from calendar_pedagoga.remote_generation import (
    DEFAULT_GENERATION_API_URL,
    GENERATION_API_TOKEN_ENV as REMOTE_TOKEN_ENV,
    GENERATION_API_URL_ENV,
    PARTIAL_GENERATION_CONFIGURATION_MESSAGE,
    REMOTE_JOB_EXPIRED_MESSAGE,
    REMOTE_JOB_LOST_MESSAGE,
    REMOTE_JOB_TIMEOUT_MESSAGE,
    advance_remote_generation_job,
    build_generation_payload,
    generation_api_url,
    run_remote_calendar_generation,
    select_generation_route,
    submit_remote_calendar_job,
)
from calendar_pedagoga.semantic_review import SemanticReviewCase
from pathlib import Path


REFERENCES = Path(__file__).resolve().parents[1] / "references"
REVISION = "test-revision"
TEST_API_TOKEN = "kp-test-generation-token"
UTP_PATH = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
PROGRAM_PATH = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"


@pytest.fixture(autouse=True)
def _generation_api_token(monkeypatch) -> None:
    monkeypatch.setenv(GENERATION_API_TOKEN_ENV, TEST_API_TOKEN)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("CALENDAR_GENERATION_REMOTE", raising=False)
    monkeypatch.delenv(GENERATION_API_URL_ENV, raising=False)


def _plan():
    return confirmed_plan_from_external_utp(
        parse_utp(UTP_PATH), study_year=2, source_name=UTP_PATH.name
    )


def _template() -> CalendarTemplateSelection:
    return CalendarTemplateSelection(CalendarTemplateSource.STANDARD)


def _result(status: CalendarDocumentStatus, *, review_cases=()) -> PipelineResult:
    return PipelineResult(
        filename="Черновик.docx" if review_cases else "План.docx",
        content=b"PK\x03\x04docx",
        warnings=("ok",),
        resolved_lessons=(),
        status=status,
        review_cases=review_cases,
    )


def _final_runner(*_args, **_kwargs) -> PipelineResult:
    return _result(CalendarDocumentStatus.FINAL_READY)


def _draft_runner(*_args, **_kwargs) -> PipelineResult:
    return _result(
        CalendarDocumentStatus.DRAFT_READY,
        review_cases=(
            SemanticReviewCase(
                review_id="r1",
                source_fingerprint="s1",
                week_number=2,
                topic_title="Тема",
                program_source="SOURCE",
                required_clauses=("A",),
                proposed_result="Результат.",
                proposed_control="Контроль.",
                reasons=("Причина",),
            ),
        ),
    )


def _blocked_runner(*_args, **_kwargs):
    raise PipelineError("Повреждённый план")


def _service(runner=_final_runner, **kwargs) -> GenerationService:
    return GenerationService(
        runner=runner,
        revision_provider=lambda: REVISION,
        job_timeout_seconds=kwargs.pop("job_timeout_seconds", 20),
        ttl_seconds=kwargs.pop("ttl_seconds", 60),
        **kwargs,
    )


def _http_via_client(client: TestClient):
    def http_request(
        method: str,
        url: str,
        *,
        json_body=None,
        headers=None,
        timeout=60,
    ):
        parsed = urlparse(url)
        response = client.request(
            method,
            parsed.path,
            json=json_body,
            headers=dict(headers or {}),
        )
        return response.status_code, response.headers, response.content

    return http_request


def _run(client: TestClient, **kwargs) -> tuple[PipelineResult, list[str]]:
    phases: list[str] = []
    result = run_remote_calendar_generation(
        _plan(),
        academic_year="2026–2027",
        template=_template(),
        source_utp_name=UTP_PATH.name,
        program_filename=PROGRAM_PATH.name,
        program_content=PROGRAM_PATH.read_bytes(),
        semantic_revision=REVISION,
        on_progress=phases.append,
        api_url="http://generation.test",
        http_request=_http_via_client(client),
        poll_interval=0.01,
        timeout=kwargs.pop("timeout", 10),
        **kwargs,
    )
    return result, phases


def test_generation_api_url_defaults_and_env(monkeypatch) -> None:
    monkeypatch.delenv(GENERATION_API_URL_ENV, raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("CALENDAR_GENERATION_REMOTE", raising=False)
    assert generation_api_url() == DEFAULT_GENERATION_API_URL
    monkeypatch.setenv(GENERATION_API_URL_ENV, "http://127.0.0.1:8000/")
    assert generation_api_url() == "http://127.0.0.1:8000"


def test_remote_client_sends_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv(REMOTE_TOKEN_ENV, TEST_API_TOKEN)
    seen: list[dict[str, str]] = []

    def http_request(method, url, *, json_body=None, headers=None, timeout=60):
        seen.append(dict(headers or {}))
        succeeded = (
            '{"job_id":"abc","job_state":"SUCCEEDED","phase":"DOCX",'
            '"pipeline_status":"FINAL_READY","review_cases":[],'
            '"confirmation_errors":[],"warnings":[],"docx_available":true,'
            '"filename":"Plan.docx","error":null}'
        ).encode("utf-8")
        if method == "POST":
            return 202, {}, succeeded
        if method == "GET" and url.endswith("/document"):
            return 200, {"Content-Disposition": "attachment; filename*=UTF-8''Plan.docx"}, b"PK\x03\x04docx"
        if method == "DELETE":
            return 204, {}, b""
        return 200, {}, succeeded

    result = run_remote_calendar_generation(
        _plan(),
        academic_year="2026–2027",
        template=_template(),
        source_utp_name=UTP_PATH.name,
        program_filename=PROGRAM_PATH.name,
        program_content=PROGRAM_PATH.read_bytes(),
        semantic_revision=REVISION,
        api_url="http://generation.test",
        http_request=http_request,
        poll_interval=0.01,
    )
    assert result.status is CalendarDocumentStatus.FINAL_READY
    assert seen
    assert all(item.get("Authorization") == f"Bearer {TEST_API_TOKEN}" for item in seen)


def test_public_remote_mode_without_url_or_token_is_fail_closed(monkeypatch) -> None:
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv(GENERATION_API_URL_ENV, raising=False)
    monkeypatch.delenv(REMOTE_TOKEN_ENV, raising=False)
    calls: list[tuple] = []

    def http_request(*_args, **_kwargs):
        calls.append((_args, _kwargs))
        return 500, {}, b"{}"

    with pytest.raises(PipelineError, match="CALENDAR_GENERATION_API_URL"):
        generation_api_url()
    with pytest.raises(PipelineError, match="CALENDAR_GENERATION_API_TOKEN"):
        run_remote_calendar_generation(
            _plan(),
            academic_year="2026–2027",
            template=_template(),
            source_utp_name=UTP_PATH.name,
            program_filename=PROGRAM_PATH.name,
            program_content=PROGRAM_PATH.read_bytes(),
            semantic_revision=REVISION,
            http_request=http_request,
        )
    assert calls == []


def test_render_loopback_is_rejected_even_with_token(monkeypatch) -> None:
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv(GENERATION_API_URL_ENV, "http://127.0.0.1:8000")
    monkeypatch.setenv(REMOTE_TOKEN_ENV, TEST_API_TOKEN)
    with pytest.raises(PipelineError, match="CALENDAR_GENERATION_API_URL"):
        generation_api_url()
    with pytest.raises(PipelineError, match="CALENDAR_GENERATION_API_URL"):
        select_generation_route()


def test_render_without_credentials_selects_in_process_pipeline(monkeypatch) -> None:
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv(GENERATION_API_URL_ENV, raising=False)
    monkeypatch.delenv(REMOTE_TOKEN_ENV, raising=False)
    assert select_generation_route() == "in_process"


def test_explicit_url_and_token_select_remote_path(monkeypatch) -> None:
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setenv(GENERATION_API_URL_ENV, "https://generation.example")
    monkeypatch.setenv(REMOTE_TOKEN_ENV, TEST_API_TOKEN)
    assert select_generation_route() == "remote"


@pytest.mark.parametrize(
    ("url", "token"),
    [
        ("https://generation.example", ""),
        ("", TEST_API_TOKEN),
    ],
)
def test_partial_generation_configuration_is_an_error(monkeypatch, url: str, token: str) -> None:
    monkeypatch.setenv("RENDER", "true")
    if url:
        monkeypatch.setenv(GENERATION_API_URL_ENV, url)
    else:
        monkeypatch.delenv(GENERATION_API_URL_ENV, raising=False)
    if token:
        monkeypatch.setenv(REMOTE_TOKEN_ENV, token)
    else:
        monkeypatch.delenv(REMOTE_TOKEN_ENV, raising=False)
    with pytest.raises(PipelineError, match=PARTIAL_GENERATION_CONFIGURATION_MESSAGE):
        select_generation_route()


def test_build_payload_uses_contract_and_json_safe_reviews() -> None:
    payload = build_generation_payload(
        _plan(),
        academic_year="2026–2027",
        source_plan_name=UTP_PATH.name,
        program_filename=PROGRAM_PATH.name,
        program_content=PROGRAM_PATH.read_bytes(),
        template=_template(),
        match_reviews={("1", "Тема", None): {"decision": "USER_CONFIRMED"}},
        generator_revision=REVISION,
    )
    assert payload["pipeline_contract"] == PIPELINE_CONTRACT
    assert payload["generator_revision"] == REVISION
    assert "template" not in payload
    assert "program_overlay" not in payload
    assert payload["match_reviews"] == [
        {
            "topic_number": "1",
            "topic_title": "Тема",
            "parent_section": None,
            "review": {"decision": "USER_CONFIRMED"},
        }
    ]
    encoded = payload["program"]["content_base64"]
    assert base64.b64decode(encoded) == PROGRAM_PATH.read_bytes()


def test_remote_client_polls_until_final_ready_and_downloads_docx() -> None:
    with TestClient(create_app(service=_service())) as client:
        result, phases = _run(client)
        assert result.status is CalendarDocumentStatus.FINAL_READY
        assert result.content.startswith(b"PK\x03\x04")
        assert result.filename
        assert "Формируем календарный план…" in phases


def test_remote_client_returns_draft_review_cases() -> None:
    with TestClient(create_app(service=_service(_draft_runner))) as client:
        result, _phases = _run(client)
        assert result.status is CalendarDocumentStatus.DRAFT_READY
        assert [case.week_number for case in result.review_cases] == [2]
        assert result.content.startswith(b"PK\x03\x04")


def test_remote_client_maps_hard_block_to_pipeline_error() -> None:
    with TestClient(create_app(service=_service(_blocked_runner))) as client:
        with pytest.raises(PipelineError, match="Повреждённый план"):
            _run(client)


def test_remote_client_maps_revision_mismatch() -> None:
    with TestClient(create_app(service=_service())) as client:
        with pytest.raises(PipelineError, match="не совпадает"):
            run_remote_calendar_generation(
                _plan(),
                academic_year="2026–2027",
                template=_template(),
                source_utp_name=UTP_PATH.name,
                program_filename=PROGRAM_PATH.name,
                program_content=PROGRAM_PATH.read_bytes(),
                semantic_revision="stale",
                api_url="http://generation.test",
                http_request=_http_via_client(client),
            )


def test_remote_client_reports_failed_job() -> None:
    calls = {"status": 0}

    def http_request(method, url, *, json_body=None, headers=None, timeout=60):
        if method == "POST":
            body = (
                b'{"job_id":"abc","job_state":"QUEUED","phase":null,'
                b'"pipeline_status":null,"review_cases":[],'
                b'"confirmation_errors":[],"warnings":[],"docx_available":false,'
                b'"filename":null,"error":null}'
            )
            return 202, {}, body
        calls["status"] += 1
        body = (
            b'{"job_id":"abc","job_state":"FAILED","phase":"DOCX",'
            b'"pipeline_status":null,"review_cases":[],'
            b'"confirmation_errors":[],"warnings":[],"docx_available":false,'
            b'"filename":null,"error":{"code":"JOB_TIMEOUT","message":"timeout"}}'
        )
        return 200, {}, body

    with pytest.raises(PipelineError, match="timeout"):
        run_remote_calendar_generation(
            _plan(),
            academic_year="2026–2027",
            template=_template(),
            source_utp_name=UTP_PATH.name,
            program_filename=PROGRAM_PATH.name,
            program_content=PROGRAM_PATH.read_bytes(),
            semantic_revision=REVISION,
            api_url="http://generation.test",
            http_request=http_request,
            poll_interval=0.01,
        )
    assert calls["status"] == 1


def test_remote_client_rejects_ai_mode() -> None:
    with pytest.raises(PipelineError, match="без AI"):
        run_remote_calendar_generation(
            _plan(),
            academic_year="2026–2027",
            template=_template(),
            source_utp_name=UTP_PATH.name,
            program_filename=PROGRAM_PATH.name,
            program_content=PROGRAM_PATH.read_bytes(),
            use_ai=True,
            semantic_revision=REVISION,
            http_request=lambda *_args, **_kwargs: (500, {}, b"{}"),
        )


def _queued_body(job_id: str = "abc") -> bytes:
    return (
        f'{{"job_id":"{job_id}","job_state":"QUEUED","phase":null,'
        '"pipeline_status":null,"review_cases":[],'
        '"confirmation_errors":[],"warnings":[],"docx_available":false,'
        '"filename":null,"error":null}'
    ).encode("utf-8")


def test_submit_returns_immediately_after_post() -> None:
    methods: list[str] = []

    def http_request(method, url, *, json_body=None, headers=None, timeout=60):
        methods.append(method)
        if method == "POST" and url.endswith("/v1/calendar-jobs"):
            return 202, {}, _queued_body()
        raise AssertionError(f"submit must not {method} {url}")

    created = submit_remote_calendar_job(
        _plan(),
        academic_year="2026–2027",
        template=_template(),
        source_utp_name=UTP_PATH.name,
        program_filename=PROGRAM_PATH.name,
        program_content=PROGRAM_PATH.read_bytes(),
        semantic_revision=REVISION,
        api_url="http://generation.test",
        http_request=http_request,
    )
    assert created["job_id"] == "abc"
    assert created["job_state"] == "QUEUED"
    assert methods == ["POST"]


def test_advance_polls_queued_running_succeeded_and_downloads_once() -> None:
    fetches: list[str] = []
    downloads: list[str] = []
    deletes: list[str] = []
    statuses = [
        {
            "job_id": "abc",
            "job_state": "QUEUED",
            "phase": None,
            "pipeline_status": None,
            "docx_available": False,
            "filename": None,
            "warnings": [],
            "review_cases": [],
            "confirmation_errors": [],
            "error": None,
        },
        {
            "job_id": "abc",
            "job_state": "RUNNING",
            "phase": "SEMANTIC",
            "pipeline_status": None,
            "docx_available": False,
            "filename": None,
            "warnings": [],
            "review_cases": [],
            "confirmation_errors": [],
            "error": None,
        },
        {
            "job_id": "abc",
            "job_state": "SUCCEEDED",
            "phase": "LIBREOFFICE_QA",
            "pipeline_status": CalendarDocumentStatus.FINAL_READY.value,
            "docx_available": True,
            "filename": "Plan.docx",
            "warnings": ["ok"],
            "review_cases": [],
            "confirmation_errors": [],
            "error": None,
        },
    ]

    def fetch_job(job_id: str):
        fetches.append(job_id)
        return statuses.pop(0)

    def download_document(job_id: str, fallback_name=None):
        downloads.append(job_id)
        return fallback_name or "Plan.docx", b"PK\x03\x04docx"

    def delete_job(job_id: str):
        deletes.append(job_id)

    handle = {"job_id": "abc", "started_at": 100.0, "fingerprint": "fp"}
    first = advance_remote_generation_job(
        handle, current_fingerprint="fp", now=101.0,
        fetch_job=fetch_job, download_document=download_document, delete_job=delete_job,
    )
    assert first.action == "pending"
    assert first.job_state == "QUEUED"
    second = advance_remote_generation_job(
        handle, current_fingerprint="fp", now=102.0,
        fetch_job=fetch_job, download_document=download_document, delete_job=delete_job,
    )
    assert second.action == "pending"
    assert second.job_state == "RUNNING"
    assert second.phase == "SEMANTIC"
    third = advance_remote_generation_job(
        handle, current_fingerprint="fp", now=103.0,
        fetch_job=fetch_job, download_document=download_document, delete_job=delete_job,
    )
    assert third.action == "succeeded"
    assert third.result is not None
    assert third.result.status is CalendarDocumentStatus.FINAL_READY
    assert third.result.content.startswith(b"PK\x03\x04")
    assert fetches == ["abc", "abc", "abc"]
    assert downloads == ["abc"]
    assert deletes == ["abc"]


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        (
            {
                "job_id": "abc",
                "job_state": "FAILED",
                "phase": "DOCX",
                "error": {"code": "JOB_TIMEOUT", "message": "worker timeout"},
            },
            "worker timeout",
        ),
        (
            {
                "job_id": "abc",
                "job_state": "EXPIRED",
                "phase": None,
                "error": {"code": "JOB_EXPIRED", "message": REMOTE_JOB_EXPIRED_MESSAGE},
            },
            REMOTE_JOB_EXPIRED_MESSAGE,
        ),
        (
            {
                "job_id": "abc",
                "job_state": "FAILED",
                "phase": None,
                "error": {"code": "JOB_LOST", "message": REMOTE_JOB_LOST_MESSAGE},
            },
            REMOTE_JOB_LOST_MESSAGE,
        ),
    ],
)
def test_advance_maps_terminal_failures(job, expected) -> None:
    outcome = advance_remote_generation_job(
        {"job_id": "abc", "started_at": 1.0, "fingerprint": "fp"},
        current_fingerprint="fp",
        now=2.0,
        fetch_job=lambda _job_id: job,
        download_document=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("download")),
        delete_job=lambda _job_id: (_ for _ in ()).throw(AssertionError("delete")),
    )
    assert outcome.action == "failed"
    assert outcome.error == expected


def test_advance_timeout_uses_started_at_without_fetch() -> None:
    outcome = advance_remote_generation_job(
        {"job_id": "abc", "started_at": 0.0, "fingerprint": "fp"},
        current_fingerprint="fp",
        now=12 * 60,
        fetch_job=lambda _job_id: (_ for _ in ()).throw(AssertionError("fetch")),
        download_document=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("download")),
        delete_job=lambda _job_id: None,
    )
    assert outcome.action == "timeout"
    assert outcome.error == REMOTE_JOB_TIMEOUT_MESSAGE


def test_advance_stale_fingerprint_does_not_apply_result() -> None:
    outcome = advance_remote_generation_job(
        {"job_id": "abc", "started_at": 1.0, "fingerprint": "old"},
        current_fingerprint="new",
        now=2.0,
        fetch_job=lambda _job_id: (_ for _ in ()).throw(AssertionError("fetch")),
        download_document=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("download")),
        delete_job=lambda _job_id: None,
    )
    assert outcome.action == "stale"
    assert outcome.result is None
