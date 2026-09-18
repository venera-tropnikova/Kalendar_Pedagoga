from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from calendar_pedagoga.ai_review_proposal import (
    AI_REVIEW_FALLBACK_FLAG,
    AIReviewProposal,
    AIReviewRequest,
    GigaChatReviewProposalProvider,
    MAX_AI_CALLS_PER_CALENDAR,
    ReviewProposalSession,
    ai_review_proposal_candidate,
    ai_review_request,
    build_review_proposal_session,
    clear_review_proposal_cache,
)
from calendar_pedagoga.content_engine_v2 import REQUIRED_ACTION
from test_semantic_review_core import _docx_rows_for, _review_row, _unsafe_proposal


SOURCE = "Правила безопасного поведения."
GOOD_RESULT = "Называет правила безопасного поведения."
GOOD_CONTROL = "устный опрос по правилам безопасного поведения"


class _FakeProvider:
    """Provider stub: canned proposal or a controlled API failure."""

    model = "fake-model"

    def __init__(
        self,
        result: str = GOOD_RESULT,
        control: str = GOOD_CONTROL,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.control = control
        self.error = error
        self.seen: list[AIReviewRequest] = []

    def propose(self, request: AIReviewRequest) -> AIReviewProposal | None:
        self.seen.append(request)
        if self.error is not None:
            raise self.error
        if not self.result:
            return None
        return AIReviewProposal(result=self.result, control=self.control)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_review_proposal_cache()
    yield
    clear_review_proposal_cache()


def _session(provider: _FakeProvider) -> ReviewProposalSession:
    return ReviewProposalSession(provider=provider)


def test_ai_candidate_fills_docx_cells_after_two_failed_passes() -> None:
    row = _unsafe_proposal(SOURCE)
    provider = _FakeProvider()
    session = _session(provider)

    marked, cases = _docx_rows_for(row, proposal_session=session)

    assert [case.week_number for case in cases] == [1]
    assert marked[0].planned_result == GOOD_RESULT
    assert marked[0].assessment_method == GOOD_CONTROL
    assert session.calls == 1
    assert len(provider.seen) == 1


def test_ai_request_carries_only_source_topic_type_and_clauses() -> None:
    row = _unsafe_proposal(SOURCE)

    request = ai_review_request(row)

    assert request is not None
    assert request.topic_title == row.source.topic_title
    assert request.lesson_type == row.lesson_type
    assert request.program_content == row.source.program_content_full
    assert request.required_clauses == ("Правила безопасного поведения",)
    payload = request.to_payload()
    assert set(payload) == {
        "topic_title",
        "lesson_type",
        "theory_text",
        "practice_text",
        "program_content",
        "required_clauses",
    }
    assert row.planned_result not in str(payload)
    assert row.assessment_method not in str(payload)


@pytest.mark.parametrize(
    ("result", "control"),
    [
        ("Called правила.", "x"),
        ("Называет правила.", "устный опрос по правилам"),
        (
            "Называет правила безопасного поведения и приёмы самостраховки.",
            "устный опрос по правилам безопасного поведения и приёмам самостраховки",
        ),
        (GOOD_RESULT, "устный опрос по истории"),
        ("", ""),
    ],
)
def test_unproven_ai_proposal_leaves_cells_empty(result: str, control: str) -> None:
    row = _unsafe_proposal(SOURCE)
    session = _session(_FakeProvider(result=result, control=control))

    marked, _cases = _docx_rows_for(row, proposal_session=session)

    assert marked[0].planned_result == ""
    assert marked[0].assessment_method == ""


def test_api_error_keeps_pipeline_running_with_empty_cells() -> None:
    row = _unsafe_proposal(SOURCE)
    provider = _FakeProvider(error=RuntimeError("GigaChat недоступен"))
    session = _session(provider)

    marked, _cases = _docx_rows_for(row, proposal_session=session)

    assert marked[0].planned_result == ""
    assert marked[0].assessment_method == ""
    assert session.calls == 2  # one retry only
    assert session.errors


def test_call_budget_is_capped_per_calendar() -> None:
    provider = _FakeProvider()
    session = _session(provider)
    rows = tuple(
        _unsafe_proposal(f"Правила безопасного поведения {index}.")
        for index in range(MAX_AI_CALLS_PER_CALENDAR + 3)
    )

    for row in rows:
        ai_review_proposal_candidate(row, session)

    assert session.calls == MAX_AI_CALLS_PER_CALENDAR
    assert len(provider.seen) == MAX_AI_CALLS_PER_CALENDAR


def test_identical_week_is_asked_only_once() -> None:
    row = _unsafe_proposal(SOURCE)
    provider = _FakeProvider()
    session = _session(provider)

    first = ai_review_proposal_candidate(row, session)
    second = ai_review_proposal_candidate(row, _session(provider))

    assert first == second == (GOOD_RESULT, GOOD_CONTROL)
    assert len(provider.seen) == 1


def test_r13_week_is_never_sent_to_ai() -> None:
    row = _review_row(text="Выполнение упражнения без страховки запрещено.")
    provider = _FakeProvider()
    session = _session(provider)

    marked, _cases = _docx_rows_for(row, proposal_session=session)

    assert ai_review_request(row) is None
    assert provider.seen == []
    assert marked[0].planned_result == ""
    assert marked[0].assessment_method == ""


def test_weeks_resolved_by_earlier_passes_are_never_sent_to_ai() -> None:
    safe = _review_row(1)
    second_pass = _unsafe_proposal("Виды туристских узлов.")
    provider = _FakeProvider()
    session = _session(provider)

    marked, cases = _docx_rows_for(
        safe, replace(second_pass, source=replace(second_pass.source, week_number=2)),
        proposal_session=session,
    )

    assert [case.week_number for case in cases] == [1, 2]
    assert marked[0].planned_result == safe.planned_result
    assert "туристских узлов" in marked[1].planned_result
    assert provider.seen == []
    assert session.calls == 0


def test_optional_only_week_is_not_sent_to_ai() -> None:
    row = replace(
        _unsafe_proposal(SOURCE),
        clause_roles=(("Правила безопасного поведения", "OPTIONAL"),),
    )

    assert ai_review_request(row) is None


def test_feature_flag_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv(AI_REVIEW_FALLBACK_FLAG, raising=False)

    assert build_review_proposal_session() is None


def test_missing_credentials_disable_the_level(monkeypatch) -> None:
    monkeypatch.setenv(AI_REVIEW_FALLBACK_FLAG, "1")
    monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)

    assert build_review_proposal_session() is None


