"""HTTP transport for the local calendar generation service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Response, status

from calendar_pedagoga.generation_contract import (
    PIPELINE_CONTRACT,
    GenerationContractError,
)
from calendar_pedagoga.generation_service import (
    GenerationJobStore,
    GenerationService,
    IdempotencyConflictError,
    JobState,
    QueueCapacityError,
    RevisionMismatchError,
)
from calendar_pedagoga.generator_revision import generator_git_commit


def create_app(*, service: GenerationService | None = None, store=None) -> FastAPI:
    """Build the API; ``store`` remains accepted for stage-1 callers."""

    if service is not None and store is not None:
        raise ValueError("Pass either service or store, not both.")
    provided_jobs = service or store

    @asynccontextmanager
    async def lifespan(active_app: FastAPI):
        jobs = provided_jobs or GenerationService()
        active_app.state.generation_jobs = jobs
        try:
            yield
        finally:
            jobs.close()

    app = FastAPI(
        title="Calendar Pedagoga Generation API",
        version="1",
        lifespan=lifespan,
    )

    def jobs() -> GenerationService:
        active = getattr(app.state, "generation_jobs", None)
        if active is None:
            raise RuntimeError("Generation service lifespan is not active.")
        return active

    @app.get("/health")
    def health() -> dict[str, Any]:
        active = jobs()
        return {
            "status": "ok",
            "pipeline_contract": PIPELINE_CONTRACT,
            "generator_revision": active.revision,
            "generator_git_commit": generator_git_commit(),
            "queue": active.queue_metadata(),
        }

    @app.post("/v1/calendar-jobs", status_code=status.HTTP_202_ACCEPTED)
    def create_job(
        payload: dict[str, Any],
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            submission = jobs().submit(payload, idempotency_key=idempotency_key)
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
        except IdempotencyConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "IDEMPOTENCY_CONFLICT", "message": str(error)},
            ) from error
        except QueueCapacityError as error:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "QUEUE_FULL", "message": str(error)},
                headers={"Retry-After": "5"},
            ) from error
        except GenerationContractError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "INVALID_CONTRACT", "message": str(error)},
            ) from error
        response = dict(submission.response)
        response["idempotent_replay"] = not submission.created
        return response

    @app.get("/v1/calendar-jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        active = jobs()
        job_status = active.status(job_id)
        if job_status is None:
            return active.lost_status(job_id)
        return job_status

    @app.get("/v1/calendar-jobs/{job_id}/document")
    def get_document(job_id: str) -> Response:
        active = jobs()
        snapshot = active.document_snapshot(job_id)
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail=active.lost_status(job_id)["error"],
            )
        job_state, filename, document, error = snapshot
        if job_state is JobState.EXPIRED:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail=error,
            )
        if document is None or filename is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "DOCUMENT_NOT_AVAILABLE", "message": "DOCX недоступен."},
            )
        safe_name = Path(filename).name
        return Response(
            content=document,
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
        if not jobs().delete(job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


app = create_app()


__all__ = ["GenerationJobStore", "GenerationService", "app", "create_app"]
