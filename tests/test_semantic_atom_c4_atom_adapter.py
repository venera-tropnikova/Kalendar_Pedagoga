"""C4: lossless shadow AtomAdapter. No production writers or kind/predicate."""

from __future__ import annotations

import ast
import hashlib
import json
from itertools import permutations
from pathlib import Path

import pytest

from calendar_pedagoga.content_engine_v2 import derive_fields_v2
from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.pipeline import _build_pipeline_lesson_content
from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.adapter import project_passthrough_graph
from calendar_pedagoga.semantic_atom.atom_adapter import (
    atom_adapter_calls,
    atomize,
    identity_fields,
    reconstruct_source,
    reset_atom_adapter_calls,
)
from calendar_pedagoga.semantic_atom.models import (
    AtomizationResult,
    ObjectStatus,
    fingerprint_source,
    to_jsonable,
)
from calendar_pedagoga.semantic_atom.passthrough import (
    DiffKind,
    compare_identity,
    reset_shadow_invocation_count,
    run_passthrough_shadow,
    shadow_invocation_count,
)

ROOT = Path(__file__).resolve().parents[1]
C1_ORACLE = ROOT / "tests" / "oracles" / "semantic_atom_7a714d6.json"
C1_ORACLE_SHA256 = "432ff7b694dc2e235c5446769f4779c4b7f9498b9ebda3a57a931c9c7e059344"
PRODUCTION_DIR = ROOT / "src" / "calendar_pedagoga"
ADAPTER_PATH = PRODUCTION_DIR / "semantic_atom" / "atom_adapter.py"
MODELS_PATH = PRODUCTION_DIR / "semantic_atom" / "models.py"

PROPERTY_SOURCES = (
    "Рисование деревьев.",
    "Что такое компас?",
    "Рисование деревьев. Что такое компас?",
    "Понятия: ритм, темп, динамика.",
    "Инструменты: духовые и ударные.",
    "Курай — символ башкирского народа.",
    "Курай – народный инструмент.",
    "Знаки «Берегите природу».",
    "Рисует (детей) животных.",
    "Занятие 01.09.2026 длится 2,5 часа.",
    "Экскурсия в г. Уфа по ул. Ленина.",
    "Стихи А. С. Пушкина.",
    "Выполняет упражнение; объясняет правило.",
    "День защиты детей в рисунках.",
    "Что значит «стоп»? Рисование знаков.",
    "Знаки: «Берегите природу», «Не сори».",
    "1.09–7.09. Рисование осени.",
    "   пробелы вокруг   ",
    "Одно предложение без точки",
)


