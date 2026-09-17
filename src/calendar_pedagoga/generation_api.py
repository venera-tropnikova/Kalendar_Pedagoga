"""Local asynchronous HTTP API around the existing calendar pipeline."""

from __future__ import annotations

import base64
import binascii
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response, status

from calendar_pedagoga.generation_contract import (
    PIPELINE_CONTRACT,
    ConfirmedStudyPlanDTO,
    GenerationContractError,
    ManualSemanticConfirmationDTO,
    SemanticReviewCaseDTO,
    current_generator_revision,
)
from calendar_pedagoga.generator_revision import generator_git_commit
from calendar_pedagoga.organization_template import select_calendar_template
from calendar_pedagoga.pipeline import (
    CalendarDocumentStatus,
    PipelineError,
    PipelineResult,
    run_calendar_pipeline,
)
from calendar_pedagoga.program_parsing import parse_program


MAX_DOCUMENT_BYTES = 25 * 1024 * 1024


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass
class GenerationJob:
    job_id: str
    state: JobState = JobState.QUEUED
    phase: str = "QUEUED"
    pipeline_status: CalendarDocumentStatus | None = None
    review_cases: tuple[dict[str, Any], ...] = ()
    confirmation_errors: tuple[tuple[str, tuple[str, ...]], ...] = ()
    warnings: tuple[str, ...] = ()
    filename: str | None = None
    document: bytes | None = field(default=None, repr=False)
    error: dict[str, Any] | None = None

    def public_status(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_state": self.state.value,
            "phase": self.phase,
            "pipeline_status": (
                self.pipeline_status.value if self.pipeline_status is not None else None
            ),
            "review_cases": list(self.review_cases),
            "confirmation_errors": [
                {"review_id": review_id, "issues": list(issues)}
                for review_id, issues in self.confirmation_errors
            ],
            "warnings": list(self.warnings),
            "docx_available": self.document is not None,
            "filename": self.filename,
            "error": self.error,
        }


PipelineRunner = Callable[..., PipelineResult]


class GenerationJobStore:
    """One-process job registry with one LibreOffice pipeline worker."""

    def __init__(
        self,
        *,
        runner: PipelineRunner = run_calendar_pipeline,
        revision_provider: Callable[[], str] = current_generator_revision,
    ) -> None:
        self._runner = runner
        self._revision_provider = revision_provider
        self._jobs: dict[str, GenerationJob] = {}
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="calendar-generation",
        )

    @property
    def revision(self) -> str:
        return self._revision_provider()

    def submit(self, payload: Mapping[str, Any]) -> GenerationJob:
        contract = payload.get("pipeline_contract")
        if contract != PIPELINE_CONTRACT:
            raise GenerationContractError(
                f"Нужен pipeline_contract={PIPELINE_CONTRACT}."
            )
        requested_revision = payload.get("generator_revision")
        if requested_revision != self.revision:
            raise RevisionMismatchError(self.revision, requested_revision)
        job = GenerationJob(job_id=uuid4().hex)
        with self._lock:
            self._jobs[job.job_id] = job
        self._executor.submit(self._execute, job.job_id, dict(payload))
        return job

    def get(self, job_id: str) -> GenerationJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.state in {JobState.QUEUED, JobState.RUNNING}:
                raise JobRunningError(job_id)
            job.document = None
            del self._jobs[job_id]
            return True

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _update(self, job_id: str, **changes: Any) -> GenerationJob:
        with self._lock:
            job = self._jobs[job_id]
            for name, value in changes.items():
                setattr(job, name, value)
            return job

    def _execute(self, job_id: str, payload: Mapping[str, Any]) -> None:
        self._update(job_id, state=JobState.RUNNING, phase="VALIDATION")
        try:
            inputs = _decode_generation_payload(payload)

            def progress(label: str) -> None:
                phase = (
                    "LIBREOFFICE_QA"
                    if "Проверяем готовый документ" in label
                    else "DOCX"
                )
                self._update(job_id, phase=phase)

            self._update(job_id, phase="PIPELINE")
            result = self._runner(
                inputs.plan,
                inputs.program,
                academic_year=inputs.academic_year,
                template=inputs.template,
                source_utp_name=inputs.source_plan_name,
                use_ai=False,
                program_filename=inputs.program_filename,
                group_number=inputs.group_number,
                class_name=inputs.class_name,
                teacher_name=inputs.teacher_name,
                match_reviews=inputs.match_reviews,
                manual_confirmations=inputs.confirmations,
                semantic_revision=str(payload["generator_revision"]),
                on_progress=progress,
            )
        except (GenerationContractError, PipelineError, ValueError) as error:
            self._update(
                job_id,
                state=JobState.SUCCEEDED,
                phase="DONE",
                pipeline_status=CalendarDocumentStatus.HARD_BLOCK,
                document=None,
                error={
                    "code": "HARD_BLOCK",
                    "stage": "PIPELINE",
                    "message": str(error),
                    "retryable": False,
                },
            )
            return
        except Exception as error:  # infrastructure failure, not a semantic block
            self._update(
                job_id,
                state=JobState.FAILED,
                phase="FAILED",
                document=None,
                error={
                    "code": "GENERATION_FAILED",
                    "stage": "PIPELINE",
                    "message": f"{type(error).__name__}: {error}",
                    "retryable": True,
                },
            )
            return

        self._update(
            job_id,
            state=JobState.SUCCEEDED,
            phase="DONE",
            pipeline_status=result.status,
            review_cases=tuple(
                SemanticReviewCaseDTO.from_model(case).to_dict()
                for case in result.review_cases
            ),
            confirmation_errors=result.confirmation_errors,
            warnings=result.warnings,
            filename=result.filename,
            document=result.content,
            error=None,
        )