def test_flag_on_with_credentials_builds_capped_session(monkeypatch) -> None:
    monkeypatch.setenv(AI_REVIEW_FALLBACK_FLAG, "1")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "test-credentials")
    monkeypatch.setattr(
        "calendar_pedagoga.ai_review_proposal.GigaChatReviewProposalProvider",
        lambda: _FakeProvider(),
    )

    session = build_review_proposal_session()

    assert session is not None
    assert session.max_calls == MAX_AI_CALLS_PER_CALENDAR
    assert session.retries == 1


class _FakeGigaChatClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.payloads: list[object] = []

    def chat(self, payload: object) -> object:
        self.payloads.append(payload)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.text))]
        )


def test_gigachat_provider_reads_result_and_control_only() -> None:
    row = _unsafe_proposal(SOURCE)
    request = ai_review_request(row)
    assert request is not None
    client = _FakeGigaChatClient(
        '{"result": "%s", "control": "%s"}' % (GOOD_RESULT, GOOD_CONTROL)
    )
    provider = GigaChatReviewProposalProvider(client=client, model="test-model")

    proposal = provider.propose(request)

    assert proposal == AIReviewProposal(result=GOOD_RESULT, control=GOOD_CONTROL)
    sent = client.payloads[0].messages[1].content
    assert row.assessment_method not in sent
    assert "week_number" not in sent


def test_gigachat_empty_answer_is_not_a_proposal() -> None:
    row = _unsafe_proposal(SOURCE)
    request = ai_review_request(row)
    assert request is not None
    client = _FakeGigaChatClient('{"result": "", "control": ""}')
    provider = GigaChatReviewProposalProvider(client=client, model="test-model")

    assert provider.propose(request) is None


def test_ai_level_is_skipped_without_session() -> None:
    row = _unsafe_proposal(SOURCE)

    marked, _cases = _docx_rows_for(row)

    assert marked[0].planned_result == ""
    assert marked[0].assessment_method == ""
    assert ai_review_proposal_candidate(row, None) is None


def test_required_clause_roles_are_respected() -> None:
    row = replace(
        _unsafe_proposal(SOURCE),
        clause_roles=(("Правила безопасного поведения", REQUIRED_ACTION),),
    )

    request = ai_review_request(row)

    assert request is not None
    assert request.required_clauses == ("Правила безопасного поведения",)