def test_c1_oracle_file_is_unchanged() -> None:
    assert hashlib.sha256(C1_ORACLE.read_bytes()).hexdigest() == C1_ORACLE_SHA256


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_atom_adapter_avoids_production_imports() -> None:
    allowed = {
        "calendar_pedagoga.semantic_atom.models",
        "calendar_pedagoga.semantic_atom.atom_adapter",
    }
    for path in (ADAPTER_PATH, MODELS_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith("calendar_pedagoga") and name not in allowed:
                    if path == MODELS_PATH and name == (
                        "calendar_pedagoga.semantic_atom.canonicalize"
                    ):
                        continue
                    raise AssertionError(f"{path.name} imported {name}")


def test_production_does_not_import_atom_adapter() -> None:
    needles = (
        "atomize",
        "AtomizationResult",
        "SourceDelimiter",
        "atom_adapter",
        "identity_fields",
    )
    for path in PRODUCTION_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{path.name} contains {needle}"


def test_production_does_not_call_atom_adapter() -> None:
    reset_atom_adapter_calls()
    reset_shadow_invocation_count()
    row = CalendarContentRow(
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
        program_content_full="Рисование деревьев.",
        program_content_preview="Рисование деревьев.",
        source_program_name="Программа",
        source_utp_name="utp.docx",
        warnings=(),
    )
    _build_pipeline_lesson_content((row,), use_content_engine_v2=True)
    assert atom_adapter_calls() == 0
    assert shadow_invocation_count() == 0


def _assert_lossless(result: AtomizationResult, source: str) -> None:
    assert result.source == source
    assert reconstruct_source(result) == source
    covered = [False] * len(source)
    for piece in (*result.atoms, *result.delimiters):
        assert 0 <= piece.span.start <= piece.span.end <= len(source)
        assert piece.text == source[piece.span.start : piece.span.end]
        assert piece.span.start == piece.span.end or piece.text
        for index in range(piece.span.start, piece.span.end):
            assert not covered[index], "spans overlap"
            covered[index] = True
    assert all(covered), "source characters were lost"
    starts = [atom.span.start for atom in result.atoms]
    assert starts == sorted(starts)


def _assert_unassessed(result: AtomizationResult) -> None:
    assert result.status in {ObjectStatus.UNASSESSED, ObjectStatus.UNRESOLVED}
    for atom in result.atoms:
        assert atom.status is ObjectStatus.UNASSESSED
        assert atom.transitional is True
        assert atom.status is not ObjectStatus.PROVEN
        assert atom.status is not ObjectStatus.COVERED
        assert not hasattr(atom, "kind")
        assert not hasattr(atom, "predicate")


@pytest.mark.parametrize("source", PROPERTY_SOURCES)
def test_round_trip_source(source: str) -> None:
    result = atomize(source)
    _assert_lossless(result, source)
    _assert_unassessed(result)


@pytest.mark.parametrize("source", PROPERTY_SOURCES)
def test_ids_and_fingerprints_are_stable(source: str) -> None:
    left = atomize(source)
    right = atomize(source)
    assert [atom.id for atom in left.atoms] == [atom.id for atom in right.atoms]
    assert [atom.source_fingerprint for atom in left.atoms] == [
        atom.source_fingerprint for atom in right.atoms
    ]
    assert [item.id for item in left.delimiters] == [item.id for item in right.delimiters]


def test_independent_atoms_survive_permutation() -> None:
    parts = (
        "Рисование деревьев.",
        "Что такое компас?",
        "Объясняет правило.",
    )
    texts_by_order: list[frozenset[str]] = []
    for order in permutations(parts):
        source = " ".join(order)
        result = atomize(source)
        _assert_lossless(result, source)
        assert len(result.atoms) == 3
        texts_by_order.append(frozenset(atom.text for atom in result.atoms))
    assert len(set(texts_by_order)) == 1
    content_prints = {
        frozenset(fingerprint_source(atom.text) for atom in atomize(" ".join(order)).atoms)
        for order in permutations(parts)
    }
    assert len(content_prints) == 1


def test_quotes_brackets_dates_colons_dashes_questions() -> None:
    quoted = atomize('Знаки «Берегите природу».')
    assert len(quoted.atoms) == 1
    assert "«Берегите природу»" in quoted.atoms[0].text

    brackets = atomize("Рисует (детей и зверей) на листе.")
    assert len(brackets.atoms) == 1
    assert "(детей и зверей)" in brackets.atoms[0].text

    dated = atomize("Занятие 01.09.2026 длится 2,5 часа в г. Уфа.")
    assert len(dated.atoms) == 1
    assert "01.09.2026" in dated.atoms[0].text
    assert "2,5" in dated.atoms[0].text
    assert "г. Уфа" in dated.atoms[0].text

    colon = atomize("Понятия: ритм, темп, динамика.")
    assert len(colon.atoms) == 1

    dash = atomize("Курай — символ башкирского народа.")
    assert len(dash.atoms) == 1

    question = atomize("Что такое компас?")
    assert len(question.atoms) == 1
    assert question.atoms[0].text.endswith("?")


def test_one_phrase_with_several_independent_parts() -> None:
    source = "Рисование деревьев. Что такое компас?"
    result = atomize(source)
    _assert_lossless(result, source)
    assert len(result.atoms) == 2
    assert result.atoms[0].text == "Рисование деревьев."
    assert result.atoms[1].text == "Что такое компас?"


def test_empty_and_broken_input_are_explicit() -> None:
    empty = atomize("")
    none = atomize(None)
    invalid = atomize(123)
    whitespace = atomize(" \n\t ")
    unbalanced = atomize("Знаки «Берегите природу")
    for result, source in (
        (empty, ""),
        (none, ""),
        (invalid, ""),
        (whitespace, " \n\t "),
        (unbalanced, "Знаки «Берегите природу"),
    ):
        assert isinstance(result, AtomizationResult)
        if source:
            _assert_lossless(result, source)
        assert result.status in {ObjectStatus.UNASSESSED, ObjectStatus.UNRESOLVED}
        assert result.status is not ObjectStatus.PROVEN
    assert empty.note == "empty"
    assert none.note == "empty"
    assert invalid.note == "invalid_source"
    assert whitespace.status is ObjectStatus.UNRESOLVED
    assert unbalanced.note == "unbalanced_protected_region"
    assert len(unbalanced.atoms) == 1


def test_generated_sources_stay_lossless() -> None:
    heads = ("Рисование", "Понятия", "Курай", "Знаки")
    tails = ("деревьев", "ритма", "символа", "природы")
    seps = (". ", "! ", "? ", "; ", ", ", ": ", " — ", "\n")
    extras = (
        "«кавычки»",
        "(скобки)",
        "01.09.2026",
        "2,5",
        "г. Уфа",
        "А. С. Иванов",
    )
    sources = [f"{head}{sep}{tail}." for head, sep, tail in zip(heads, seps, tails)]
    sources.extend(extras)
    sources.extend(f"{left}. {right}" for left, right in zip(heads, extras))
    for source in sources:
        result = atomize(source)
        _assert_lossless(result, source)
        _assert_unassessed(result)


def test_new_atoms_are_not_covered_by_old_result() -> None:
    source = "Рисование деревьев. Что такое компас?"
    produced = derive_fields_v2(
        topic_title="Смесь",
        theory_text="Что такое компас?",
        practice_text="Рисование деревьев.",
        program_content=source,
        theory_hours=1,
        practice_hours=1,
    )
    shadow = run_passthrough_shadow(produced, source=source)
    report = project_passthrough_graph(produced, source=source)
    result = atomize(source)
    lesson_type, planned_result, assessment = identity_fields(produced)
    assert compare_identity(produced, shadow)[0].kind is DiffKind.EQUAL
    assert (lesson_type, planned_result, assessment) == (
        produced.lesson_type,
        produced.planned_result,
        produced.assessment_method,
    )
    assert report.projected_type == produced.lesson_type
    assert report.projected_result == produced.planned_result
    assert report.projected_control == produced.assessment_method
    assert len(result.atoms) == 2
    assert all(atom.status is ObjectStatus.UNASSESSED for atom in result.atoms)
    assert not any(atom.status is ObjectStatus.COVERED for atom in result.atoms)
    assert not any(atom.status is ObjectStatus.PROVEN for atom in result.atoms)


def test_atomization_roundtrip_json() -> None:
    source = "Рисование деревьев. Что такое компас?"
    result = atomize(source)
    payload = to_jsonable(result)
    restored = AtomizationResult.from_dict(json.loads(json.dumps(payload)))
    assert to_jsonable(restored) == payload
    assert reconstruct_source(restored) == source
