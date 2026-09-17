import base64
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import shutil

from fastapi.testclient import TestClient

from calendar_pedagoga.confirmed_study_plan import confirmed_plan_from_external_utp
from calendar_pedagoga.generation_api import create_app
from calendar_pedagoga.generation_contract import PIPELINE_CONTRACT, ConfirmedStudyPlanDTO
from calendar_pedagoga.generation_service import GenerationService
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.pipeline import CalendarDocumentStatus, PipelineError, PipelineResult
from calendar_pedagoga.semantic_review import SemanticReviewCase


REFERENCES = Path(__file__).resolve().parents[1] / "references"
REVISION = "test-revision"


def _payload() -> dict:
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    plan = confirmed_plan_from_external_utp(
        parse_utp(utp_path), study_year=2, source_name=utp_path.name
    )
    return {
        "pipeline_contract": PIPELINE_CONTRACT,
        "generator_revision": REVISION,
        "plan": ConfirmedStudyPlanDTO.from_model(plan).to_dict(),
        "program": {
            "filename": program_path.name,
            "content_base64": base64.b64encode(program_path.read_bytes()).decode("ascii"),
        },
        "academic_year": "2026–2027",
        "source_plan_name": utp_path.name,
        "confirmations": {},
    }


def _terminal(client: TestClient, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/v1/calendar-jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["job_state"] in {"SUCCEEDED", "FAILED", "EXPIRED"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _wait_for_state(client: TestClient, job_id: str, state: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        payload = client.get(f"/v1/calendar-jobs/{job_id}").json()
        if payload["job_state"] == state:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {state}")


def _result(status: CalendarDocumentStatus) -> PipelineResult:
    cases = (
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
    ) if status is CalendarDocumentStatus.DRAFT_READY else ()
    return PipelineResult(
        filename="Черновик.docx" if cases else "План.docx",
        content=b"PK\x03\x04docx",
        warnings=(),
        resolved_lessons=(),
        status=status,
        review_cases=cases,
    )


def _final_runner(*_args, **_kwargs) -> PipelineResult:
    return _result(CalendarDocumentStatus.FINAL_READY)


def _draft_runner(*_args, **_kwargs) -> PipelineResult:
    return _result(CalendarDocumentStatus.DRAFT_READY)


def _blocked_runner(*_args, **_kwargs):
    raise PipelineError("Повреждённый план")


def _slow_runner(*_args, **_kwargs) -> PipelineResult:
    time.sleep(5)
    return _result(CalendarDocumentStatus.FINAL_READY)


def _runner_with_descendant(*_args, **kwargs) -> PipelineResult:
    marker = Path(kwargs["teacher_name"])
    marker.with_suffix(".started").write_text("started", encoding="utf-8")
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib,time;time.sleep(3);"
                f"pathlib.Path({str(marker)!r}).write_text('orphan', encoding='utf-8')"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(10)
    return _result(CalendarDocumentStatus.FINAL_READY)


def _service(runner=_final_runner, **kwargs) -> GenerationService:
    return GenerationService(
        runner=runner,
        revision_provider=lambda: REVISION,
        job_timeout_seconds=kwargs.pop("job_timeout_seconds", 20),
        ttl_seconds=kwargs.pop("ttl_seconds", 60),
        **kwargs,
    )


def test_health_and_final_document_lifecycle() -> None:
    with TestClient(create_app(service=_service())) as client:
        health = client.get("/health").json()
        assert health["pipeline_contract"] == 1
        assert health["generator_revision"] == REVISION
        assert health["queue"]["concurrency"] == 1

        created = client.post("/v1/calendar-jobs", json=_payload())
        assert created.status_code == 202
        assert created.json()["job_state"] == "QUEUED"
        job_id = created.json()["job_id"]
        completed = _terminal(client, job_id)
        assert completed["pipeline_status"] == "FINAL_READY"
        assert completed["docx_available"] is True

        document = client.get(f"/v1/calendar-jobs/{job_id}/document")
        assert document.status_code == 200
        assert document.content.startswith(b"PK\x03\x04")
        assert client.delete(f"/v1/calendar-jobs/{job_id}").status_code == 204
        lost = client.get(f"/v1/calendar-jobs/{job_id}").json()
        assert lost["job_state"] == "FAILED"
        assert lost["error"]["code"] == "JOB_LOST"


def test_draft_returns_review_cases_and_document() -> None:
    with TestClient(create_app(service=_service(_draft_runner))) as client:
        job_id = client.post("/v1/calendar-jobs", json=_payload()).json()["job_id"]
        completed = _terminal(client, job_id)
        assert completed["pipeline_status"] == "DRAFT_READY"
        assert [case["week_number"] for case in completed["review_cases"]] == [2]
        assert client.get(f"/v1/calendar-jobs/{job_id}/document").status_code == 200


def test_hard_block_has_no_document() -> None:
    with TestClient(create_app(service=_service(_blocked_runner))) as client:
        job_id = client.post("/v1/calendar-jobs", json=_payload()).json()["job_id"]
        completed = _terminal(client, job_id)
        assert completed["job_state"] == "SUCCEEDED"
        assert completed["pipeline_status"] == "HARD_BLOCK"
        assert completed["docx_available"] is False
        assert completed["error"]["retryable"] is False
        assert client.get(f"/v1/calendar-jobs/{job_id}/document").status_code == 409


def test_revision_mismatch_blocks_before_job_creation() -> None:
    payload = _payload()
    payload["generator_revision"] = "stale"
    with TestClient(create_app(service=_service())) as client:
        response = client.post("/v1/calendar-jobs", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "REVISION_MISMATCH"


def test_duplicate_idempotency_key_returns_original_job() -> None:
    with TestClient(create_app(service=_service())) as client:
        first = client.post(
            "/v1/calendar-jobs",
            json=_payload(),
            headers={"Idempotency-Key": "same-request"},
        )
        second = client.post(
            "/v1/calendar-jobs",
            json=_payload(),
            headers={"Idempotency-Key": "same-request"},
        )
        assert first.status_code == second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]
        assert second.json()["idempotent_replay"] is True

        changed = _payload()
        changed["teacher_name"] = "Другой запрос"
        conflict = client.post(
            "/v1/calendar-jobs",
            json=changed,
            headers={"Idempotency-Key": "same-request"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_queue_full_returns_429_without_creating_job() -> None:
    service = _service(_slow_runner, queue_capacity=1)
    with TestClient(create_app(service=service)) as client:
        started = time.monotonic()
        first = client.post("/v1/calendar-jobs", json=_payload()).json()["job_id"]
        assert time.monotonic() - started < 1
        _wait_for_state(client, first, "RUNNING")
        second = client.post("/v1/calendar-jobs", json=_payload())
        assert second.status_code == 202
        third = client.post("/v1/calendar-jobs", json=_payload())
        assert third.status_code == 429
        assert third.json()["detail"]["code"] == "QUEUE_FULL"
        assert service.queue_metadata()["pending"] == 1


def test_timeout_kills_descendant_process_group_and_cleans_job() -> None:
    service = _service(_runner_with_descendant, job_timeout_seconds=2)
    test_dir = Path(tempfile.mkdtemp(prefix="generation_timeout_test_"))
    try:
        with TestClient(create_app(service=service)) as client:
            payload = _payload()
            marker = test_dir / "orphan-marker.txt"
            payload["teacher_name"] = str(marker)
            job_id = client.post("/v1/calendar-jobs", json=payload).json()["job_id"]
            failed = _terminal(client, job_id)
            assert failed["job_state"] == "FAILED"
            assert failed["pipeline_status"] is None
            assert failed["error"]["code"] == "JOB_TIMEOUT"
            assert failed["docx_available"] is False
            assert service.get(job_id).payload is None
            assert not list(
                Path(tempfile.gettempdir()).glob(f"calendar_job_{job_id[:8]}_*")
            )
            assert marker.with_suffix(".started").is_file()
            time.sleep(3.2)
            assert not marker.exists()
            assert client.delete(f"/v1/calendar-jobs/{job_id}").status_code == 204
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_delete_running_job_terminates_worker_and_forgets_job() -> None:
    service = _service(_slow_runner)
    with TestClient(create_app(service=service)) as client:
        job_id = client.post("/v1/calendar-jobs", json=_payload()).json()["job_id"]
        _wait_for_state(client, job_id, "RUNNING")
        assert client.delete(f"/v1/calendar-jobs/{job_id}").status_code == 204
        lost = client.get(f"/v1/calendar-jobs/{job_id}").json()
        assert lost["job_state"] == "FAILED"
        assert lost["error"]["code"] == "JOB_LOST"
        assert not list(
            Path(tempfile.gettempdir()).glob(f"calendar_job_{job_id[:8]}_*")
        )


def test_ttl_cleanup_expires_document() -> None:
    service = _service(
        ttl_seconds=0.1,
        cleanup_interval_seconds=0.02,
    )
    with TestClient(create_app(service=service)) as client:
        job_id = client.post("/v1/calendar-jobs", json=_payload()).json()["job_id"]
        assert _terminal(client, job_id)["job_state"] == "SUCCEEDED"
        expired = _wait_for_state(client, job_id, "EXPIRED")
        assert expired["docx_available"] is False
        assert expired["error"]["code"] == "JOB_EXPIRED"
        response = client.get(f"/v1/calendar-jobs/{job_id}/document")
        assert response.status_code == 410
