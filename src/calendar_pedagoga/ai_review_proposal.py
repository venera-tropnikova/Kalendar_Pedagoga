"""Constrained AI fallback for review weeks that stay empty after PASS 2.

The provider only proposes wording: acceptance stays with the existing
deterministic gates (SOURCE coverage, R13, grammar, CONTROL coverage,
verbosity).  A proposal that cannot be proved from the week's own SOURCE leaves
the calendar cells empty, exactly as before this level existed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging
from typing import Any, Protocol

from calendar_pedagoga.ai_provider import (
    AIProviderError,
    DEFAULT_GIGACHAT_MODEL,
    GIGACHAT_PERS_SCOPE,
    _gigachat_message_text,
    _parse_gigachat_json,
    _read_flag,
    _read_secret,
)
from calendar_pedagoga.content_engine_v2 import (
    LessonContentV2Row,
    REQUIRED_ACTION,
    _control_result_obligations,
    _leading_finite_verb,
    _meaning_stems,
    _normalize_spaces,
    _r13_must_abstain_action_reconstruction,
    _result_control_segments,
    _result_sentences,
    _role_is_required,
    validate_manual_lesson_content,
)
from calendar_pedagoga.semantic_review import _candidate_is_source_grounded


LOGGER = logging.getLogger(__name__)

AI_REVIEW_FALLBACK_FLAG = "CALENDAR_AI_REVIEW_FALLBACK"
MAX_AI_CALLS_PER_CALENDAR = 12
AI_RETRY_ROUNDS = 1
AI_TIMEOUT_SECONDS = 60.0
AI_MAX_OUTPUT_TOKENS = 400

INSTRUCTIONS = (
    "Сформулируй две строки календарного плана: result и control.",
    "Разрешено опираться только на переданные topic_title, lesson_type, "
    "theory_text, practice_text, program_content и required_clauses.",
    "result — короткие предложения о том, что умеет обучающийся, в третьем "
    "лице единственного числа настоящего времени: «Называет…», «Определяет…», "
    "«Характеризует…».",
    "Каждая клауза из required_clauses должна быть отражена в result своими "
    "предметными объектами.",
    "Запрещено добавлять предметные объекты, свойства, определения, примеры, "
    "классификации и числа, которых нет в переданном источнике.",
    "control — короткий нейтральный способ проверки всего, что заявлено в "
    "result; новые предметные факты в control запрещены.",
    "Если формулировку нельзя подтвердить источником, верни пустые строки.",
    "Верни только JSON-объект с ключами result и control, без markdown, "
    "пояснений, нумерации занятий и любых других полей.",
)


class ReviewProposalError(AIProviderError):
    """Recoverable failure of the AI fallback; the pipeline keeps working."""


@dataclass(frozen=True)
class AIReviewRequest:
    """Exactly the week's own evidence: SOURCE, topic, TYPE, REQUIRED clauses."""

    week_number: int
    topic_title: str
    lesson_type: str
    theory_text: str
    practice_text: str
    program_content: str
    required_clauses: tuple[str, ...]

    @property
    def source_text(self) -> str:
        return " ".join(
            (
                self.topic_title,
                self.theory_text,
                self.practice_text,
                self.program_content,
            )
        )

    def to_payload(self) -> dict[str, Any]:
        """Provider payload without week numbers, program name or prior drafts."""

        return {
            "topic_title": self.topic_title,
            "lesson_type": self.lesson_type,
            "theory_text": self.theory_text,
            "practice_text": self.practice_text,
            "program_content": self.program_content,
            "required_clauses": list(self.required_clauses),
        }


@dataclass(frozen=True)
class AIReviewProposal:
    result: str
    control: str


class ReviewProposalProvider(Protocol):
    model: str

    def propose(self, request: AIReviewRequest) -> AIReviewProposal | None: ...


def review_proposal_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["result", "control"],
        "properties": {
            "result": {"type": "string"},
            "control": {"type": "string"},
        },
    }


