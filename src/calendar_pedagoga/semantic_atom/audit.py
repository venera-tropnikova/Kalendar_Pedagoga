"""C10 full shadow differential audit. Measures only; does not cut over."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
from typing import Any

from calendar_pedagoga.confirmed_study_plan import (
    ConfirmedStudyPlanError,
    confirmed_plan_from_external_utp,
)
from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import infer_study_year_number, parse_program
from calendar_pedagoga.scheduling import build_schedule
from calendar_pedagoga.semantic_atom.canonicalize import canonicalize_text, text_hash
from calendar_pedagoga.semantic_atom.diff_adapter import (
    WeekDiff,
    classify_week,
    snapshot_from_row,
    worst_kind,
)
from calendar_pedagoga.semantic_atom.flags import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.oracle import (
    ACADEMIC_YEAR,
    REQUIRED_FIXTURES,
    find_named,
    fixture_roots,
    project_root,
)
from calendar_pedagoga.semantic_atom.passthrough import DiffKind

ADAPTER_NAME = "audit_c10"
MISSING_UTP = "missing_utp"
MISSING_PROGRAM = "missing_program"
MISSING_FIXTURE = "missing_fixture"
YEAR_UNSPECIFIED = "year_unspecified"
EMPTY_CONTENT = "empty_content"
UNRECOGNIZED_PROGRAM = "программа не распознана"
BLOCKED_IMPORT = "BLOCKED_IMPORT"
BLOCKED_PLAN = "BLOCKED_PLAN"
LANE_PRODUCTION = "production_runnable"
LANE_DIAGNOSTIC = "diagnostic_only"
LANE_BLOCKED = "blocked"
C11_BLOCKERS = frozenset({DiffKind.NEW_INVENTS, DiffKind.NEW_LOSES})

_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|\\\\)[^\s\"']+")
_FIO_FIELD_RE = re.compile(r"(?i)\b(?:фио|teacher_name|pedagogue)\b")
_YEAR_RE = re.compile(r"(\d+)\s*г", re.IGNORECASE)
_KNOWN_FAMILY = (
    ("ключ", "key"),
    ("скалолаз", "climb"),
    ("проводн", "tour"),
    ("утп тп", "tour"),
)


@dataclass(frozen=True)
class CorpusPair:
    id: str
    program: str
    utp: str
    study_year: int | None
    run_status: str = ""
    analysis_lane: str = ""
    block_reason: str = ""
    error_type: str = ""
    error: str = ""

    def public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "program": self.program,
            "utp": self.utp,
            "study_year": self.study_year,
            "run_status": self.run_status,
            "analysis_lane": self.analysis_lane or _inferred_lane(self, has_weeks=False),
            "block_reason": self.block_reason,
            "error_type": self.error_type,
        }
        if self.error:
            payload["error"] = sanitize_text(self.error)
        return payload


def sanitize_text(value: str | None) -> str:
    text = canonicalize_text(value)
    text = _PATH_RE.sub("<path>", text)
    if _FIO_FIELD_RE.search(text):
        text = _FIO_FIELD_RE.sub("<redacted>", text)
    return text


def iter_documents(roots: tuple[Path, ...] | None = None) -> tuple[Path, ...]:
    folders = roots if roots is not None else fixture_roots()
    seen: set[str] = set()
    found: list[Path] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
            if path.suffix.lower() not in {".doc", ".docx"}:
                continue
            key = path.name.casefold()
            if key in seen or "календарн" in key:
                continue
            seen.add(key)
            found.append(path)
    return tuple(found)


def document_names(roots: tuple[Path, ...] | None = None) -> tuple[str, ...]:
    return tuple(path.name for path in iter_documents(roots))


def _family(name: str) -> str | None:
    folded = name.casefold()
    if "календарн" in folded:
        return None
    for needle, family in _KNOWN_FAMILY:
        if needle in folded:
            return family
    return _stem_family(name)


def _stem_family(name: str) -> str:
    stem = Path(name).stem.casefold()
    stem = re.sub(r"^утп\s+", "", stem)
    stem = re.sub(r"^программа\s+", "", stem)
    stem = re.sub(r"\d+\s*г(?:\.|од)?", "", stem)
    stem = re.sub(r"\d+\s*ч", "", stem)
    stem = re.sub(r"[_\W]+", " ", stem, flags=re.UNICODE)
    return canonicalize_text(stem) or "extra"


def _year_from_name(name: str) -> int | None:
    match = _YEAR_RE.search(name)
    return int(match.group(1)) if match else None


def _is_utp(name: str) -> bool:
    return name.casefold().startswith("утп")


def accounted_document_names(pairs: tuple[CorpusPair, ...]) -> set[str]:
    names: set[str] = set()
    for pair in pairs:
        if pair.program:
            names.add(pair.program)
        if pair.utp:
            names.add(pair.utp)
    return names


def discover_corpus_pairs(
    *,
    roots: tuple[Path, ...] | None = None,
    include_required: bool = True,
) -> tuple[CorpusPair, ...]:
    """Every available program/UTP/year pair. Nothing is dropped silently."""

    pairs: list[CorpusPair] = []
    known_keys: set[tuple[str, str, int | None]] = set()
    known_ids: set[str] = set()

    if include_required:
        for spec in REQUIRED_FIXTURES:
            pair = CorpusPair(
                id=str(spec["id"]),
                program=str(spec["program"]),
                utp=str(spec["utp"]),
                study_year=int(spec["study_year"]),
            )
            pairs.append(pair)
            known_ids.add(pair.id)
            known_keys.add((pair.program.casefold(), pair.utp.casefold(), pair.study_year))

    docs = iter_documents(roots)
    programs = [path for path in docs if not _is_utp(path.name)]
    utps = [path for path in docs if _is_utp(path.name)]
    used_programs: set[str] = {pair.program.casefold() for pair in pairs if pair.program}
    used_utps: set[str] = {pair.utp.casefold() for pair in pairs if pair.utp}

    programs_by_family: dict[str, list[Path]] = {}
    for program in programs:
        programs_by_family.setdefault(_family(program.name) or "extra", []).append(program)

    for utp in utps:
        family = _family(utp.name) or "extra"
        year = _year_from_name(utp.name)
        for program in programs_by_family.get(family, ()):
            key = (program.name.casefold(), utp.name.casefold(), year)
            if key in known_keys:
                used_programs.add(program.name.casefold())
                used_utps.add(utp.name.casefold())
                continue
            if year is None and any(
                item.program.casefold() == program.name.casefold()
                and item.utp.casefold() == utp.name.casefold()
                for item in pairs
            ):
                used_programs.add(program.name.casefold())
                used_utps.add(utp.name.casefold())
                continue
            pair_id = f"{family}_y{year}" if year is not None else f"{family}_yearless"
            if pair_id in known_ids:
                pair_id = f"{pair_id}_{text_hash(utp.name + program.name)[:8]}"
            pair = CorpusPair(
                id=pair_id,
                program=program.name,
                utp=utp.name,
                study_year=year,
            )
            pairs.append(pair)
            known_ids.add(pair.id)
            known_keys.add(key)
            used_programs.add(program.name.casefold())
            used_utps.add(utp.name.casefold())

    for program in programs:
        if program.name.casefold() in used_programs:
            continue
        pair = CorpusPair(
            id=f"unpaired_program_{text_hash(program.name)[:12]}",
            program=program.name,
            utp="",
            study_year=_year_from_name(program.name),
        )
        pairs.append(pair)
    for utp in utps:
        if utp.name.casefold() in used_utps:
            continue
        pair = CorpusPair(
            id=f"unpaired_utp_{text_hash(utp.name)[:12]}",
            program="",
            utp=utp.name,
            study_year=_year_from_name(utp.name),
        )
        pairs.append(pair)

    pairs.sort(key=lambda item: (item.id, item.program, item.utp, item.study_year or -1))
    return tuple(pairs)


def _blocked(
    pair: CorpusPair,
    *,
    status: str,
    reason: str,
    error: BaseException | None = None,
    year: int | None = None,
    lane: str = LANE_BLOCKED,
) -> CorpusPair:
    return replace(
        pair,
        run_status=status,
        analysis_lane=lane,
        block_reason=sanitize_text(reason) if reason else "",
        error_type=type(error).__name__ if error is not None else pair.error_type,
        error=sanitize_text(str(error)) if error is not None else pair.error,
        study_year=year if year is not None else pair.study_year,
    )


def _import_block(pair: CorpusPair, error: BaseException, year: int | None = None) -> CorpusPair:
    return _blocked(
        pair,
        status=BLOCKED_IMPORT,
        reason=str(error) or type(error).__name__,
        error=error,
        year=year,
    )


def _recognized_program(program: object) -> bool:
    return bool(getattr(program, "content_items", ()))


def _try_parse_program(path: Path, year: int | None):
    return parse_program(path.read_bytes(), path.name, study_year=year)


def assess_pair(pair: CorpusPair, roots: tuple[Path, ...] | None = None) -> CorpusPair:
    if pair.analysis_lane and pair.run_status:
        return pair
    program_path = _resolve_named(pair.program, roots) if pair.program else None
    utp_path = _resolve_named(pair.utp, roots) if pair.utp else None

    if pair.program and not pair.utp:
        if program_path is None:
            return _blocked(pair, status="BLOCKED", reason=MISSING_FIXTURE)
        try:
            program = _try_parse_program(program_path, pair.study_year)
        except Exception as error:
            return _import_block(pair, error)
        if not _recognized_program(program):
            return _blocked(pair, status=BLOCKED_IMPORT, reason=UNRECOGNIZED_PROGRAM)
        return _blocked(pair, status="BLOCKED", reason=MISSING_UTP)

    if pair.utp and not pair.program:
        if utp_path is None:
            return _blocked(pair, status="BLOCKED", reason=MISSING_FIXTURE)
        try:
            parsed = parse_utp(utp_path)
        except Exception as error:
            return _import_block(pair, error)
        if not parsed.topics:
            return _blocked(pair, status=BLOCKED_IMPORT, reason="утп не распознан")
        return _blocked(pair, status="BLOCKED", reason=MISSING_PROGRAM)

    if not pair.program or not pair.utp:
        return _blocked(pair, status="BLOCKED", reason=MISSING_FIXTURE)
    if program_path is None or utp_path is None:
        return _blocked(pair, status="BLOCKED", reason=MISSING_FIXTURE)

    try:
        utp = parse_utp(utp_path)
    except Exception as error:
        return _import_block(pair, error)
    year = pair.study_year
    if year is None:
        year = infer_study_year_number(utp.metadata.study_year) or _year_from_name(utp_path.name)
    if year is None:
        return _blocked(pair, status="BLOCKED", reason=YEAR_UNSPECIFIED)
    try:
        program = _try_parse_program(program_path, year)
    except Exception as error:
        return _import_block(pair, error, year=year)
    if not _recognized_program(program):
        return _blocked(pair, status=BLOCKED_IMPORT, reason=UNRECOGNIZED_PROGRAM, year=year)

    plan_error: ConfirmedStudyPlanError | None = None
    try:
        confirmed_plan_from_external_utp(utp, study_year=year, source_name=utp_path.name)
    except ConfirmedStudyPlanError as error:
        plan_error = error

    try:
        rows = build_lesson_content_v2(
            build_content_model(build_schedule(utp, ACADEMIC_YEAR), utp, program, utp_path.name)
        )
    except Exception as error:
        if plan_error is not None:
            return _blocked(
                pair,
                status=BLOCKED_PLAN,
                reason=str(plan_error),
                error=plan_error,
                year=year,
            )
        return _import_block(pair, error, year=year)

    if plan_error is not None:
        return _blocked(
            pair,
            status=BLOCKED_PLAN,
            reason=str(plan_error),
            error=plan_error,
            year=year,
            lane=LANE_DIAGNOSTIC if rows else LANE_BLOCKED,
        )
    if not rows:
        return _blocked(pair, status="BLOCKED", reason=EMPTY_CONTENT, year=year)
    return replace(
        pair,
        run_status="RUNNABLE",
        analysis_lane=LANE_PRODUCTION,
        study_year=year,
    )


def _root_hint(roots: tuple[Path, ...] | None) -> Path | None:
    if not roots:
        return None
    return roots[0]


def _find_in_roots(name: str, roots: tuple[Path, ...]) -> Path | None:
    for folder in roots:
        path = folder / name
        if path.is_file():
            return path
    return None


def _resolve_named(name: str, roots: tuple[Path, ...] | None) -> Path | None:
    path = find_named(name, _root_hint(roots))
    if roots is not None:
        path = path or _find_in_roots(name, roots)
    return path


def _inferred_lane(pair: CorpusPair, *, has_weeks: bool) -> str:
    if pair.analysis_lane:
        return pair.analysis_lane
    if pair.run_status == "RUNNABLE":
        return LANE_PRODUCTION
    if pair.run_status == BLOCKED_PLAN and has_weeks:
        return LANE_DIAGNOSTIC
    return LANE_BLOCKED


def _empty_week_stats() -> dict[str, Any]:
    return {
        "pairs": 0,
        "weeks_total": 0,
        "diff": {str(kind): 0 for kind in DiffKind},
        "old": {"empty_result": 0, "empty_control": 0},
        "shadow": {
            "empty_result": 0,
            "empty_control": 0,
            "atoms": 0,
            "proven": 0,
            "unresolved": 0,
        },
        "unresolved_by_reason": {},
    }


def _add_week(stats: dict[str, Any], item: WeekDiff) -> None:
    stats["weeks_total"] += 1
    stats["diff"][str(item.kind)] += 1
    stats["old"]["empty_result"] += int(item.old_empty_result)
    stats["old"]["empty_control"] += int(item.old_empty_control)
    stats["shadow"]["empty_result"] += int(item.shadow_empty_result)
    stats["shadow"]["empty_control"] += int(item.shadow_empty_control)
    stats["shadow"]["atoms"] += item.shadow_atoms
    stats["shadow"]["proven"] += item.shadow_proven
    stats["shadow"]["unresolved"] += item.shadow_unresolved
    for reason in item.unresolved_reasons:
        bucket = stats["unresolved_by_reason"]
        bucket[reason] = bucket.get(reason, 0) + 1


def _load_rows(pair: CorpusPair, roots: tuple[Path, ...] | None = None):
    program_path = _resolve_named(pair.program, roots)
    utp_path = _resolve_named(pair.utp, roots)
    if program_path is None or utp_path is None:
        raise FileNotFoundError(MISSING_FIXTURE)
    utp = parse_utp(utp_path)
    year = pair.study_year or infer_study_year_number(utp.metadata.study_year)
    program = parse_program(
        program_path.read_bytes(),
        program_path.name,
        study_year=year,
    )
    content = build_content_model(
        build_schedule(utp, ACADEMIC_YEAR),
        utp,
        program,
        utp_path.name,
    )
    return build_lesson_content_v2(content)


def classify_pair_weeks(
    pair: CorpusPair,
    roots: tuple[Path, ...] | None = None,
) -> tuple[WeekDiff, ...]:
    rows = _load_rows(pair, roots)
    diffs: list[WeekDiff] = []
    for row in sorted(rows, key=lambda item: item.source.week_number):
        old = snapshot_from_row(row)
        diffs.append(classify_week(row, old.source))
    return tuple(diffs)


def build_audit_report(
    pairs: tuple[CorpusPair, ...],
    weeks: dict[str, tuple[WeekDiff, ...]],
) -> dict[str, Any]:
    production = _empty_week_stats()
    diagnostic = _empty_week_stats()
    blocked_by_status: dict[str, int] = {}
    blocked_by_reason: dict[str, int] = {}
    blocked_pairs = 0
    corpora: list[dict[str, Any]] = []
    for pair in sorted(pairs, key=lambda item: item.id):
        pair_weeks = weeks.get(pair.id, ())
        lane = _inferred_lane(pair, has_weeks=bool(pair_weeks))
        week_kinds = [item.kind for item in pair_weeks]
        pair_kind = (
            str(worst_kind(week_kinds))
            if pair_weeks and lane in {LANE_PRODUCTION, LANE_DIAGNOSTIC}
            else pair.run_status or "BLOCKED"
        )
        if lane == LANE_PRODUCTION:
            production["pairs"] += 1
            for item in pair_weeks:
                _add_week(production, item)
        elif lane == LANE_DIAGNOSTIC:
            diagnostic["pairs"] += 1
            for item in pair_weeks:
                _add_week(diagnostic, item)
        else:
            blocked_pairs += 1
            blocked_by_status[pair.run_status or "BLOCKED"] = (
                blocked_by_status.get(pair.run_status or "BLOCKED", 0) + 1
            )
            reason = pair.block_reason or pair.error_type or "blocked"
            blocked_by_reason[reason] = blocked_by_reason.get(reason, 0) + 1
        corpora.append(
            {
                "id": pair.id,
                "run_status": pair.run_status,
                "analysis_lane": lane,
                "block_reason": pair.block_reason,
                "error_type": pair.error_type,
                "study_year": pair.study_year,
                "kind": pair_kind,
                "weeks": [item.public_dict() for item in pair_weeks],
            }
        )
    production["unresolved_by_reason"] = dict(sorted(production["unresolved_by_reason"].items()))
    diagnostic["unresolved_by_reason"] = dict(sorted(diagnostic["unresolved_by_reason"].items()))
    prod_diff = production["diff"]
    c11 = (
        "BLOCKED"
        if prod_diff[str(DiffKind.NEW_INVENTS)] or prod_diff[str(DiffKind.NEW_LOSES)]
        else "READY"
    )
    return {
        "academic_year": ACADEMIC_YEAR,
        "flag": bool(USE_SEMANTIC_ATOM_ENGINE),
        "c11_status": c11,
        "c11_blockers": sorted(
            str(kind) for kind in C11_BLOCKERS if prod_diff[str(kind)]
        ),
        "severity_order": [str(kind) for kind in (
            DiffKind.NEW_INVENTS,
            DiffKind.NEW_LOSES,
            DiffKind.UNRESOLVED_DRIFT,
            DiffKind.NEW_COVERS_MORE,
            DiffKind.EQUAL,
            DiffKind.BLOCKED,
        )],
        "manifest": [pair.public_dict() for pair in sorted(pairs, key=lambda item: item.id)],
        "aggregates": {
            "pairs_total": len(pairs),
            LANE_PRODUCTION: production,
            LANE_DIAGNOSTIC: diagnostic,
            "blocked_documents": {
                "pairs": blocked_pairs,
                "by_status": dict(sorted(blocked_by_status.items())),
                "by_reason": dict(sorted(blocked_by_reason.items())),
            },
        },
        "corpora": corpora,
    }


def run_differential_audit(
    *,
    roots: tuple[Path, ...] | None = None,
    include_required: bool = True,
) -> dict[str, Any]:
    discovered = discover_corpus_pairs(roots=roots, include_required=include_required)
    assessed: list[CorpusPair] = []
    weeks: dict[str, tuple[WeekDiff, ...]] = {}
    for pair in discovered:
        current = assess_pair(pair, roots)
        assessed.append(current)
        lane = current.analysis_lane or _inferred_lane(current, has_weeks=False)
        if lane not in {LANE_PRODUCTION, LANE_DIAGNOSTIC}:
            weeks[current.id] = ()
            continue
        try:
            weeks[current.id] = classify_pair_weeks(current, roots)
        except Exception as error:
            if current.run_status == BLOCKED_PLAN:
                assessed[-1] = replace(
                    current,
                    analysis_lane=LANE_BLOCKED,
                    error_type=type(error).__name__,
                    error=sanitize_text(str(error)),
                )
            else:
                assessed[-1] = _import_block(current, error, year=current.study_year)
            weeks[current.id] = ()
    report = build_audit_report(tuple(assessed), weeks)
    assert_report_safe(report)
    return report


def assert_report_safe(report: dict[str, Any]) -> None:
    blob = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if "SOURCE" in blob:
        raise AssertionError("public report contains SOURCE")
    if _PATH_RE.search(blob):
        raise AssertionError("public report contains a local path")
    if re.search(r"(?i)\bфио\b", blob):
        raise AssertionError("public report contains FIO")


def report_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_local_report(
    report: dict[str, Any] | None = None,
    *,
    root: Path | None = None,
) -> Path:
    payload = report if report is not None else run_differential_audit()
    target_root = project_root(root)
    out_dir = target_root / "_shadow_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "c10_differential_audit.json"
    target.write_text(report_json(payload), encoding="utf-8")
    manifest = {
        "c11_status": payload["c11_status"],
        "aggregates": payload["aggregates"],
        "manifest": payload["manifest"],
    }
    (out_dir / "c10_corpus_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
