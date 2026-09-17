from decimal import Decimal

import pytest

from calendar_pedagoga.confirmed_study_plan import confirmed_plan_from_manual
from calendar_pedagoga.generation_contract import (
    PIPELINE_CONTRACT,
    ConfirmedStudyPlanDTO,
    GenerationContractError,
    ManualSemanticConfirmationDTO,
    SemanticReviewCaseDTO,
)
from calendar_pedagoga.parsing import Hours, Topic
from calendar_pedagoga.semantic_review import (
    ManualSemanticConfirmation,
    SemanticReviewCase,
)


def _fractional_plan():
    return confirmed_plan_from_manual(
        study_year=1,
        topics=(
            Topic("1", "Дробная тема", Hours(Decimal("1.5"), Decimal("0.5"), 1)),
        ),
        total_hours=Decimal("1.5"),
        theory_hours=Decimal("0.5"),
        practice_hours=1,
        study_weeks=1,
        hours_per_week=Decimal("1.5"),
    )


def test_pipeline_contract_version_is_one() -> None:
    assert PIPELINE_CONTRACT == 1


def test_confirmed_plan_decimal_round_trip_uses_only_strings() -> None:
    original = _fractional_plan()
    payload = ConfirmedStudyPlanDTO.from_model(original).to_dict()

    assert payload["total_hours"] == "1.5"
    assert payload["practice_hours"] == "1"
    assert payload["hours_per_week"] == "1.5"
    assert payload["topics"][0]["hours"] == {
        "total": "1.5",
        "theory": "0.5",
        "practice": "1",
    }

    restored = ConfirmedStudyPlanDTO.from_dict(payload).to_model()
    assert restored == original
    assert restored.total_hours == Decimal("1.5")


def test_confirmed_plan_rejects_json_number_for_hours() -> None:
    payload = ConfirmedStudyPlanDTO.from_model(_fractional_plan()).to_dict()
    payload["total_hours"] = 1.5
    with pytest.raises(GenerationContractError, match="JSON-строкой"):
        ConfirmedStudyPlanDTO.from_dict(payload).to_model()


def test_confirmation_and_review_case_round_trip() -> None:
    confirmation = ManualSemanticConfirmation("r1", "s1", "Результат.", "Контроль.")
    case = SemanticReviewCase(
        review_id="r1",
        source_fingerprint="s1",
        week_number=2,
        topic_title="Тема",
        program_source="SOURCE",
        required_clauses=("A", "B"),
        proposed_result="Результат.",
        proposed_control="Контроль.",
        reasons=("Причина",),
    )

    assert ManualSemanticConfirmationDTO.from_dict(
        ManualSemanticConfirmationDTO.from_model(confirmation).to_dict()
    ).to_model() == confirmation
    assert SemanticReviewCaseDTO.from_dict(
        SemanticReviewCaseDTO.from_model(case).to_dict()
    ).to_model() == case
