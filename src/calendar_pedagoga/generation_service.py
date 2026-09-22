"""Bounded, single-worker lifecycle for calendar generation jobs."""

from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
from queue import Empty, Full, Queue
import shutil
import signal
import tempfile
from threading import Event, RLock, Thread
import time
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from calendar_pedagoga.generation_contract import (
    PIPELINE_CONTRACT,
    ConfirmedStudyPlanDTO,
    GenerationContractError,
    ManualSemanticConfirmationDTO,
    SemanticReviewCaseDTO,
    current_generator_revision,
    decode_match_reviews,
    decode_program_overlay,
)
from calendar_pedagoga.organization_template import select_calendar_template
from calendar_pedagoga.pipeline import (
    CalendarDocumentStatus,
    PipelineError,
    PipelineResult,
    run_calendar_pipeline,
)
from calendar_pedagoga.program_parsing import parse_program
from calendar_pedagoga.program_structure_confirmation import overlay_confirmed_program


MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
DEFAULT_QUEUE_CAPACITY = 4
DEFAULT_JOB_TIMEOUT_SECONDS = 12 * 60.0
DEFAULT_JOB_TTL_SECONDS = 30 * 60.0


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class JobPhase(StrEnum):
    VALIDATION = "VALIDATION"
    SCHEDULE = "SCHEDULE"
    SEMANTIC = "SEMANTIC"
    DOCX = "DOCX"
    LIBREOFFICE_QA = "LIBREOFFICE_QA"


TERMINAL_STATES = {JobState.SUCCEEDED, JobState.FAILED, JobState.EXPIRED}


class RevisionMismatchError(GenerationContractError):
    def __init__(self, expected: str, actual: object) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__("Версия генератора Streamlit не совпадает с Render API.")


class QueueCapacityError(RuntimeError):
    pass


class IdempotencyConflictError(GenerationContractError):
    pass


