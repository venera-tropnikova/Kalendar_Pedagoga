"""C1 safety harness: oracle, identity passthrough, flag OFF, no production hook."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from calendar_pedagoga.content_engine_v2 import (
    build_lesson_content_v2,
    derive_fields_v2,
)
from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.generator_revision import generator_paths
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.pipeline import _build_pipeline_lesson_content
from calendar_pedagoga.semantic_atom import (
    USE_SEMANTIC_ATOM_ENGINE,
    DiffKind,
    compare_identity,
    reset_shadow_invocation_count,
    run_passthrough_shadow,
    shadow_invocation_count,
    shadow_lexical_violations,
)
from calendar_pedagoga.semantic_atom.canonicalize import canonicalize_text
from calendar_pedagoga.semantic_atom.lexical import pedagogical_predicates
from calendar_pedagoga.semantic_atom.oracle import (
    BASELINE_COMMIT,
    EXPECTED_WEEK_COUNT,
    KNOWN_PYTEST_FAILURES,
    ORACLE_COVERAGE_MISSING,
    READY_STATUSES,
    REQUIRED_FIXTURES,
    available_runnable_ids,
    capture_baseline_oracle,
    compare_oracle_payloads,
    find_named,
    load_baseline_oracle,
    oracle_coverage_gaps,
)

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DIR = ROOT / "src" / "calendar_pedagoga"
UNSEEN_SOURCES = (
    "Квантовая запутанность нейтрино без наблюдения.",
    "Сигма-фокус гамма. XYZ протокол.",
    "Что такое флумбер? Абракадабра — вид зиггуната.",
    "Понятия: ритм, темп, динамика.",
    "Инструменты: духовые и ударные. Рисование деревьев.",
)


def _content_row(**kwargs) -> CalendarContentRow:
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
        program_content_full="Рисование деревьев.",
        program_content_preview="Рисование деревьев.",
        source_program_name="Программа",
        source_utp_name="utp.docx",
        warnings=(),
    )
    base.update(kwargs)
    return CalendarContentRow(**base)


def _production_modules() -> list[Path]:
    return sorted(path for path in PRODUCTION_DIR.glob("*.py") if path.is_file())


def test_flag_is_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_known_pytest_failures_are_pinned_not_silenced() -> None:
    assert KNOWN_PYTEST_FAILURES
    assert all(item.startswith("tests/") for item in KNOWN_PYTEST_FAILURES)


def test_production_sources_do_not_reference_shadow() -> None:
    forbidden = (
        "semantic_atom",
        "USE_SEMANTIC_ATOM_ENGINE",
        "run_passthrough_shadow",
        "run_shadow",
    )
    for path in _production_modules():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "semantic_atom" not in alias.name, path.name
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "semantic_atom" not in node.module, path.name
        for token in forbidden:
            assert token not in source, f"{path.name} contains {token}"


def test_generator_revision_excludes_shadow_package() -> None:
    assert all("semantic_atom" not in path.as_posix() for path in generator_paths(ROOT))


def test_production_does_not_invoke_shadow_when_flag_off() -> None:
    reset_shadow_invocation_count()
    rows = (_content_row(),)
    built = build_lesson_content_v2(rows)
    _build_pipeline_lesson_content(rows, use_content_engine_v2=True)
    assert built
    assert shadow_invocation_count() == 0


def test_monkeypatched_flag_on_still_skips_shadow(monkeypatch) -> None:
    monkeypatch.setattr(
        "calendar_pedagoga.semantic_atom.flags.USE_SEMANTIC_ATOM_ENGINE",
        True,
    )
    reset_shadow_invocation_count()
    _build_pipeline_lesson_content((_content_row(),), use_content_engine_v2=True)
    assert shadow_invocation_count() == 0


def test_canonicalize_quotes_and_spaces() -> None:
    assert canonicalize_text('  «лес», „поляна“  и  "луг" ') == '"лес", "поляна" и "луг"'


def test_passthrough_identity_on_unseen_wording() -> None:
    for source in UNSEEN_SOURCES:
        produced = derive_fields_v2(
            topic_title="Синтетика",
            theory_text=source,
            practice_text="",
            program_content=source,
            theory_hours=2,
            practice_hours=0,
        )
        shadow = run_passthrough_shadow(produced, source=source)
        diffs = compare_identity(produced, shadow)
        assert diffs[0].kind is DiffKind.EQUAL
        assert len(shadow.atoms) == len(produced.clause_coverage)


def test_atom_permutation_preserves_text_set() -> None:
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
    permuted = replace(
        produced,
        clause_coverage=tuple(reversed(produced.clause_coverage)),
    )
    permuted_shadow = run_passthrough_shadow(permuted, source=source)
    assert {atom.text for atom in shadow.atoms} == {
        atom.text for atom in permuted_shadow.atoms
    }
    assert compare_identity(produced, shadow)[0].kind is DiffKind.EQUAL


def test_lexical_checker_allows_source_morph_and_registry() -> None:
    source = "Рисование деревьев."
    violations = shadow_lexical_violations(
        source=source,
        result="Рисует деревья.",
        control="Просмотр рисунков.",
    )
    assert violations == ()


def test_lexical_checker_fails_invented_lexeme() -> None:
    violations = shadow_lexical_violations(
        source="Рисование деревьев.",
        result="Объясняет тайны квантовой запутанности.",
        control="Устный опрос по теме.",
    )
    assert "тайн" in " ".join(violations) or "тайны" in violations
    assert "квантовой" in violations or "квантовая" in violations or "запутанности" in violations


def test_lexical_checker_is_not_used_by_production_modules() -> None:
    needle = "shadow_lexical_violations"
    for path in _production_modules():
        assert needle not in path.read_text(encoding="utf-8")


def test_predicate_registry_matches_live_ce2() -> None:
    oracle = load_baseline_oracle()
    assert oracle["baseline_commit"] == BASELINE_COMMIT
    assert oracle["predicate_registry"] == sorted(pedagogical_predicates())


def test_oracle_manifest_covers_all_runnable_fixtures() -> None:
    oracle = load_baseline_oracle()
    gaps = oracle_coverage_gaps(oracle)
    assert gaps == [], f"{ORACLE_COVERAGE_MISSING}: {gaps}"
    pinned = {item["id"] for item in oracle["corpora"]}
    required = {item["id"] for item in REQUIRED_FIXTURES}
    assert required <= pinned, f"{ORACLE_COVERAGE_MISSING}: {sorted(required - pinned)}"
    discovered = set(available_runnable_ids())
    extra = discovered - pinned
    assert extra == set(), f"{ORACLE_COVERAGE_MISSING}: {sorted(extra)}"


def test_oracle_matches_live_production_and_docx() -> None:
    expected = load_baseline_oracle()
    actual = capture_baseline_oracle()
    mismatches = compare_oracle_payloads(expected, actual)
    assert mismatches == [], "\n".join(mismatches)


def test_oracle_ready_docs_have_36_cells_blocked_have_exact_error() -> None:
    oracle = load_baseline_oracle()
    for corpus in oracle["corpora"]:
        status = corpus["status"]
        rows = corpus["rows"]
        if status in READY_STATUSES:
            assert len(rows) == EXPECTED_WEEK_COUNT, corpus["id"]
            assert [row["week"] for row in rows] == list(range(1, EXPECTED_WEEK_COUNT + 1))
            for row in rows:
                assert "docx_lesson_type" in row
                assert "docx_planned_result" in row
                assert "docx_assessment" in row
        else:
            assert status == "BASELINE_ERROR", f"{ORACLE_COVERAGE_MISSING}: {corpus['id']}"
            assert corpus["error_type"]
            assert corpus["error"]
            expected_type = next(
                (
                    item.get("expected_error_type")
                    for item in REQUIRED_FIXTURES
                    if item["id"] == corpus["id"]
                ),
                None,
            )
            if expected_type:
                assert corpus["error_type"] == expected_type


def test_passthrough_identity_on_oracle_rows() -> None:
    from calendar_pedagoga.content_generation import build_content_model
    from calendar_pedagoga.parsing import parse_utp
    from calendar_pedagoga.program_parsing import parse_program
    from calendar_pedagoga.scheduling import build_schedule

    for spec in REQUIRED_FIXTURES:
        utp_path = find_named(spec["utp"])
        program_path = find_named(spec["program"])
        assert utp_path is not None and program_path is not None, (
            f"{ORACLE_COVERAGE_MISSING}: {spec['id']}"
        )
        utp = parse_utp(utp_path)
        program = parse_program(
            program_path.read_bytes(),
            program_path.name,
            study_year=spec["study_year"],
        )
        content = build_content_model(
            build_schedule(utp, "2026–2027"),
            utp,
            program,
            utp_path.name,
        )
        for row in build_lesson_content_v2(content):
            source = " ".join(
                part
                for part in (
                    row.theory_text,
                    row.practice_text,
                    row.source.program_content_full,
                )
                if part
            )
            shadow = run_passthrough_shadow(row, source=source)
            assert compare_identity(row, shadow)[0].kind is DiffKind.EQUAL
            assert len(shadow.atoms) == len(row.clause_coverage)
