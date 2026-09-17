import base64
import time
from pathlib import Path

from fastapi.testclient import TestClient

from calendar_pedagoga.confirmed_study_plan import confirmed_plan_from_external_utp
from calendar_pedagoga.generation_api import GenerationJobStore, create_app
from calendar_pedagoga.generation_contract import PIPELINE_CONTRACT, ConfirmedStudyPlanDTO
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.pipeline import (
    CalendarDocumentStatus,
    PipelineError,
    PipelineResult,
)
from calendar_pedagoga.semantic_review import SemanticReviewCase


REFERENCES = Path(__file__).resolve().parents[1] / "references"
REVISION = "test-revision"


def _payload() -> dict:
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
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


def _terminal(client: TestClient, job_id: str) -> dict:
    for _ in range(1000):
        response = client.get(f"/v1/calendar-jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["job_state"] in {"SUCCEEDED", "FAILED"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not finish")


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


def test_health_and_final_document_lifecycle() -> None:
    store = GenerationJobStore(
        runner=lambda *_args, **_kwargs: _result(CalendarDocumentStatus.FINAL_READY),
        revision_provider=lambda: REVISION,
    )
    with TestClient(create_app(store=store)) as client:
        health = client.get("/health").json()
        assert health["pipeline_contract"] == 1
        assert health["generator_revision"] == REVISION

        created = client.post("/v1/calendar-jobs", json=_payload())
        assert created.status_code == 202
        job_id = created.json()["job_id"]
        completed = _terminal(client, job_id)
        assert completed["pipeline_status"] == "FINAL_READY"
        assert completed["docx_available"] is True

        document = client.get(f"/v1/calendar-jobs/{job_id}/document")
        assert document.status_code == 200
        assert document.content.startswith(b"PK\x03\x04")
        assert client.delete(f"/v1/calendar-jobs/{job_id}").status_code == 204
        assert client.get(f"/v1/calendar-jobs/{job_id}").status_code == 404


def test_draft_returns_review_cases_and_document() -> None:
    store = GenerationJobStore(
        runner=lambda *_args, **_kwargs: _result(CalendarDocumentStatus.DRAFT_READY),
        revision_provider=lambda: REVISION,
    )
    with TestClient(create_app(store=store)) as client:
        job_id = client.post("/v1/calendar-jobs", json=_payload()).json()["job_id"]
        completed = _terminal(client, job_id)
        assert completed["pipeline_status"] == "DRAFT_READY"
        assert [case["week_number"] for case in completed["review_cases"]] == [2]
        assert client.get(f"/v1/calendar-jobs/{job_id}/document").status_code == 200


def test_hard_block_has_no_document() -> None:
    def blocked(*_args, **_kwargs):
        raise PipelineError("Повреждённый план")

    store = GenerationJobStore(runner=blocked, revision_provider=lambda: REVISION)
    with TestClient(create_app(store=store)) as client:
        job_id = client.post("/v1/calendar-jobs", json=_payload()).json()["job_id"]
        completed = _terminal(client, job_id)
        assert completed["pipeline_status"] == "HARD_BLOCK"
        assert completed["docx_available"] is False
        assert completed["error"]["retryable"] is False
        assert client.get(f"/v1/calendar-jobs/{job_id}/document").status_code == 409


def test_revision_mismatch_blocks_before_job_creation() -> None:
    store = GenerationJobStore(
        runner=lambda *_args, **_kwargs: _result(CalendarDocumentStatus.FINAL_READY),
        revision_provider=lambda: REVISION,
    )
    payload = _payload()
    payload["generator_revision"] = "stale"
    with TestClient(create_app(store=store)) as client:
        response = client.post("/v1/calendar-jobs", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "REVISION_MISMATCH"