class RevisionMismatchError(GenerationContractError):
    def __init__(self, expected: str, actual: object) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__("Версия генератора Streamlit не совпадает с Render API.")


class JobRunningError(RuntimeError):
    pass


@dataclass(frozen=True)
class _GenerationInputs:
    plan: Any
    program: Any
    template: Any
    academic_year: str
    source_plan_name: str
    program_filename: str
    group_number: str | None
    class_name: str | None
    teacher_name: str | None
    match_reviews: Mapping[str, Any]
    confirmations: Mapping[str, Any]


def _decode_document(value: object, *, field: str) -> tuple[str, bytes]:
    item = value if isinstance(value, Mapping) else None
    if item is None:
        raise GenerationContractError(f"Поле «{field}» должно быть объектом.")
    filename = str(item.get("filename") or "").strip()
    encoded = item.get("content_base64")
    if not filename or not isinstance(encoded, str):
        raise GenerationContractError(f"Файл «{field}» передан неверно.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise GenerationContractError(f"Файл «{field}» содержит неверный base64.") from error
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        raise GenerationContractError(
            f"Размер файла «{field}» должен быть от 1 до {MAX_DOCUMENT_BYTES} байт."
        )
    return Path(filename).name, content


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _decode_generation_payload(payload: Mapping[str, Any]) -> _GenerationInputs:
    plan = ConfirmedStudyPlanDTO.from_dict(payload.get("plan")).to_model()
    program_filename, program_content = _decode_document(
        payload.get("program"), field="program"
    )
    program = parse_program(
        program_content,
        program_filename,
        study_year=plan.study_year,
    )
    template_value = payload.get("template")
    if template_value is None:
        template = select_calendar_template()
    else:
        template_filename, template_content = _decode_document(
            template_value, field="template"
        )
        template = select_calendar_template(template_filename, template_content)

    confirmations_value = payload.get("confirmations", {})
    if not isinstance(confirmations_value, Mapping):
        raise GenerationContractError("Поле «confirmations» должно быть объектом.")
    confirmations = {
        str(review_id): ManualSemanticConfirmationDTO.from_dict(value).to_model()
        for review_id, value in confirmations_value.items()
    }
    if any(review_id != value.review_id for review_id, value in confirmations.items()):
        raise GenerationContractError(
            "Ключ confirmation должен совпадать с его review_id."
        )
    match_reviews = payload.get("match_reviews", {})
    if not isinstance(match_reviews, Mapping):
        raise GenerationContractError("Поле «match_reviews» должно быть объектом.")
    academic_year = str(payload.get("academic_year") or "").strip()
    source_plan_name = str(payload.get("source_plan_name") or "").strip()
    if not academic_year or not source_plan_name:
        raise GenerationContractError(
            "academic_year и source_plan_name обязательны."
        )
    return _GenerationInputs(
        plan=plan,
        program=program,
        template=template,
        academic_year=academic_year,
        source_plan_name=Path(source_plan_name).name,
        program_filename=program_filename,
        group_number=_optional_text(payload.get("group_number")),
        class_name=_optional_text(payload.get("class_name")),
        teacher_name=_optional_text(payload.get("teacher_name")),
        match_reviews=dict(match_reviews),
        confirmations=confirmations,
    )


def create_app(*, store: GenerationJobStore | None = None) -> FastAPI:
    jobs = store or GenerationJobStore()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            jobs.close()

    app = FastAPI(
        title="Calendar Pedagoga Generation API",
        version="1",
        lifespan=lifespan,
    )
    app.state.generation_jobs = jobs

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "pipeline_contract": PIPELINE_CONTRACT,
            "generator_revision": jobs.revision,
            "generator_git_commit": generator_git_commit(),
        }

    @app.post("/v1/calendar-jobs", status_code=status.HTTP_202_ACCEPTED)
    def create_job(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            job = jobs.submit(payload)
        except RevisionMismatchError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "REVISION_MISMATCH",
                    "message": str(error),
                    "expected_revision": error.expected,
                    "actual_revision": error.actual,
                },
            ) from error
        except GenerationContractError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "INVALID_CONTRACT", "message": str(error)},
            ) from error
        return job.public_status()

    @app.get("/v1/calendar-jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job.public_status()

    @app.get("/v1/calendar-jobs/{job_id}/document")
    def get_document(job_id: str) -> Response:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.document is None or job.filename is None:
            raise HTTPException(status_code=409, detail="DOCX is not available")
        safe_name = Path(job.filename).name
        return Response(
            content=job.document,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            headers={
                "Content-Disposition": (
                    "attachment; filename*=UTF-8''" + quote(safe_name)
                )
            },
        )

    @app.delete("/v1/calendar-jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_job(job_id: str) -> Response:
        try:
            deleted = jobs.delete(job_id)
        except JobRunningError as error:
            raise HTTPException(status_code=409, detail="Job is still running") from error
        if not deleted:
            raise HTTPException(status_code=404, detail="Job not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


app = create_app()
