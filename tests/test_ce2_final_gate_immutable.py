# -*- coding: utf-8 -*-
"""FINAL semantic gate is last: fold/CONTROL before it; no post-gate mutation."""

from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from zipfile import ZipFile

import pytest

from calendar_pedagoga.content_engine_v2 import (
    ActionFrame,
    ContentEngineV2Result,
    _apply_result_grammar_gate,
    _control_covers_all_result_items,
    _finalize_content_fields,
    _fold_week_result,
    derive_fields_v2,
    format_unresolved_review_block_message,
    unresolved_mandatory_review_blocks,
    week_has_unresolved_mandatory_review,
)
from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.lesson_content import LessonContentRow
from calendar_pedagoga.lesson_resolution import resolve_lesson_content
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.pipeline import PipelineError, _build_pipeline_lesson_content
from calendar_pedagoga.docx_generation import _inject_generator_provenance
from docx import Document


def _row(**kwargs) -> CalendarContentRow:
    base = dict(
        week_number=1,
        date_range="01–07.09",
        month="Сентябрь",
        section="Раздел",
        topic_number="1.1",
        topic_title="Тема",
        source_topic_title="Тема",
        theory_hours=0,
        practice_hours=2,
        total_hours=2,
        match_status=MatchStatus.EXACT,
        program_section="Раздел",
        program_topic="Тема",
        program_content_full="",
        program_content_preview="",
        source_program_name="Программа",
        source_utp_name="utp.docx",
        warnings=(),
    )
    base.update(kwargs)
    return CalendarContentRow(**base)


def test_unknown_multi_clause_practice_needs_review_and_blocks_ready_docx() -> None:
    practice = (
        "Квантовая запутанность нейтрино без наблюдения. "
        "Сигма-фокус гамма. "
        "XYZ протокол."
    )
    derived = derive_fields_v2(
        topic_title="Неизвестная практика",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
    )
    assert any(status == "NEEDS_REVIEW" for _, status in derived.clause_coverage)
    content = (
        _row(
            program_content_full="Практика.\n" + practice,
            program_content_preview=practice,
            topic_title="Неизвестная практика",
            source_topic_title="Неизвестная практика",
            program_topic="Неизвестная практика",
        ),
    )
    from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2

    v2 = build_lesson_content_v2(content)
    blocks = unresolved_mandatory_review_blocks(v2)
    assert blocks
    assert week_has_unresolved_mandatory_review(v2[0])
    message = format_unresolved_review_block_message(blocks)
    assert "незакрытые" in message
    with pytest.raises(PipelineError, match="не готов"):
        _build_pipeline_lesson_content(content, use_content_engine_v2=True)


def test_pipeline_blocks_synthetic_unresolved_week(monkeypatch) -> None:
    practice = "Квантовая запутанность нейтрино без наблюдения."
    content = (
        _row(
            program_content_full="Практика.\n" + practice,
            program_content_preview=practice,
            topic_title="Неизвестное обязательное",
            source_topic_title="Неизвестное обязательное",
            program_topic="Неизвестное обязательное",
        ),
    )
    with pytest.raises(PipelineError, match="не готов"):
        _build_pipeline_lesson_content(content, use_content_engine_v2=True)


def test_fold_does_not_change_meaning_after_final_gate() -> None:
    theory = "Строение клетки. Функции клеточной мембраны."
    prepared = ContentEngineV2Result(
        frame=ActionFrame(theory, "характеризует", "строение клетки", ""),
        lesson_type="теоретическое занятие",
        planned_result=(
            "Характеризует строение клетки. Характеризует функции клеточной мембраны."
        ),
        assessment_method=(
            "устный опрос по строению клетки; устный опрос по функциям клеточной мембраны"
        ),
        theory_text=theory,
        practice_text="",
        clause_coverage=(
            ("Строение клетки", "COVERED"),
            ("Функции клеточной мембраны", "COVERED"),
        ),
    )
    final = _finalize_content_fields(
        prepared,
        topic_title="Биология клетки",
        theory_text=theory,
        practice_text="",
        program_content=theory,
        theory_hours=2,
        practice_hours=0,
    )
    # Fold may join predicates, but both meanings stay present and COVERED.
    low = final.planned_result.casefold()
    assert "клетк" in low
    assert "мембран" in low
    statuses = dict(final.clause_coverage)
    assert statuses.get("Строение клетки") == "COVERED"
    assert statuses.get("Функции клеточной мембраны") == "COVERED"
    # No further fold is allowed to demote coverage after FINAL gate.
    refolded = _fold_week_result(final.planned_result)
    assert "клетк" in refolded.casefold() and "мембран" in refolded.casefold()