@dataclass
class GenerationJob:
    job_id: str
    state: JobState = JobState.QUEUED
    phase: JobPhase | None = None
    pipeline_status: CalendarDocumentStatus | None = None
    review_cases: tuple[dict[str, Any], ...] = ()
    confirmation_errors: tuple[tuple[str, tuple[str, ...]], ...] = ()
    warnings: tuple[str, ...] = ()
    filename: str | None = None
    document: bytes | None = field(default=None, repr=False)
    error: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: _utc_now())
    updated_at: str = field(default_factory=lambda: _utc_now())
    finished_monotonic: float | None = field(default=None, repr=False)
    payload: dict[str, Any] | None = field(default=None, repr=False)
    idempotency_key: str | None = field(default=None, repr=False)
    request_fingerprint: str | None = field(default=None, repr=False)
    temp_dir: str | None = field(default=None, repr=False)

    def public_status(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_state": self.state.value,
            "phase": self.phase.value if self.phase is not None else None,
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
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class JobSubmission:
    job: GenerationJob
    created: bool
    response: Mapping[str, Any]


PipelineRunner = Callable[..., PipelineResult]


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    overlay_value = payload.get("program_overlay")
    if overlay_value is not None:
        program = overlay_confirmed_program(
            program, decode_program_overlay(overlay_value)
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
    match_reviews = decode_match_reviews(payload.get("match_reviews", []))
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
        match_reviews=match_reviews,
        confirmations=confirmations,
    )


def _request_fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_submission(payload: Mapping[str, Any], revision: str) -> None:
    if payload.get("pipeline_contract") != PIPELINE_CONTRACT:
        raise GenerationContractError(
            f"Нужен pipeline_contract={PIPELINE_CONTRACT}."
        )
    requested_revision = payload.get("generator_revision")
    if requested_revision != revision:
        raise RevisionMismatchError(revision, requested_revision)


def _send(connection: Connection, kind: str, value: object) -> None:
    connection.send((kind, value))


@contextmanager
def _pipeline_phase_reporting(connection: Connection) -> Iterator[None]:
    """Report exact internal boundaries without changing pipeline behavior."""

    import calendar_pedagoga.pipeline as pipeline_module

    names = (
        ("build_schedule", JobPhase.SCHEDULE),
        ("_build_pipeline_lesson_content_outcome", JobPhase.SEMANTIC),
        ("generate_calendar_docx", JobPhase.DOCX),
    )
    originals: dict[str, Callable[..., Any]] = {}
    for name, phase in names:
        original = getattr(pipeline_module, name)
        originals[name] = original

        def wrapper(*args: Any, _original=original, _phase=phase, **kwargs: Any):
            _send(connection, "phase", _phase.value)
            return _original(*args, **kwargs)

        setattr(pipeline_module, name, wrapper)
    try:
        yield
    finally:
        for name, original in originals.items():
            setattr(pipeline_module, name, original)


def _process_main(
    connection: Connection,
    payload: Mapping[str, Any],
    runner: PipelineRunner,
    temp_dir: str,
) -> None:
    if os.name != "nt":
        os.setsid()
    os.environ["TMP"] = temp_dir
    os.environ["TEMP"] = temp_dir
    os.environ["TMPDIR"] = temp_dir
    tempfile.tempdir = temp_dir
    try:
        _send(connection, "phase", JobPhase.VALIDATION.value)
        inputs = _decode_generation_payload(payload)

        def progress(label: str) -> None:
            if "Проверяем готовый документ" in label:
                _send(connection, "phase", JobPhase.LIBREOFFICE_QA.value)

        with _pipeline_phase_reporting(connection):
            result = runner(
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
        _send(
            connection,
            "result",
            {
                "pipeline_status": result.status.value,
                "review_cases": tuple(
                    SemanticReviewCaseDTO.from_model(case).to_dict()
                    for case in result.review_cases
                ),
                "confirmation_errors": result.confirmation_errors,
                "warnings": result.warnings,
                "filename": result.filename,
                "document": result.content,
            },
        )
    except (GenerationContractError, PipelineError, ValueError) as error:
        _send(
            connection,
            "hard_block",
            {
                "code": "HARD_BLOCK",
                "stage": "PIPELINE",
                "message": str(error),
                "retryable": False,
            },
        )
    except Exception as error:
        _send(
            connection,
            "failed",
            {
                "code": "GENERATION_FAILED",
                "stage": "WORKER",
                "message": f"{type(error).__name__}: {error}",
                "retryable": True,
            },
        )
    finally:
        connection.close()


def _terminate_process_tree(process: multiprocessing.Process) -> None:
    if process.pid is None or not process.is_alive():
        return
    if os.name == "nt":
        _terminate_windows_process_tree(process.pid)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)


def _terminate_windows_process_tree(root_pid: int) -> None:
    """Terminate descendants before their parent using the Win32 process snapshot."""

    from ctypes import wintypes

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    children: dict[int, list[int]] = {}
    if snapshot != invalid_handle:
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        found = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while found:
            children.setdefault(int(entry.th32ParentProcessID), []).append(
                int(entry.th32ProcessID)
            )
            found = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
        kernel32.CloseHandle(snapshot)

    termination_order: list[int] = []

    def collect(pid: int) -> None:
        for child_pid in children.get(pid, ()):
            collect(child_pid)
        termination_order.append(pid)

    collect(root_pid)
    for pid in termination_order:
        handle = kernel32.OpenProcess(0x0001 | 0x00100000, False, pid)
        if not handle:
            continue
        try:
            kernel32.TerminateProcess(handle, 1)
            kernel32.WaitForSingleObject(handle, 5000)
        finally:
            kernel32.CloseHandle(handle)


class GenerationService:
    """In-memory metadata/document store with one isolated pipeline worker."""

    def __init__(
        self,
        *,
        runner: PipelineRunner = run_calendar_pipeline,
        revision_provider: Callable[[], str] = current_generator_revision,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
        job_timeout_seconds: float = DEFAULT_JOB_TIMEOUT_SECONDS,
        ttl_seconds: float = DEFAULT_JOB_TTL_SECONDS,
        cleanup_interval_seconds: float | None = None,
        mp_context: multiprocessing.context.BaseContext | None = None,
    ) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        if job_timeout_seconds <= 0 or ttl_seconds <= 0:
            raise ValueError("timeouts must be positive")
        self._runner = runner
        self._revision_provider = revision_provider
        self._queue: Queue[str | None] = Queue(maxsize=queue_capacity)
        self._queue_capacity = queue_capacity
        self._job_timeout = job_timeout_seconds
        self._ttl = ttl_seconds
        self._cleanup_interval = cleanup_interval_seconds or min(60.0, ttl_seconds / 4)
        self._mp = mp_context or multiprocessing.get_context("spawn")
        self._jobs: dict[str, GenerationJob] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = RLock()
        self._stop = Event()
        self._active_job_id: str | None = None
        self._active_process: multiprocessing.Process | None = None
        self._worker = Thread(target=self._worker_loop, name="calendar-worker", daemon=True)
        self._janitor = Thread(target=self._janitor_loop, name="calendar-janitor", daemon=True)
        self._worker.start()
        self._janitor.start()

    @property
    def revision(self) -> str:
        return self._revision_provider()

    @property
    def queue_capacity(self) -> int:
        return self._queue_capacity

    def queue_metadata(self) -> dict[str, int]:
        with self._lock:
            running = int(self._active_job_id is not None)
        return {
            "capacity": self._queue_capacity,
            "pending": self._queue.qsize(),
            "running": running,
            "concurrency": 1,
        }

    def submit(
        self,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> JobSubmission:
        data = dict(payload)
        _validate_submission(data, self.revision)
        payload_key = data.pop("idempotency_key", None)
        if idempotency_key is not None and payload_key not in {None, idempotency_key}:
            raise IdempotencyConflictError(
                "Idempotency-Key header не совпадает с полем запроса."
            )
        key_value = idempotency_key if idempotency_key is not None else payload_key
        key = str(key_value).strip() if key_value is not None else None
        if key_value is not None and (not key or len(key) > 128):
            raise GenerationContractError(
                "idempotency_key должен содержать от 1 до 128 символов."
            )
        fingerprint = _request_fingerprint(data)
        with self._lock:
            self._expire_locked(time.monotonic())
            if key is not None and key in self._idempotency:
                existing = self._jobs.get(self._idempotency[key])
                if existing is not None and existing.state is not JobState.EXPIRED:
                    if existing.request_fingerprint != fingerprint:
                        raise IdempotencyConflictError(
                            "Этот idempotency_key уже связан с другим запросом."
                        )
                    return JobSubmission(existing, False, existing.public_status())
                self._idempotency.pop(key, None)

            job = GenerationJob(
                job_id=uuid4().hex,
                payload=data,
                idempotency_key=key,
                request_fingerprint=fingerprint,
            )
            self._jobs[job.job_id] = job
            if key is not None:
                self._idempotency[key] = job.job_id
            response = job.public_status()
            try:
                self._queue.put_nowait(job.job_id)
            except Full as error:
                self._jobs.pop(job.job_id, None)
                if key is not None:
                    self._idempotency.pop(key, None)
                raise QueueCapacityError("Очередь генерации заполнена.") from error
        return JobSubmission(job, True, response)

    def get(self, job_id: str) -> GenerationJob | None:
        with self._lock:
            self._expire_locked(time.monotonic())
            return self._jobs.get(job_id)

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._expire_locked(time.monotonic())
            job = self._jobs.get(job_id)
            return None if job is None else job.public_status()

    def document_snapshot(
        self, job_id: str
    ) -> tuple[JobState, str | None, bytes | None, dict[str, Any] | None] | None:
        with self._lock:
            self._expire_locked(time.monotonic())
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return job.state, job.filename, job.document, job.error

    def lost_status(self, job_id: str) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "job_state": JobState.FAILED.value,
            "phase": None,
            "pipeline_status": None,
            "review_cases": [],
            "confirmation_errors": [],
            "warnings": [],
            "docx_available": False,
            "filename": None,
            "error": {
                "code": "JOB_LOST",
                "stage": "WORKER",
                "message": "Job не найден после перезапуска или очистки worker.",
                "retryable": True,
            },
            "created_at": None,
            "updated_at": _utc_now(),
        }

    def delete(self, job_id: str) -> bool:
        process: multiprocessing.Process | None = None
        temp_dir: str | None = None
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if job is None:
                return False
            if job.idempotency_key is not None:
                self._idempotency.pop(job.idempotency_key, None)
            job.payload = None
            job.document = None
            temp_dir = job.temp_dir
            if self._active_job_id == job_id:
                process = self._active_process
        if process is not None:
            _terminate_process_tree(process)
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return True

    def cleanup_expired(self) -> int:
        with self._lock:
            return self._expire_locked(time.monotonic())

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        with self._lock:
            process = self._active_process
        if process is not None:
            _terminate_process_tree(process)
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        self._worker.join(timeout=10)
        self._janitor.join(timeout=10)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=0.2)
            except Empty:
                continue
            try:
                if job_id is None:
                    return
                self._run_job(job_id)
            finally:
                self._queue.task_done()

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.payload is None:
                return
            temp_dir = tempfile.mkdtemp(prefix=f"calendar_job_{job_id[:8]}_")
            job.temp_dir = temp_dir
            payload = job.payload
            job.state = JobState.RUNNING
            job.phase = JobPhase.VALIDATION
            job.updated_at = _utc_now()
        parent_connection, child_connection = self._mp.Pipe(duplex=False)
        process = self._mp.Process(
            target=_process_main,
            args=(child_connection, payload, self._runner, temp_dir),
            name=f"calendar-job-{job_id[:8]}",
        )
        start_error: Exception | None = None
        with self._lock:
            if job_id not in self._jobs:
                parent_connection.close()
                child_connection.close()
                shutil.rmtree(temp_dir, ignore_errors=True)
                return
            self._active_job_id = job_id
            self._active_process = process
            try:
                process.start()
            except Exception as error:
                start_error = error
                self._active_job_id = None
                self._active_process = None
        if start_error is not None:
            parent_connection.close()
            child_connection.close()
            self._finish(
                job_id,
                "failed",
                {
                    "code": "WORKER_START_FAILED",
                    "stage": "WORKER",
                    "message": f"{type(start_error).__name__}: worker не запущен.",
                    "retryable": True,
                },
            )
            shutil.rmtree(temp_dir, ignore_errors=True)
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job.temp_dir = None
            return
        child_connection.close()
        deadline = time.monotonic() + self._job_timeout
        terminal: tuple[str, Any] | None = None
        try:
            while True:
                with self._lock:
                    cancelled = job_id not in self._jobs
                if cancelled or self._stop.is_set():
                    _terminate_process_tree(process)
                    return
                if time.monotonic() >= deadline:
                    _terminate_process_tree(process)
                    terminal = (
                        "failed",
                        {
                            "code": "JOB_TIMEOUT",
                            "stage": "WORKER",
                            "message": "Превышено общее время генерации документа.",
                            "retryable": True,
                        },
                    )
                    break
                if parent_connection.poll(0.05):
                    try:
                        kind, value = parent_connection.recv()
                    except EOFError:
                        kind = "eof"
                        value = None
                    if kind == "phase":
                        self._set_phase(job_id, JobPhase(value))
                        continue
                    if kind in {"result", "hard_block", "failed"}:
                        terminal = (kind, value)
                        break
                if not process.is_alive():
                    terminal = (
                        "failed",
                        {
                            "code": "JOB_LOST",
                            "stage": "WORKER",
                            "message": "Worker завершился без итогового статуса.",
                            "retryable": True,
                        },
                    )
                    break
            process.join(timeout=5)
            if process.is_alive():
                _terminate_process_tree(process)
            if terminal is not None:
                self._finish(job_id, *terminal)
        finally:
            parent_connection.close()
            shutil.rmtree(temp_dir, ignore_errors=True)
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
                    self._active_process = None
                job = self._jobs.get(job_id)
                if job is not None:
                    job.temp_dir = None

    def _set_phase(self, job_id: str, phase: JobPhase) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.state is JobState.RUNNING:
                job.phase = phase
                job.updated_at = _utc_now()

    def _finish(self, job_id: str, kind: str, value: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.payload = None
            job.finished_monotonic = time.monotonic()
            job.updated_at = _utc_now()
            if kind == "result":
                job.state = JobState.SUCCEEDED
                job.pipeline_status = CalendarDocumentStatus(value["pipeline_status"])
                job.review_cases = tuple(value["review_cases"])
                job.confirmation_errors = tuple(value["confirmation_errors"])
                job.warnings = tuple(value["warnings"])
                job.filename = value["filename"]
                job.document = value["document"]
                job.error = None
            elif kind == "hard_block":
                job.state = JobState.SUCCEEDED
                job.pipeline_status = CalendarDocumentStatus.HARD_BLOCK
                job.document = None
                job.filename = None
                job.error = dict(value)
            else:
                job.state = JobState.FAILED
                job.pipeline_status = None
                job.document = None
                job.filename = None
                job.error = dict(value)

    def _expire_locked(self, now: float) -> int:
        expired = 0
        for job in self._jobs.values():
            if (
                job.state in {JobState.SUCCEEDED, JobState.FAILED}
                and job.finished_monotonic is not None
                and now - job.finished_monotonic >= self._ttl
            ):
                if job.idempotency_key is not None:
                    self._idempotency.pop(job.idempotency_key, None)
                job.state = JobState.EXPIRED
                job.document = None
                job.filename = None
                job.payload = None
                job.pipeline_status = None
                job.review_cases = ()
                job.confirmation_errors = ()
                job.warnings = ()
                job.error = {
                    "code": "JOB_EXPIRED",
                    "stage": "CLEANUP",
                    "message": "Срок хранения результата job истёк.",
                    "retryable": True,
                }
                job.updated_at = _utc_now()
                expired += 1
        return expired

    def _janitor_loop(self) -> None:
        while not self._stop.wait(self._cleanup_interval):
            self.cleanup_expired()


# Backwards-compatible name from contract stage 1.
GenerationJobStore = GenerationService