_PROPOSAL_CACHE: dict[str, AIReviewProposal | None] = {}


def clear_review_proposal_cache() -> None:
    _PROPOSAL_CACHE.clear()


def _cache_key(model: str, request: AIReviewRequest) -> str:
    payload = json.dumps(
        [model, request.to_payload()],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class ReviewProposalSession:
    """One calendar's AI budget: hard call cap, one retry, process-local cache."""

    provider: ReviewProposalProvider
    max_calls: int = MAX_AI_CALLS_PER_CALENDAR
    retries: int = AI_RETRY_ROUNDS
    calls: int = 0
    errors: list[str] = field(default_factory=list)

    def propose(self, request: AIReviewRequest) -> AIReviewProposal | None:
        key = _cache_key(self.provider.model, request)
        if key in _PROPOSAL_CACHE:
            return _PROPOSAL_CACHE[key]
        for attempt in range(self.retries + 1):
            if self.calls >= self.max_calls:
                LOGGER.warning(
                    "AI-fallback: достигнут предел %s вызовов на календарь",
                    self.max_calls,
                )
                return None
            self.calls += 1
            try:
                proposal = self.provider.propose(request)
            except Exception as error:  # noqa: BLE001 - fallback must not break generation
                self.errors.append(str(error))
                LOGGER.warning("AI-fallback: неделя %s, %s", request.week_number, error)
                if attempt >= self.retries:
                    return None
                continue
            _PROPOSAL_CACHE[key] = proposal
            return proposal
        return None


class GigaChatReviewProposalProvider:
    """GigaChat asked for RESULT/CONTROL only, with json_schema response."""

    scope = GIGACHAT_PERS_SCOPE

    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
        timeout: float = AI_TIMEOUT_SECONDS,
    ) -> None:
        resolved_model = model or _read_secret("GIGACHAT_MODEL") or DEFAULT_GIGACHAT_MODEL
        if client is None:
            credentials = _read_secret("GIGACHAT_CREDENTIALS")
            if not credentials:
                raise ReviewProposalError("GIGACHAT_CREDENTIALS не настроен.")
            from gigachat import GigaChat

            client = GigaChat(
                credentials=credentials,
                scope=GIGACHAT_PERS_SCOPE,
                model=resolved_model,
                verify_ssl_certs=_read_flag("GIGACHAT_VERIFY_SSL_CERTS", True),
                timeout=timeout,
            )
        self.client = client
        self.model = resolved_model

    def propose(self, request: AIReviewRequest) -> AIReviewProposal | None:
        user_content = json.dumps(
            {
                "response_schema": review_proposal_schema(),
                "payload": request.to_payload(),
            },
            ensure_ascii=False,
        )
        response = self._create_chat("\n".join(INSTRUCTIONS), user_content)
        parsed = _parse_gigachat_json(_gigachat_message_text(response))
        result = str(parsed.get("result") or "").strip()
        control = str(parsed.get("control") or "").strip()
        if not result or not control:
            return None
        return AIReviewProposal(result=result, control=control)

    def _create_chat(self, instructions: str, user_content: str) -> Any:
        chat_api = getattr(self.client, "chat", None)
        if not callable(chat_api):
            raise ReviewProposalError("Клиент GigaChat не поддерживает chat API.")
        try:
            from gigachat.models import Chat, Messages, MessagesRole
        except Exception as error:  # noqa: BLE001 - SDK layout differences
            raise ReviewProposalError(
                "Клиент GigaChat не поддерживает chat API."
            ) from error
        base = {
            "model": self.model,
            "messages": [
                Messages(role=MessagesRole.SYSTEM, content=instructions),
                Messages(role=MessagesRole.USER, content=user_content),
            ],
            "max_tokens": AI_MAX_OUTPUT_TOKENS,
        }
        response_format = {
            "type": "json_schema",
            "schema": review_proposal_schema(),
            "strict": True,
        }
        try:
            return chat_api(Chat(**base, response_format=response_format))
        except Exception:  # noqa: BLE001 - older SDK/model without json_schema
            return chat_api(Chat(**base))


