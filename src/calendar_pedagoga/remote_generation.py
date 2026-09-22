"""HTTP client: Streamlit → calendar generation API → async worker."""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

from calendar_pedagoga.confirmed_study_plan import (
    ConfirmedStudyPlan,
    confirmed_plan_from_external_utp,
)
from calendar_pedagoga.generation_contract import (
    PIPELINE_CONTRACT,
    ConfirmedStudyPlanDTO,
    GenerationContractError,
    ManualSemanticConfirmationDTO,
    SemanticReviewCaseDTO,
    current_generator_revision,
    encode_match_reviews,
    encode_program_overlay,
)
from calendar_pedagoga.organization_template import (
    CalendarTemplateSelection,
    CalendarTemplateSource,
)
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.pipeline import CalendarDocumentStatus, PipelineError, PipelineResult
from calendar_pedagoga.program_parsing import ProgramData
from calendar_pedagoga.semantic_review import ManualSemanticConfirmation


DEFAULT_GENERATION_API_URL = "http://127.0.0.1:8000"
GENERATION_API_URL_ENV = "CALENDAR_GENERATION_API_URL"
GENERATION_API_TOKEN_ENV = "CALENDAR_GENERATION_API_TOKEN"
DEFAULT_POLL_INTERVAL_SECONDS = 0.4
DEFAULT_WAIT_TIMEOUT_SECONDS = 12 * 60.0
_HTTP_TIMEOUT_SECONDS = 60.0
_PUBLIC_REMOTE_FLAGS = {"1", "true", "yes", "on"}
_PUBLIC_REMOTE_BLOCK = (
    "Удалённая генерация недоступна: задайте "
    "CALENDAR_GENERATION_API_URL и CALENDAR_GENERATION_API_TOKEN."
)

_PHASE_LABELS = {
    "VALIDATION": "Формируем календарный план…",
    "SCHEDULE": "Формируем календарный план…",
    "SEMANTIC": "Формируем календарный план…",
    "DOCX": "Формируем календарный план…",
    "LIBREOFFICE_QA": "Проверяем готовый документ…",
}
REMOTE_JOB_TIMEOUT_MESSAGE = "Превышено время ожидания генерации документа."
REMOTE_JOB_EXPIRED_MESSAGE = "Срок хранения результата job истёк."
REMOTE_JOB_LOST_MESSAGE = "Job не найден после перезапуска или очистки worker."
QUEUED_PROGRESS_LABEL = "Очередь генерации…"

HttpRequest = Callable[..., tuple[int, Mapping[str, str], bytes]]


def public_remote_mode() -> bool:
    if (os.environ.get("RENDER") or "").strip():
        return True
    return (os.environ.get("CALENDAR_GENERATION_REMOTE") or "").strip().casefold() in (
        _PUBLIC_REMOTE_FLAGS
    )


def generation_api_token() -> str:
    return (os.environ.get(GENERATION_API_TOKEN_ENV) or "").strip()


def _is_loopback_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold()
    return host in {"127.0.0.1", "localhost", "::1"}


def generation_api_url(explicit: str | None = None) -> str:
    raw = explicit if explicit is not None else os.environ.get(GENERATION_API_URL_ENV)
    value = (raw or "").strip().rstrip("/")
    if public_remote_mode():
        if not value or _is_loopback_url(value):
            raise PipelineError(_PUBLIC_REMOTE_BLOCK)
        return value
    return value or DEFAULT_GENERATION_API_URL


def _with_bearer_token(request: HttpRequest, token: str) -> HttpRequest:
    def wrapped(
        method: str,
        url: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = _HTTP_TIMEOUT_SECONDS,
    ) -> tuple[int, Mapping[str, str], bytes]:
        merged = dict(headers or {})
        if token:
            merged["Authorization"] = f"Bearer {token}"
        return request(
            method,
            url,
            json_body=json_body,
            headers=merged,
            timeout=timeout,
        )

    return wrapped


