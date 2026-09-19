"""Approved CONTROL template lexemes stay bound to a proven template id."""

from __future__ import annotations

from calendar_pedagoga.semantic_atom.lexical import (
    APPROVED_CONTROL_TEMPLATES,
    ApprovedControlTemplate,
    TEMPLATE_FUNCTION_WORDS,
    lookup_approved_control_template,
    shadow_lexical_violations,
)

READY_WORK = APPROVED_CONTROL_TEMPLATES["product_review.ready_work"]
DRAWINGS = APPROVED_CONTROL_TEMPLATES["product_review.drawings"]


def _ready_work(**overrides) -> ApprovedControlTemplate:
    payload = {
        "template_id": "product_review.ready_work",
        "text": READY_WORK,
        "provenance": "control_c9",
        "bound": True,
        "proven": True,
    }
    payload.update(overrides)
    return ApprovedControlTemplate(**payload)


def test_exact_approved_template_passes() -> None:
    violations = shadow_lexical_violations(
        source="Изготовление сувениров, масок, открыток.",
        result="Изготавливает сувениры, маски, открытки.",
        control="Просмотр и оценка готовой работы",
        approved_templates=(_ready_work(),),
    )
    assert violations == ()
    assert lookup_approved_control_template("Просмотр и оценка готовой работы.") == (
        "product_review.ready_work"
    )


def test_modified_template_text_is_blocked() -> None:
    violations = shadow_lexical_violations(
        source="Изготовление сувениров.",
        result="Изготавливает сувениры.",
        control="Просмотр и оценка готовой работы ученика",
        approved_templates=(_ready_work(),),
    )
    assert "готовой" in violations or "работы" in violations or "ученика" in violations


def test_wrong_template_id_is_blocked() -> None:
    violations = shadow_lexical_violations(
        source="Изготовление сувениров.",
        result="Изготавливает сувениры.",
        control="Просмотр и оценка готовой работы",
        approved_templates=(_ready_work(template_id="product_review.drawings"),),
    )
    assert "готовой" in violations
    assert "работы" in violations


def test_ready_work_words_outside_template_are_blocked() -> None:
    bare = shadow_lexical_violations(
        source="Изготовление сувениров.",
        result="Изготавливает сувениры.",
        control="готовой работы",
        approved_templates=(_ready_work(),),
    )
    assert "готовой" in bare
    assert "работы" in bare
    in_result = shadow_lexical_violations(
        source="Изготовление сувениров.",
        result="Оценка готовой работы.",
        control=DRAWINGS,
        approved_templates=(
            ApprovedControlTemplate(
                template_id="product_review.drawings",
                text=DRAWINGS,
                provenance="control_c9",
                bound=True,
                proven=True,
            ),
        ),
    )
    assert "готовой" in in_result
    assert "работы" in in_result


def test_binding_and_provenance_are_required() -> None:
    unbound = shadow_lexical_violations(
        source="Изготовление сувениров.",
        result="Изготавливает сувениры.",
        control="Просмотр и оценка готовой работы",
        approved_templates=(_ready_work(bound=False),),
    )
    missing_provenance = shadow_lexical_violations(
        source="Изготовление сувениров.",
        result="Изготавливает сувениры.",
        control="Просмотр и оценка готовой работы",
        approved_templates=(_ready_work(provenance=""),),
    )
    unproven = shadow_lexical_violations(
        source="Изготовление сувениров.",
        result="Изготавливает сувениры.",
        control="Просмотр и оценка готовой работы",
        approved_templates=(_ready_work(proven=False),),
    )
    for violations in (unbound, missing_provenance, unproven):
        assert "готовой" in violations
        assert "работы" in violations


def test_ready_work_words_are_not_global_allow_list() -> None:
    assert "готовой" not in TEMPLATE_FUNCTION_WORDS
    assert "работы" not in TEMPLATE_FUNCTION_WORDS
    violations = shadow_lexical_violations(
        source="Изготовление сувениров.",
        result="Изготавливает сувениры.",
        control="Просмотр и оценка готовой работы",
    )
    assert "готовой" in violations
    assert "работы" in violations
