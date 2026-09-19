"""Capture and compare the immutable 7a714d6 production oracle.

Stores only generated TYPE/RESULT/CONTROL and hashes. Does not persist
uploaded user documents or personal fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from typing import Any

from docx import Document
from docx.table import _Cell

from calendar_pedagoga.confirmed_study_plan import confirmed_plan_from_external_utp
from calendar_pedagoga.content_engine_v2 import (
    LessonContentV2Row,
    build_lesson_content_v2,
    unresolved_mandatory_review_blocks,
)
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.docx_generation import _columns_for_table
from calendar_pedagoga.organization_template import select_calendar_template
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.pipeline import run_calendar_pipeline
from calendar_pedagoga.program_parsing import infer_study_year_number, parse_program
from calendar_pedagoga.scheduling import build_schedule
from calendar_pedagoga.semantic_atom.canonicalize import canonicalize_text, text_hash
from calendar_pedagoga.semantic_atom.lexical import (
    pedagogical_predicates,
    shadow_lexical_violations,
)
from calendar_pedagoga.semantic_atom.passthrough import run_passthrough_shadow

BASELINE_COMMIT = "7a714d663c8f2b6578808d356d6bdeaafcb7f0d5"
ACADEMIC_YEAR = "2026–2027"
ORACLE_COVERAGE_MISSING = "oracle coverage missing"
READY_STATUSES = frozenset({"DRAFT_READY", "FINAL_READY"})
EXPECTED_WEEK_COUNT = 36
KNOWN_PYTEST_FAILURES = (
    "tests/test_ce2_control_np_dative.py::test_ordinary_noun_control_object_stays_correct",
    "tests/test_ce2_control_np_dative.py::test_mixed_week_keeps_finite_control_and_inflects_knowledge_np",
    "tests/test_result_grammar_gate.py::test_review_keeps_source_control_type_and_safe_independent_action",
    "tests/test_result_grammar_gate.py::test_catalogue_heading_is_not_a_covered_citation",
    "tests/test_result_grammar_gate.py::test_instrumental_government_is_not_an_admissible_knowledge_object",
    "tests/test_result_grammar_gate.py::test_practice_process_actions_become_finite_result",
    "tests/test_result_grammar_gate.py::test_practice_process_list_keeps_control_from_same_result",
    "tests/test_result_grammar_gate.py::test_colon_catalogue_after_process_is_not_performed_activity",
)

REQUIRED_FIXTURES = (
    {
        "id": "key_y1",
        "utp": "УТП КЛЮЧ 1 г. 2ч.docx",
        "program": "Программа КЛЮЧ.DOC",
        "study_year": 1,
    },
    {
        "id": "key_y2",
        "utp": "УТП КЛЮЧ 2 г. 2ч.docx",
        "program": "Программа КЛЮЧ.DOC",
        "study_year": 2,
    },
    {
        "id": "climb_y1",
        "utp": "УТП Скалолазание.docx",
        "program": "Программа Скалолазание.docx",
        "study_year": 1,
    },
    {
        "id": "tour_y3",
        "utp": "УТП ТП 3г. 2ч.docx",
        "program": "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx",
        "study_year": 3,
        "expected_error_type": "ConfirmedStudyPlanError",
    },
)

_FROZEN_REFERENCES = Path(r"D:\Kalendar_Pedagoga\references")
_YEAR_RE = re.compile(r"(\d+)\s*г", re.IGNORECASE)


@dataclass(frozen=True)
class _DocxWeek:
    week_number: int
    lesson_type: str
    planned_result: str
    assessment: str


def project_root(root: Path | None = None) -> Path:
    if root is None:
        return Path(__file__).resolve().parents[3]
    return Path(root)


def references_dir(root: Path | None = None) -> Path:
    return project_root(root) / "references"


def fixture_roots(root: Path | None = None) -> tuple[Path, ...]:
    roots = [references_dir(root)]
    if _FROZEN_REFERENCES.is_dir() and _FROZEN_REFERENCES.resolve() != roots[0].resolve():
        roots.append(_FROZEN_REFERENCES)
    return tuple(roots)


def default_oracle_path(root: Path | None = None) -> Path:
    return project_root(root) / "tests" / "oracles" / "semantic_atom_7a714d6.json"


def find_named(name: str, root: Path | None = None) -> Path | None:
    for folder in fixture_roots(root):
        path = folder / name
        if path.is_file():
            return path
    return None


def _cell_text(row, index: int) -> str:
    return _Cell(row._tr.tc_lst[index], row).text


def _join_column(rows, index: int) -> str:
    return "".join(_cell_text(row, index) for row in rows)


def extract_docx_weeks(content: bytes) -> tuple[_DocxWeek, ...]:
    table = Document(BytesIO(content)).tables[0]
    columns = _columns_for_table(table)
    groups: list[list] = []
    for row in table.rows[2:]:
        week_cell = _cell_text(row, columns.week)
        if not week_cell.strip():
            continue
        week_number = int(week_cell.splitlines()[0])
        if (
            not groups
            or int(_cell_text(groups[-1][0], columns.week).splitlines()[0])
            != week_number
        ):
            groups.append([row])
        else:
            groups[-1].append(row)
    extracted: list[_DocxWeek] = []
    for rows in groups:
        extracted.append(
            _DocxWeek(
                week_number=int(_cell_text(rows[0], columns.week).splitlines()[0]),
                lesson_type=_join_column(rows, columns.lesson_type),
                planned_result=_join_column(rows, columns.planned_result),
                assessment=_join_column(rows, columns.assessment),
            )
        )
    return tuple(extracted)


def _source_text(row: LessonContentV2Row) -> str:
    return " ".join(
        part
        for part in (
            row.theory_text,
            row.practice_text,
            row.source.program_content_full,
        )
        if part
    )


def _row_record(v2: LessonContentV2Row, docx: _DocxWeek | None) -> dict[str, Any]:
    source = _source_text(v2)
    shadow = run_passthrough_shadow(v2, source=source)
    violations = shadow_lexical_violations(
        source=source,
        result=shadow.planned_result,
        control=shadow.assessment_method,
    )
    record = {
        "week": v2.source.week_number,
        "match_status": str(v2.source.match_status),
        "lesson_type": canonicalize_text(v2.lesson_type),
        "planned_result": canonicalize_text(v2.planned_result),
        "assessment_method": canonicalize_text(v2.assessment_method),
        "clause_coverage": [
            [canonicalize_text(clause), canonicalize_text(status)]
            for clause, status in v2.clause_coverage
        ],
        "type_hash": text_hash(v2.lesson_type),
        "result_hash": text_hash(v2.planned_result),
        "control_hash": text_hash(v2.assessment_method),
        "lexical_violations": list(violations),
    }
    if docx is not None:
        record.update(
            {
                "docx_lesson_type": canonicalize_text(docx.lesson_type),
                "docx_planned_result": canonicalize_text(docx.planned_result),
                "docx_assessment": canonicalize_text(docx.assessment),
                "docx_type_hash": text_hash(docx.lesson_type),
                "docx_result_hash": text_hash(docx.planned_result),
                "docx_control_hash": text_hash(docx.assessment),
            }
        )
    return record


def _family(name: str) -> str | None:
    folded = name.casefold()
    if "календарн" in folded:
        return None
    if "ключ" in folded:
        return "key"
    if "скалолаз" in folded:
        return "climb"
    if "турист" in folded or "утп тп" in folded or folded.startswith("утп тп"):
        return "tour"
    return None


def _year_from_name(name: str) -> int | None:
    match = _YEAR_RE.search(name)
    return int(match.group(1)) if match else None


def _iter_docs(root: Path | None = None) -> list[Path]:
    seen: set[str] = set()
    found: list[Path] = []
    for folder in fixture_roots(root):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.suffix.lower() not in {".doc", ".docx"}:
                continue
            key = path.name.casefold()
            if key in seen or "календарн" in key:
                continue
            seen.add(key)
            found.append(path)
    return found


def _spec_from_paths(utp: Path, program: Path, study_year: int) -> dict[str, Any]:
    family = _family(utp.name) or _family(program.name) or "extra"
    return {
        "id": f"{family}_y{study_year}",
        "utp": utp.name,
        "program": program.name,
        "study_year": study_year,
    }


def _probe_runnable(spec: dict[str, Any], root: Path | None = None) -> bool:
    utp_path = find_named(spec["utp"], root)
    program_path = find_named(spec["program"], root)
    if utp_path is None or program_path is None:
        return False
    try:
        utp = parse_utp(utp_path)
        year = spec["study_year"] or infer_study_year_number(utp.metadata.study_year)
        if year is None:
            return False
        program = parse_program(
            program_path.read_bytes(),
            program_path.name,
            study_year=year,
        )
        if not program.content_items:
            return False
        schedule = build_schedule(utp, ACADEMIC_YEAR)
        content = build_content_model(schedule, utp, program, utp_path.name)
        return bool(build_lesson_content_v2(content))
    except Exception:
        return False


def discover_runnable_specs(root: Path | None = None) -> tuple[dict[str, Any], ...]:
    """All available runnable program/year pairs. Missing required files stay listed."""

    specs = [dict(item) for item in REQUIRED_FIXTURES]
    known_files = {
        (item["utp"].casefold(), item["program"].casefold(), item["study_year"])
        for item in specs
    }
    known_years = {
        ((_family(item["utp"]) or _family(item["program"])), item["study_year"])
        for item in specs
    }
    docs = _iter_docs(root)
    programs = [
        path
        for path in docs
        if not path.name.casefold().startswith("утп")
    ]
    utps = [path for path in docs if path.name.casefold().startswith("утп")]
    extras: list[dict[str, Any]] = []
    programs_by_family: dict[str, list[Path]] = {}
    for program in programs:
        family = _family(program.name)
        if family is None:
            continue
        programs_by_family.setdefault(family, []).append(program)
    for utp in utps:
        family = _family(utp.name)
        if family is None:
            continue
        year = _year_from_name(utp.name) or infer_study_year_number(utp.name)
        if year is None:
            continue
        for program in programs_by_family.get(family, ()):
            key = (utp.name.casefold(), program.name.casefold(), year)
            if key in known_files or (family, year) in known_years:
                continue
            spec = _spec_from_paths(utp, program, year)
            if spec["id"] in {item["id"] for item in specs + extras}:
                continue
            if _probe_runnable(spec, root):
                extras.append(spec)
                known_files.add(key)
                known_years.add((family, year))
    extras.sort(key=lambda item: item["id"])
    return tuple(specs + extras)


def available_runnable_ids(root: Path | None = None) -> tuple[str, ...]:
    ids: list[str] = []
    for spec in discover_runnable_specs(root):
        if spec["id"] in {item["id"] for item in REQUIRED_FIXTURES}:
            if find_named(spec["utp"], root) and find_named(spec["program"], root):
                ids.append(spec["id"])
            continue
        ids.append(spec["id"])
    return tuple(sorted(set(ids)))


def oracle_coverage_gaps(
    oracle: dict[str, Any] | None = None,
    root: Path | None = None,
) -> list[str]:
    payload = oracle if oracle is not None else load_baseline_oracle(root=root)
    pinned = {item["id"] for item in payload.get("corpora", [])}
    missing: list[str] = []
    for spec in REQUIRED_FIXTURES:
        if not find_named(spec["utp"], root) or not find_named(spec["program"], root):
            missing.append(spec["id"])
        elif spec["id"] not in pinned:
            missing.append(spec["id"])
    for fixture_id in available_runnable_ids(root):
        if fixture_id not in pinned and fixture_id not in missing:
            missing.append(fixture_id)
    return sorted(missing)


def _load_sources(spec: dict[str, Any], root: Path | None = None):
    utp_path = find_named(spec["utp"], root)
    program_path = find_named(spec["program"], root)
    if utp_path is None or program_path is None:
        raise FileNotFoundError(f"{ORACLE_COVERAGE_MISSING}: {spec['id']}")
    utp = parse_utp(utp_path)
    year = spec["study_year"]
    if year is None:
        year = infer_study_year_number(utp.metadata.study_year)
    program = parse_program(
        program_path.read_bytes(),
        program_path.name,
        study_year=year,
    )
    return utp, program, utp_path, program_path, year


def _capture_corpus(spec: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": spec["id"],
        "program": spec["program"],
        "utp": spec["utp"],
        "study_year": spec["study_year"],
        "rows": [],
    }
    try:
        utp, program, utp_path, program_path, year = _load_sources(spec, root)
    except FileNotFoundError as error:
        payload.update(
            {
                "status": "MISSING_FIXTURE",
                "error_type": type(error).__name__,
                "error": canonicalize_text(str(error)),
            }
        )
        return payload
    except Exception as error:
        payload.update(
            {
                "status": "BASELINE_ERROR",
                "error_type": type(error).__name__,
                "error": canonicalize_text(str(error)),
            }
        )
        return payload

    try:
        schedule = build_schedule(utp, ACADEMIC_YEAR)
        content = build_content_model(schedule, utp, program, utp_path.name)
        v2_rows = build_lesson_content_v2(content)
    except Exception as error:
        payload.update(
            {
                "status": "BASELINE_ERROR",
                "error_type": type(error).__name__,
                "error": canonicalize_text(str(error)),
            }
        )
        return payload

    blocks = unresolved_mandatory_review_blocks(v2_rows)
    payload["unresolved_weeks"] = [week for week, _clauses in blocks]
    docx_weeks: dict[int, _DocxWeek] = {}
    review_weeks: list[int] = []
    try:
        plan = confirmed_plan_from_external_utp(
            utp,
            study_year=year,
            source_name=utp_path.name,
        )
        pipeline = run_calendar_pipeline(
            plan,
            program,
            academic_year=ACADEMIC_YEAR,
            template=select_calendar_template(),
            source_utp_name=utp_path.name,
            program_filename=program_path.name,
            use_ai=False,
        )
        extracted = extract_docx_weeks(pipeline.content)
        if len(extracted) != EXPECTED_WEEK_COUNT:
            raise ValueError(
                f"{spec['id']}: DOCX weeks {len(extracted)} != {EXPECTED_WEEK_COUNT}"
            )
        docx_weeks = {item.week_number: item for item in extracted}
        review_weeks = sorted({case.week_number for case in pipeline.review_cases})
        payload["status"] = str(pipeline.status.value)
    except Exception as error:
        payload["status"] = "BASELINE_ERROR"
        payload["error_type"] = type(error).__name__
        payload["error"] = canonicalize_text(str(error))

    payload["review_weeks"] = review_weeks
    payload["rows"] = [
        {
            **_row_record(row, docx_weeks.get(row.source.week_number)),
            "review": row.source.week_number in review_weeks
            or row.source.week_number in payload["unresolved_weeks"],
        }
        for row in sorted(v2_rows, key=lambda item: item.source.week_number)
    ]
    return payload


def capture_baseline_oracle(root: Path | None = None) -> dict[str, Any]:
    corpora = [_capture_corpus(spec, root) for spec in discover_runnable_specs(root)]
    corpora.sort(key=lambda item: item["id"])
    return {
        "baseline_commit": BASELINE_COMMIT,
        "academic_year": ACADEMIC_YEAR,
        "predicate_registry": sorted(pedagogical_predicates()),
        "required_ids": [item["id"] for item in REQUIRED_FIXTURES],
        "corpora": corpora,
    }


def write_baseline_oracle(path: Path | None = None, root: Path | None = None) -> Path:
    import json

    target = path or default_oracle_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = capture_baseline_oracle(root)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def load_baseline_oracle(path: Path | None = None, root: Path | None = None) -> dict[str, Any]:
    import json

    target = path or default_oracle_path(root)
    return json.loads(target.read_text(encoding="utf-8"))


def _require_docx_or_error(corpus: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    corpus_id = corpus["id"]
    status = corpus.get("status")
    rows = corpus.get("rows") or []
    if status in READY_STATUSES:
        if len(rows) != EXPECTED_WEEK_COUNT:
            mismatches.append(
                f"{corpus_id}: expected {EXPECTED_WEEK_COUNT} rows, got {len(rows)}"
            )
        weeks = [row.get("week") for row in rows]
        if weeks != list(range(1, EXPECTED_WEEK_COUNT + 1)):
            mismatches.append(f"{corpus_id}: weeks are not 1..{EXPECTED_WEEK_COUNT}")
        for row in rows:
            for field in (
                "docx_lesson_type",
                "docx_planned_result",
                "docx_assessment",
                "docx_type_hash",
                "docx_result_hash",
                "docx_control_hash",
            ):
                if field not in row:
                    mismatches.append(f"{corpus_id} W{row.get('week')} missing {field}")
    elif status == "BASELINE_ERROR":
        if not corpus.get("error_type") or not corpus.get("error"):
            mismatches.append(f"{corpus_id}: blocked document missing error class/reason")
        expected_type = next(
            (
                item.get("expected_error_type")
                for item in REQUIRED_FIXTURES
                if item["id"] == corpus_id
            ),
            None,
        )
        if expected_type and corpus.get("error_type") != expected_type:
            mismatches.append(
                f"{corpus_id}: error_type {corpus.get('error_type')} != {expected_type}"
            )
    else:
        mismatches.append(f"{ORACLE_COVERAGE_MISSING}: {corpus_id} status={status}")
    return mismatches


def compare_oracle_payloads(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> list[str]:
    """Return human-readable mismatches. Empty list means identity."""

    mismatches: list[str] = []
    for key in ("baseline_commit", "academic_year", "predicate_registry", "required_ids"):
        if expected.get(key) != actual.get(key):
            mismatches.append(f"{key}: oracle drift")
    expected_corpora = {item["id"]: item for item in expected.get("corpora", [])}
    actual_corpora = {item["id"]: item for item in actual.get("corpora", [])}
    if sorted(expected_corpora) != sorted(actual_corpora):
        mismatches.append(
            f"{ORACLE_COVERAGE_MISSING}: {sorted(set(actual_corpora) - set(expected_corpora)) or sorted(set(expected_corpora) - set(actual_corpora))}"
        )
        return mismatches
    for corpus_id in sorted(expected_corpora):
        left = expected_corpora[corpus_id]
        right = actual_corpora[corpus_id]
        mismatches.extend(_require_docx_or_error(left))
        mismatches.extend(_require_docx_or_error(right))
        if left.get("status") != right.get("status"):
            mismatches.append(
                f"{corpus_id}.status: {left.get('status')} != {right.get('status')}"
            )
        if left.get("error_type") != right.get("error_type"):
            mismatches.append(
                f"{corpus_id}.error_type: {left.get('error_type')} != {right.get('error_type')}"
            )
        if left.get("error") != right.get("error"):
            mismatches.append(f"{corpus_id}.error differs")
        if left.get("review_weeks") != right.get("review_weeks"):
            mismatches.append(f"{corpus_id}.review_weeks differs")
        if left.get("unresolved_weeks") != right.get("unresolved_weeks"):
            mismatches.append(f"{corpus_id}.unresolved_weeks differs")
        left_rows = {row["week"]: row for row in left.get("rows", [])}
        right_rows = {row["week"]: row for row in right.get("rows", [])}
        if sorted(left_rows) != sorted(right_rows):
            mismatches.append(f"{corpus_id}.weeks differ")
            continue
        for week in sorted(left_rows):
            old = left_rows[week]
            new = right_rows[week]
            fields = [
                "match_status",
                "lesson_type",
                "planned_result",
                "assessment_method",
                "clause_coverage",
                "type_hash",
                "result_hash",
                "control_hash",
                "lexical_violations",
                "review",
            ]
            if left.get("status") in READY_STATUSES:
                fields.extend(
                    (
                        "docx_lesson_type",
                        "docx_planned_result",
                        "docx_assessment",
                        "docx_type_hash",
                        "docx_result_hash",
                        "docx_control_hash",
                    )
                )
            for field in fields:
                if old.get(field) != new.get(field):
                    mismatches.append(f"{corpus_id} W{week} {field}")
    return mismatches