def _json_dumps(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def default_http_request(
    method: str,
    url: str,
    *,
    json_body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = _HTTP_TIMEOUT_SECONDS,
) -> tuple[int, Mapping[str, str], bytes]:
    request_headers = {"Accept": "*/*"}
    if headers:
        request_headers.update(dict(headers))
    data = None
    if json_body is not None:
        data = _json_dumps(json_body)
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), dict(response.headers.items()), response.read()
    except HTTPError as error:
        return int(error.code), dict(error.headers.items()), error.read()
    except URLError as error:
        reason = getattr(error, "reason", error)
        raise PipelineError(f"Generation API недоступен: {reason}") from error


def _decode_json(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _api_error_message(status: int, body: bytes, *, fallback: str) -> str:
    payload = _decode_json(body)
    if isinstance(payload, Mapping):
        detail = payload.get("detail")
        if isinstance(detail, Mapping):
            message = str(detail.get("message") or "").strip()
            if message:
                return message
            code = str(detail.get("code") or "").strip()
            if code:
                return code
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    text = body.decode("utf-8", errors="replace").strip()
    if text:
        return text
    return f"{fallback} (HTTP {status})"


def _wire_file(filename: str, content: bytes) -> dict[str, str]:
    return {
        "filename": filename,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def build_generation_payload(
    plan: ConfirmedStudyPlan | UtpParseResult,
    *,
    academic_year: str,
    source_plan_name: str,
    program_filename: str,
    program_content: bytes,
    template: CalendarTemplateSelection | None = None,
    group_number: str | None = None,
    class_name: str | None = None,
    teacher_name: str | None = None,
    match_reviews: Mapping | None = None,
    manual_confirmations: Mapping[str, ManualSemanticConfirmation] | None = None,
    generator_revision: str | None = None,
    program: ProgramData | None = None,
) -> dict[str, Any]:
    if not program_filename.strip() or not program_content:
        raise PipelineError("Для удалённой генерации нужна образовательная программа.")
    try:
        reviews = encode_match_reviews(match_reviews)
        overlay = (
            encode_program_overlay(program.content_items)
            if program is not None and program.content_items
            else None
        )
    except GenerationContractError as error:
        raise PipelineError(str(error)) from error
    payload: dict[str, Any] = {
        "pipeline_contract": PIPELINE_CONTRACT,
        "generator_revision": generator_revision or current_generator_revision(),
        "plan": ConfirmedStudyPlanDTO.from_model(
            _confirmed_plan(plan, source_plan_name=source_plan_name)
        ).to_dict(),
        "program": _wire_file(program_filename, program_content),
        "academic_year": academic_year,
        "source_plan_name": source_plan_name,
        "confirmations": _wire_confirmations(manual_confirmations),
        "match_reviews": reviews,
    }
    if overlay is not None:
        payload["program_overlay"] = overlay
    if group_number:
        payload["group_number"] = group_number
    if class_name:
        payload["class_name"] = class_name
    if teacher_name:
        payload["teacher_name"] = teacher_name
    if (
        template is not None
        and template.source is CalendarTemplateSource.ORGANIZATION
        and template.filename
        and template.content
    ):
        payload["template"] = _wire_file(template.filename, template.content)
    return payload


def _wire_confirmations(
    confirmations: Mapping[str, ManualSemanticConfirmation] | None,
) -> dict[str, Any]:
    if not confirmations:
        return {}
    encoded: dict[str, Any] = {}
    for review_id, value in confirmations.items():
        if isinstance(value, ManualSemanticConfirmation):
            encoded[str(review_id)] = ManualSemanticConfirmationDTO.from_model(
                value
            ).to_dict()
        elif isinstance(value, Mapping):
            encoded[str(review_id)] = dict(value)
        else:
            raise PipelineError("Поле confirmations передано неверно.")
    return encoded


def _confirmed_plan(
    plan: ConfirmedStudyPlan | UtpParseResult,
    *,
    source_plan_name: str,
) -> ConfirmedStudyPlan:
    if isinstance(plan, ConfirmedStudyPlan):
        return plan
    return confirmed_plan_from_external_utp(plan, source_name=source_plan_name)


def _confirmation_errors(
    raw: object,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(raw, list):
        return ()
    errors: list[tuple[str, tuple[str, ...]]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        review_id = str(item.get("review_id") or "")
        issues = item.get("issues") or ()
        if not review_id:
            continue
        errors.append((review_id, tuple(str(issue) for issue in issues)))
    return tuple(errors)


def _review_cases(raw: object):
    if not isinstance(raw, list):
        return ()
    return tuple(SemanticReviewCaseDTO.from_dict(item).to_model() for item in raw)


def _authorized_request(
    http_request: HttpRequest | None,
    api_url: str | None,
) -> tuple[str, HttpRequest]:
    token = generation_api_token()
    if public_remote_mode() and not token:
        raise PipelineError(_PUBLIC_REMOTE_BLOCK)
    return generation_api_url(api_url), _with_bearer_token(
        http_request or default_http_request, token
    )


def _read_job_payload(status: int, body: bytes, *, fallback: str) -> dict[str, Any]:
    if status != 200:
        raise PipelineError(_api_error_message(status, body, fallback=fallback))
    payload = _decode_json(body)
    if not isinstance(payload, Mapping):
        raise PipelineError("Generation API вернул неверный статус job.")
    return dict(payload)


def remote_job_progress_label(job_state: object, phase: object) -> str:
    if job_state == "QUEUED":
        return QUEUED_PROGRESS_LABEL
    if isinstance(phase, str) and phase:
        return _PHASE_LABELS.get(phase, "Формируем календарный план…")
    return "Формируем календарный план…"


def remote_job_error_message(job: Mapping[str, Any]) -> str:
    error = job.get("error") if isinstance(job.get("error"), Mapping) else {}
    code = str(error.get("code") or "").strip()
    message = str(error.get("message") or "").strip()
    if code == "JOB_LOST":
        return message or REMOTE_JOB_LOST_MESSAGE
    if job.get("job_state") == "EXPIRED":
        return message or REMOTE_JOB_EXPIRED_MESSAGE
    return message or "Генерация документа не удалась."


def _poll_job(
    *,
    base_url: str,
    job_id: str,
    http_request: HttpRequest,
    on_progress: Callable[[str], None] | None,
    poll_interval: float,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_phase: str | None = None
    while time.monotonic() < deadline:
        status, _headers, body = http_request(
            "GET",
            urljoin(base_url + "/", f"v1/calendar-jobs/{job_id}"),
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        payload = _read_job_payload(
            status, body, fallback="Не удалось получить статус job"
        )
        phase = payload.get("phase")
        if isinstance(phase, str) and phase and phase != last_phase:
            if on_progress is not None:
                on_progress(remote_job_progress_label(payload.get("job_state"), phase))
            last_phase = phase
        state = payload.get("job_state")
        if state in {"SUCCEEDED", "FAILED", "EXPIRED"}:
            return payload
        time.sleep(poll_interval)
    raise PipelineError(REMOTE_JOB_TIMEOUT_MESSAGE)


def _download_document(
    *,
    base_url: str,
    job_id: str,
    http_request: HttpRequest,
    fallback_name: str | None,
) -> tuple[str, bytes]:
    status, headers, body = http_request(
        "GET",
        urljoin(base_url + "/", f"v1/calendar-jobs/{job_id}/document"),
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    if status != 200:
        raise PipelineError(
            _api_error_message(status, body, fallback="DOCX недоступен")
        )
    filename = fallback_name or "Календарный_план.docx"
    disposition = str(headers.get("Content-Disposition") or headers.get("content-disposition") or "")
    marker = "filename*=UTF-8''"
    if marker in disposition:
        filename = unquote(disposition.split(marker, 1)[1].split(";", 1)[0].strip()) or filename
    return filename, body


def submit_remote_calendar_job(
    plan: ConfirmedStudyPlan | UtpParseResult,
    program: ProgramData | None = None,
    *,
    academic_year: str,
    template: CalendarTemplateSelection,
    source_utp_name: str,
    use_ai: bool = False,
    ai_provider: object | None = None,
    program_filename: str | None = None,
    program_content: bytes | None = None,
    group_number: str | None = None,
    class_name: str | None = None,
    teacher_name: str | None = None,
    match_reviews: Mapping | None = None,
    manual_confirmations: Mapping[str, ManualSemanticConfirmation] | None = None,
    semantic_revision: str | None = None,
    on_progress: Callable[[str], None] | None = None,
    api_url: str | None = None,
    http_request: HttpRequest | None = None,
) -> dict[str, Any]:
    """POST /v1/calendar-jobs and return the created job. Does not poll."""

    del ai_provider
    if use_ai:
        raise PipelineError("Удалённая генерация работает только в режиме CE2 без AI.")
    if on_progress is not None:
        on_progress("Формируем календарный план…")
    payload = build_generation_payload(
        plan,
        academic_year=academic_year,
        source_plan_name=source_utp_name,
        program_filename=str(program_filename or ""),
        program_content=program_content or b"",
        template=template,
        group_number=group_number,
        class_name=class_name,
        teacher_name=teacher_name,
        match_reviews=match_reviews,
        manual_confirmations=manual_confirmations,
        generator_revision=semantic_revision,
        program=program,
    )
    base_url, request = _authorized_request(http_request, api_url)
    status, _headers, body = request(
        "POST",
        urljoin(base_url + "/", "v1/calendar-jobs"),
        json_body=payload,
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    if status != 202:
        raise PipelineError(
            _api_error_message(status, body, fallback="Не удалось поставить job в очередь")
        )
    created = _decode_json(body)
    if not isinstance(created, Mapping) or not created.get("job_id"):
        raise PipelineError("Generation API не вернул job_id.")
    if on_progress is not None:
        on_progress(remote_job_progress_label(created.get("job_state"), created.get("phase")))
    return dict(created)


def fetch_remote_calendar_job(
    job_id: str,
    *,
    api_url: str | None = None,
    http_request: HttpRequest | None = None,
) -> dict[str, Any]:
    base_url, request = _authorized_request(http_request, api_url)
    status, _headers, body = request(
        "GET",
        urljoin(base_url + "/", f"v1/calendar-jobs/{job_id}"),
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    return _read_job_payload(status, body, fallback="Не удалось получить статус job")


def download_remote_calendar_document(
    job_id: str,
    *,
    fallback_name: str | None = None,
    api_url: str | None = None,
    http_request: HttpRequest | None = None,
) -> tuple[str, bytes]:
    base_url, request = _authorized_request(http_request, api_url)
    return _download_document(
        base_url=base_url,
        job_id=job_id,
        http_request=request,
        fallback_name=fallback_name,
    )


def delete_remote_calendar_job(
    job_id: str,
    *,
    api_url: str | None = None,
    http_request: HttpRequest | None = None,
) -> None:
    base_url, request = _authorized_request(http_request, api_url)
    try:
        request(
            "DELETE",
            urljoin(base_url + "/", f"v1/calendar-jobs/{job_id}"),
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except PipelineError:
        pass


def _pipeline_result_from_job(
    job: Mapping[str, Any],
    *,
    filename: str,
    document: bytes,
) -> PipelineResult:
    pipeline_status_value = job.get("pipeline_status")
    if pipeline_status_value == CalendarDocumentStatus.HARD_BLOCK.value:
        raise PipelineError(remote_job_error_message(job) or "Формирование календарного плана заблокировано.")
    if pipeline_status_value not in {
        CalendarDocumentStatus.FINAL_READY.value,
        CalendarDocumentStatus.DRAFT_READY.value,
    }:
        raise PipelineError("Generation API вернул неизвестный статус документа.")
    return PipelineResult(
        filename=filename,
        content=document,
        warnings=tuple(job.get("warnings") or ()),
        resolved_lessons=(),
        status=CalendarDocumentStatus(pipeline_status_value),
        review_cases=_review_cases(job.get("review_cases")),
        confirmation_errors=_confirmation_errors(job.get("confirmation_errors")),
    )


@dataclass(frozen=True)
class RemoteJobAdvance:
    action: str
    job_state: str | None = None
    phase: str | None = None
    label: str = ""
    error: str | None = None
    result: PipelineResult | None = None


def advance_remote_generation_job(
    handle: Mapping[str, Any],
    *,
    current_fingerprint: object,
    now: float,
    fetch_job: Callable[[str], Mapping[str, Any]],
    download_document: Callable[..., tuple[str, bytes]],
    delete_job: Callable[[str], None],
    timeout: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
) -> RemoteJobAdvance:
    """One non-blocking poll step. Never submits a new job."""

    if handle.get("fingerprint") != current_fingerprint:
        return RemoteJobAdvance(action="stale")
    started_at = float(handle.get("started_at") or 0.0)
    if now - started_at >= timeout:
        return RemoteJobAdvance(action="timeout", error=REMOTE_JOB_TIMEOUT_MESSAGE)
    job_id = str(handle.get("job_id") or "").strip()
    if not job_id:
        return RemoteJobAdvance(action="failed", error="Generation API не вернул job_id.")
    job = dict(fetch_job(job_id))
    state = job.get("job_state")
    phase = job.get("phase") if isinstance(job.get("phase"), str) else None
    error = job.get("error") if isinstance(job.get("error"), Mapping) else {}
    if state == "EXPIRED" or (isinstance(error, Mapping) and error.get("code") == "JOB_LOST"):
        return RemoteJobAdvance(
            action="failed",
            job_state=str(state) if isinstance(state, str) else None,
            phase=phase,
            error=remote_job_error_message(job),
        )
    if state == "FAILED":
        return RemoteJobAdvance(
            action="failed",
            job_state="FAILED",
            phase=phase,
            error=remote_job_error_message(job),
        )
    if state != "SUCCEEDED":
        return RemoteJobAdvance(
            action="pending",
            job_state=str(state or "QUEUED"),
            phase=phase,
            label=remote_job_progress_label(state, phase),
        )
    pipeline_status_value = job.get("pipeline_status")
    if pipeline_status_value == CalendarDocumentStatus.HARD_BLOCK.value:
        return RemoteJobAdvance(
            action="failed",
            job_state="SUCCEEDED",
            phase=phase,
            error=str(
                (error or {}).get("message")
                or "Формирование календарного плана заблокировано."
            ),
        )
    if pipeline_status_value not in {
        CalendarDocumentStatus.FINAL_READY.value,
        CalendarDocumentStatus.DRAFT_READY.value,
    }:
        return RemoteJobAdvance(
            action="failed",
            job_state="SUCCEEDED",
            phase=phase,
            error="Generation API вернул неизвестный статус документа.",
        )
    if not job.get("docx_available"):
        return RemoteJobAdvance(
            action="failed",
            job_state="SUCCEEDED",
            phase=phase,
            error="DOCX недоступен.",
        )
    filename, document = download_document(job_id, fallback_name=job.get("filename"))
    try:
        delete_job(job_id)
    except PipelineError:
        pass
    result = _pipeline_result_from_job(job, filename=filename, document=document)
    ready_label = (
        "Черновой календарный план готов"
        if result.status is CalendarDocumentStatus.DRAFT_READY
        else "Календарный план готов"
    )
    return RemoteJobAdvance(
        action="succeeded",
        job_state="SUCCEEDED",
        phase=phase,
        label=ready_label,
        result=result,
    )


def run_remote_calendar_generation(
    plan: ConfirmedStudyPlan | UtpParseResult,
    program: ProgramData | None = None,
    *,
    academic_year: str,
    template: CalendarTemplateSelection,
    source_utp_name: str,
    use_ai: bool = False,
    ai_provider: object | None = None,
    program_filename: str | None = None,
    program_content: bytes | None = None,
    group_number: str | None = None,
    class_name: str | None = None,
    teacher_name: str | None = None,
    match_reviews: Mapping | None = None,
    manual_confirmations: Mapping[str, ManualSemanticConfirmation] | None = None,
    semantic_revision: str | None = None,
    on_progress: Callable[[str], None] | None = None,
    api_url: str | None = None,
    http_request: HttpRequest | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
) -> PipelineResult:
    """Submit a generation job and wait for the remote worker DOCX."""

    created = submit_remote_calendar_job(
        plan,
        program,
        academic_year=academic_year,
        template=template,
        source_utp_name=source_utp_name,
        use_ai=use_ai,
        ai_provider=ai_provider,
        program_filename=program_filename,
        program_content=program_content,
        group_number=group_number,
        class_name=class_name,
        teacher_name=teacher_name,
        match_reviews=match_reviews,
        manual_confirmations=manual_confirmations,
        semantic_revision=semantic_revision,
        on_progress=on_progress,
        api_url=api_url,
        http_request=http_request,
    )
    job_id = str(created["job_id"])
    job = created
    if job.get("job_state") not in {"SUCCEEDED", "FAILED", "EXPIRED"}:
        base_url, request = _authorized_request(http_request, api_url)
        job = _poll_job(
            base_url=base_url,
            job_id=job_id,
            http_request=request,
            on_progress=on_progress,
            poll_interval=poll_interval,
            timeout=timeout,
        )
    state = job.get("job_state")
    error = job.get("error") if isinstance(job.get("error"), Mapping) else {}
    if state == "FAILED" or (isinstance(error, Mapping) and error.get("code") == "JOB_LOST"):
        raise PipelineError(remote_job_error_message(job))
    if state == "EXPIRED":
        raise PipelineError(REMOTE_JOB_EXPIRED_MESSAGE)
    if not job.get("docx_available"):
        pipeline_status_value = job.get("pipeline_status")
        if pipeline_status_value == CalendarDocumentStatus.HARD_BLOCK.value:
            raise PipelineError(
                str(error.get("message") or "Формирование календарного плана заблокировано.")
            )
        raise PipelineError("DOCX недоступен.")
    filename, document = download_remote_calendar_document(
        job_id,
        fallback_name=job.get("filename"),
        api_url=api_url,
        http_request=http_request,
    )
    delete_remote_calendar_job(job_id, api_url=api_url, http_request=http_request)
    return _pipeline_result_from_job(job, filename=filename, document=document)


__all__ = [
    "DEFAULT_GENERATION_API_URL",
    "DEFAULT_WAIT_TIMEOUT_SECONDS",
    "GENERATION_API_TOKEN_ENV",
    "GENERATION_API_URL_ENV",
    "REMOTE_JOB_EXPIRED_MESSAGE",
    "REMOTE_JOB_LOST_MESSAGE",
    "REMOTE_JOB_TIMEOUT_MESSAGE",
    "RemoteJobAdvance",
    "advance_remote_generation_job",
    "build_generation_payload",
    "delete_remote_calendar_job",
    "download_remote_calendar_document",
    "fetch_remote_calendar_job",
    "generation_api_token",
    "generation_api_url",
    "public_remote_mode",
    "remote_job_progress_label",
    "run_remote_calendar_generation",
    "submit_remote_calendar_job",
]