def test_knowledge_fold_keeps_every_source_clause_covered() -> None:
    theory = "История прибора. Назначение прибора. Устройство прибора."
    prepared = ContentEngineV2Result(
        frame=ActionFrame(theory, "характеризует", "история прибора", ""),
        lesson_type="теоретическое занятие",
        planned_result=(
            "Характеризует историю прибора. "
            "Характеризует назначение прибора. "
            "Характеризует устройство прибора."
        ),
        assessment_method=(
            "устный опрос по истории прибора; "
            "устный опрос по назначению прибора; "
            "устный опрос по устройству прибора"
        ),
        theory_text=theory,
        practice_text="",
        clause_coverage=(
            ("История прибора", "COVERED"),
            ("Назначение прибора", "COVERED"),
            ("Устройство прибора", "COVERED"),
        ),
    )

    final = _finalize_content_fields(
        prepared,
        topic_title="Прибор",
        theory_text=theory,
        practice_text="",
        program_content=theory,
        theory_hours=2,
        practice_hours=0,
    )

    assert final.planned_result == (
        "Характеризует историю прибора, назначение прибора и устройство прибора."
    )
    assert final.assessment_method == (
        "устный опрос: историю прибора, назначение прибора и устройство прибора"
    )
    assert all(status == "COVERED" for _, status in final.clause_coverage)


def test_grammar_rejection_after_fold_demotes_clause() -> None:
    theory = "Строение клетки. Функции мембраны."
    candidate = ContentEngineV2Result(
        frame=ActionFrame("Строение клетки", "характеризует", "строение клетки", ""),
        lesson_type="теоретическое занятие",
        planned_result="Характеризует строение клетки. Характеризует функции мембраны.",
        assessment_method=(
            "устный опрос по строению клетки; устный опрос по функциям мембраны"
        ),
        theory_text=theory,
        practice_text="",
        clause_coverage=(
            ("Строение клетки", "COVERED"),
            ("Функции мембраны", "COVERED"),
        ),
    )
    folded = replace(
        candidate,
        planned_result=_fold_week_result(candidate.planned_result),
    )
    # Simulate a fold that left an unproven grammar sentence, then FINAL grammar.
    broken = replace(
        folded,
        planned_result="Характеризует строение клетки. Характеризует функции.",
    )
    after_grammar = _apply_result_grammar_gate(
        broken,
        topic_title="Биология клетки",
        theory_text=theory,
        practice_text="",
        program_content=theory,
        theory_hours=2,
        practice_hours=0,
    )
    final = _finalize_content_fields(
        after_grammar,
        topic_title="Биология клетки",
        theory_text=theory,
        practice_text="",
        program_content=theory,
        theory_hours=2,
        practice_hours=0,
    )
    statuses = dict(final.clause_coverage)
    assert statuses.get("Строение клетки") in {"COVERED", "NEEDS_REVIEW"}
    assert statuses.get("Функции мембраны") == "NEEDS_REVIEW" or not final.planned_result


def test_control_covers_entire_final_result() -> None:
    derived = derive_fields_v2(
        topic_title="Лабораторная работа",
        theory_text="",
        practice_text="Выполнение разминки. Измерение пульса.",
        program_content="Выполнение разминки. Измерение пульса.",
        theory_hours=0,
        practice_hours=2,
    )
    assert derived.planned_result.strip()
    assert _control_covers_all_result_items(
        derived.planned_result, derived.assessment_method
    )


def test_post_gate_mutation_is_rejected() -> None:
    lesson = LessonContentRow(
        source=_row(),
        theory_text="",
        practice_text="Выполнение разминки.",
        lesson_type="практикум",
        planned_result="Выполняет разминку.",
        assessment_method="педагогическое наблюдение за выполнением разминки",
        warnings=(),
    )
    # Frozen path must keep TYPE/RESULT/CONTROL identical.
    resolved = resolve_lesson_content((lesson,), None, freeze_pedagogical_fields=True)
    assert resolved[0].lesson_type == lesson.lesson_type
    assert resolved[0].planned_result == lesson.planned_result
    assert resolved[0].assessment_method == lesson.assessment_method


def test_docx_contains_generator_provenance_custom_properties() -> None:
    doc = Document()
    doc.add_paragraph("visible body")
    buffer = BytesIO()
    doc.save(buffer)
    content = _inject_generator_provenance(buffer.getvalue())
    with ZipFile(BytesIO(content)) as archive:
        custom = archive.read("docProps/custom.xml").decode("utf-8")
    assert "GeneratorGitCommit" in custom
    assert "GeneratorRevision" in custom
    assert "GeneratedAt" in custom
    # Must not rely on Word core revision counter.
    core = ZipFile(BytesIO(content)).read("docProps/core.xml").decode("utf-8")
    assert "GeneratorGitCommit" not in core
