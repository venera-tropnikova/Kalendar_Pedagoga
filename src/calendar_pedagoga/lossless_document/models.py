"""Immutable evidence-only contracts. No educational/domain interpretation."""
from __future__ import annotations

from dataclasses import dataclass
import re


def normalize_text(raw: str) -> str:
    """A search view only; never used to replace source text or identify objects."""
    return re.sub(r"\s+", " ", raw).strip()


@dataclass(frozen=True)
class SourceSpan:
    part_uri: str
    element_path: tuple[int, ...]
    char_start: int | None = None
    char_end: int | None = None

    @property
    def xpath(self) -> str:
        return "/*[1]" + "".join(f"/*[{index + 1}]" for index in self.element_path)


@dataclass(frozen=True)
class PackagePart:
    name: str
    data: bytes
    sha256: str


@dataclass(frozen=True)
class XmlNode:
    id: str
    parent_id: str | None
    child_ids: tuple[str, ...]
    order: int
    tag: str
    attributes: tuple[tuple[str, str], ...]
    text: str | None
    tail: str | None
    source: SourceSpan

    @property
    def raw_text(self) -> str:
        return self.text or ""

    @property
    def normalized_text(self) -> str:
        return normalize_text(self.raw_text)


@dataclass(frozen=True)
class TextFragment:
    id: str
    owner_id: str | None
    role: str
    raw_text: str
    normalized_text: str
    source: SourceSpan


@dataclass(frozen=True)
class Numbering:
    num_id: str
    level: str
    inherited_from_style: bool


@dataclass(frozen=True)
class CellCoordinate:
    table_id: str
    row: int
    column: int
    column_span: int


@dataclass(frozen=True)
class Paragraph:
    id: str
    parent_id: str | None
    fragment_ids: tuple[str, ...]
    raw_text: str
    normalized_text: str
    style_id: str | None
    numbering: Numbering | None
    cell: CellCoordinate | None
    source: SourceSpan


@dataclass(frozen=True)
class PhysicalCell:
    id: str
    coordinate: CellCoordinate
    vertical_merge: str | None
    horizontal_merge: str | None
    source: SourceSpan


@dataclass(frozen=True)
class LogicalCell:
    id: str
    anchor_id: str
    physical_cell_ids: tuple[str, ...]
    grid_slots: tuple[tuple[int, int], ...]
    merged: bool


@dataclass(frozen=True)
class Table:
    id: str
    row_ids: tuple[str, ...]
    grid_widths: tuple[str | None, ...]
    repeated_header_row_ids: tuple[str, ...]
    physical_cells: tuple[PhysicalCell, ...]
    logical_cells: tuple[LogicalCell, ...]
    source: SourceSpan


@dataclass(frozen=True)
class AlternativeBranch:
    id: str
    container_id: str
    kind: str
    requires: str | None
    source: SourceSpan


@dataclass(frozen=True)
class YearMarkerCandidate:
    id: str
    paragraph_id: str
    raw_text: str
    marker: str
    evidence: tuple[str, ...]
    confidence: float
    source: SourceSpan
    # Intentionally no year scope, content assignment or selected study year.


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    source: SourceSpan | None = None


@dataclass(frozen=True)
class ConversionEvent:
    tool: str
    tool_version: str
    input_sha256: str
    output_sha256: str | None
    input_format: str
    output_format: str
    command: tuple[str, ...]
    status: str
    returncode: int | None
    stdout: str
    stderr: str
    elapsed_seconds: float


@dataclass(frozen=True)
class ExtractedDocument:
    schema_version: str
    id: str
    source_format: str
    source_sha256: str
    package_sha256: str
    original_bytes: bytes
    package_bytes: bytes
    parts: tuple[PackagePart, ...]
    nodes: tuple[XmlNode, ...]
    fragments: tuple[TextFragment, ...]
    paragraphs: tuple[Paragraph, ...]
    tables: tuple[Table, ...]
    unknown_block_ids: tuple[str, ...]
    alternative_branches: tuple[AlternativeBranch, ...]
    year_candidates: tuple[YearMarkerCandidate, ...]
    diagnostics: tuple[Diagnostic, ...]
    conversion_log: tuple[ConversionEvent, ...]

    @property
    def block_ids(self) -> tuple[str, ...]:
        ids = {p.id for p in self.paragraphs} | {t.id for t in self.tables} | set(self.unknown_block_ids)
        return tuple(node.id for node in self.nodes if node.id in ids)

    def counts(self) -> dict[str, int]:
        cells = [c for table in self.tables for c in table.physical_cells]
        logical = [c for table in self.tables for c in table.logical_cells]
        listed = [p for p in self.paragraphs if p.numbering is not None]
        return {
            "blocks": len(self.block_ids), "paragraphs": len(self.paragraphs),
            "tables": len(self.tables), "physical_cells": len(cells),
            "logical_cells": len(logical), "merges": sum(c.merged for c in logical),
            "grid_slots": sum(len(c.grid_slots) for c in logical),
            "list_paragraphs": len(listed),
            "list_instances": len({(p.source.part_uri, p.numbering.num_id) for p in listed}),
            "year_candidates": len(self.year_candidates),
            "unknown_blocks": len(self.unknown_block_ids), "alternative_branches": len(self.alternative_branches),
            "xml_nodes": len(self.nodes),
            "text_fragments": len(self.fragments),
        }