def ai_review_fallback_enabled() -> bool:
    """Feature flag; the AI level stays off unless it is switched on."""

    return _read_flag(AI_REVIEW_FALLBACK_FLAG, False)


def build_review_proposal_session() -> ReviewProposalSession | None:
    """Session or ``None``; a missing key or SDK simply disables the level."""

    if not ai_review_fallback_enabled():
        return None
    try:
        provider = GigaChatReviewProposalProvider()
    except Exception as error:  # noqa: BLE001 - configuration must not break generation
        LOGGER.warning("AI-fallback недоступен: %s", error)
        return None
    return ReviewProposalSession(provider=provider)


def _result_is_gate_checkable(result: str) -> bool:
    """Gates prove only what they can parse; unparsable RESULT is not proof.

    CE2 gates were written for CE2's own wording, so a sentence without a
    recognised finite verb yields no obligation and would pass CONTROL coverage
    vacuously.  Foreign text has to earn the same parse before it is judged.
    """

    sentences = _result_sentences(result)
    if not sentences:
        return False
    if any(not _leading_finite_verb(sentence) for sentence in sentences):
        return False
    obligations = _control_result_obligations(result) or [
        (verb, obj) for verb, obj in _result_control_segments(result) if obj
    ]
    return bool(obligations)


def _clauses_fully_cited(result: str, clauses: tuple[str, ...]) -> bool:
    """Every meaning stem of every REQUIRED clause has to survive in RESULT."""

    low = _normalize_spaces(result).casefold()
    for clause in clauses:
        stems = _meaning_stems(clause)
        if not stems or any(stem[:4] not in low for stem in stems):
            return False
    return True


def ai_review_request(row: LessonContentV2Row) -> AIReviewRequest | None:
    """Request for one reviewed week, or ``None`` when AI must not be asked."""

    role_map = dict(row.clause_roles)
    required = tuple(
        clause
        for clause, _status in row.clause_coverage
        if _role_is_required(role_map.get(clause, REQUIRED_ACTION))
    )
    if not required:
        return None
    if any(_r13_must_abstain_action_reconstruction(clause) for clause in required):
        return None
    return AIReviewRequest(
        week_number=row.source.week_number,
        topic_title=row.source.topic_title,
        lesson_type=row.lesson_type,
        theory_text=row.theory_text,
        practice_text=row.practice_text,
        program_content=row.source.program_content_full,
        required_clauses=required,
    )


def ai_review_proposal_candidate(
    row: LessonContentV2Row,
    session: ReviewProposalSession | None,
) -> tuple[str, str] | None:
    """Third level: AI proposes, the existing gates decide.

    The proposal is re-proved against the same gates as a manual confirmation
    plus SOURCE grounding, so nothing enters the DOCX on the AI's authority.
    """

    if session is None:
        return None
    request = ai_review_request(row)
    if request is None:
        return None
    proposal = session.propose(request)
    if proposal is None:
        return None
    if not _result_is_gate_checkable(proposal.result) or not _clauses_fully_cited(
        proposal.result, request.required_clauses
    ):
        LOGGER.info(
            "AI-fallback: неделя %s недоказуема гейтами", request.week_number
        )
        return None
    verdict = validate_manual_lesson_content(
        row,
        planned_result=proposal.result,
        assessment_method=proposal.control,
    )
    if not verdict.accepted:
        LOGGER.info(
            "AI-fallback: неделя %s отклонена гейтами: %s",
            request.week_number,
            "; ".join(verdict.issues),
        )
        return None
    if not _candidate_is_source_grounded(
        proposal.result, request.required_clauses, request.source_text
    ):
        LOGGER.info(
            "AI-fallback: неделя %s не подтверждена SOURCE", request.week_number
        )
        return None
    return proposal.result, proposal.control
