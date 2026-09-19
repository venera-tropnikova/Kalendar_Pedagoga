"""Identity passthrough: one production clause → one shadow atom.

Does not generate new TYPE/RESULT/CONTROL. Never called from production.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from calendar_pedagoga.semantic_atom.canonicalize import canonicalize_pairs, canonicalize_text

_SHADOW_INVOCATIONS = 0


class _CoverageRow(Protocol):
    lesson_type: str
    planned_result: str
    assessment_method: str
    clause_coverage: tuple[tuple[str, str], ...]


class DiffKind(StrEnum):
    EQUAL = "EQUAL"
    NEW_COVERS_MORE = "NEW_COVERS_MORE"
    NEW_LOSES = "NEW_LOSES"
    NEW_INVENTS = "NEW_INVENTS"
    UNRESOLVED_DRIFT = "UNRESOLVED_DRIFT"


@dataclass(frozen=True)
class ShadowAtom:
    text: str
    span: tuple[int, int]
    status: str


@dataclass(frozen=True)
class ShadowRow:
    lesson_type: str
    planned_result: str
    assessment_method: str
    clause_coverage: tuple[tuple[str, str], ...]
    atoms: tuple[ShadowAtom, ...]


@dataclass(frozen=True)
class IdentityDiff:
    kind: DiffKind
    field: str = ""
    old: str = ""
    new: str = ""


def shadow_invocation_count() -> int:
    return _SHADOW_INVOCATIONS


def reset_shadow_invocation_count() -> None:
    global _SHADOW_INVOCATIONS
    _SHADOW_INVOCATIONS = 0


def _atom_span(source: str, clause: str) -> tuple[int, int]:
    haystack = source or ""
    needle = clause or ""
    if not needle:
        return (0, 0)
    index = haystack.find(needle)
    if index < 0:
        stripped = needle.strip(" .;:")
        index = haystack.find(stripped) if stripped else -1
        needle = stripped
    if index < 0:
        return (-1, -1)
    return (index, index + len(needle))


def run_passthrough_shadow(
    row: _CoverageRow,
    *,
    source: str = "",
) -> ShadowRow:
    """Project current CE2 fields into the shadow shape without rewriting them."""

    global _SHADOW_INVOCATIONS
    _SHADOW_INVOCATIONS += 1
    atoms = tuple(
        ShadowAtom(text=clause, span=_atom_span(source, clause), status=status)
        for clause, status in row.clause_coverage
    )
    return ShadowRow(
        lesson_type=row.lesson_type,
        planned_result=row.planned_result,
        assessment_method=row.assessment_method,
        clause_coverage=tuple(row.clause_coverage),
        atoms=atoms,
    )


def compare_identity(old: _CoverageRow, shadow: ShadowRow) -> tuple[IdentityDiff, ...]:
    """C1 differential: passthrough must be EQUAL on pedagogical fields."""

    diffs: list[IdentityDiff] = []
    fields = (
        ("lesson_type", old.lesson_type, shadow.lesson_type),
        ("planned_result", old.planned_result, shadow.planned_result),
        ("assessment_method", old.assessment_method, shadow.assessment_method),
    )
    for name, left, right in fields:
        if canonicalize_text(left) != canonicalize_text(right):
            diffs.append(
                IdentityDiff(
                    kind=DiffKind.UNRESOLVED_DRIFT,
                    field=name,
                    old=canonicalize_text(left),
                    new=canonicalize_text(right),
                )
            )
    if canonicalize_pairs(old.clause_coverage) != canonicalize_pairs(shadow.clause_coverage):
        diffs.append(
            IdentityDiff(
                kind=DiffKind.UNRESOLVED_DRIFT,
                field="clause_coverage",
                old=repr(canonicalize_pairs(old.clause_coverage)),
                new=repr(canonicalize_pairs(shadow.clause_coverage)),
            )
        )
    if not diffs:
        return (IdentityDiff(kind=DiffKind.EQUAL),)
    return tuple(diffs)
