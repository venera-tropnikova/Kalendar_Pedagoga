"""Read-only index of stage A; source text and XML are never re-extracted."""
from __future__ import annotations

from collections import defaultdict
import re

from calendar_pedagoga.lossless_document.models import ExtractedDocument
from .models import Evidence


def local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def number_prefix(text: str) -> tuple[int, ...]:
    match = re.match(
        r'^\s*(?:(?:раздел|тема)\s*(?:№\s*)?)?(\d+(?:\.\d+)*)(?:[.)]?(?:\s+|$)|[.)]?(?=[А-Яа-я]))',
        text, re.I,
    )
    return tuple(map(int, match.group(1).split('.'))) if match else ()


class DocumentIndex:
    def __init__(self, document: ExtractedDocument):
        self.document = document
        self.nodes = {n.id: n for n in document.nodes}
        self.paragraphs = {p.id: p for p in document.paragraphs}
        self.tables = {t.id: t for t in document.tables}
        self.children = {n.id: tuple(self.nodes[c] for c in n.child_ids) for n in document.nodes}
        self.block_ids = document.block_ids
        self.positions = {b: i for i, b in enumerate(self.block_ids)}
        self.main_paragraphs = [p for p in document.paragraphs if p.source.part_uri == '/word/document.xml']
        self.paragraph_indices = {p.id: i for i, p in enumerate(self.main_paragraphs)}
        self.cell_paragraphs = defaultdict(list)
        for paragraph in document.paragraphs:
            if paragraph.cell:
                c = paragraph.cell
                self.cell_paragraphs[(c.table_id, c.row, c.column)].append(paragraph)
        self.flow = []
        for bid in self.block_ids:
            item = self.paragraphs.get(bid) or self.tables.get(bid)
            if item and item.source.part_uri == '/word/document.xml' and not any(
                local(n.tag) in ('p', 'tbl', 'Choice', 'Fallback') for n in self.ancestors(bid)
            ):
                self.flow.append(item)
        self.flow_positions = {b.id: i for i, b in enumerate(self.flow)}
        self.styles = {self.attribute(n, 'styleId'): n for n in document.nodes if local(n.tag) == 'style'}
        self.flag_cache = {}
        self.number_sources = {}
        self.number_labels = self._numbering_labels()

    def child(self, node_id, name):
        return next((n for n in self.children.get(node_id, ()) if local(n.tag) == name), None)

    def attribute(self, node, name, default=None):
        if node is None:
            return default
        return next((v for k, v in node.attributes if local(k) == name), default)

    def property(self, node_id, name):
        return self.attribute(self.child(node_id, name), 'val')

    def descendants(self, node_id):
        stack = list(reversed(self.children.get(node_id, ())))
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(self.children.get(node.id, ())))

    def ancestors(self, node_id):
        parent = self.nodes[node_id].parent_id
        while parent:
            yield self.nodes[parent]
            parent = self.nodes[parent].parent_id

    def evidence(self, code, ids, observed=''):
        ids = tuple(dict.fromkeys(ids))
        return Evidence(code, ids, tuple(self.nodes[i].source for i in ids), observed)

    def table_blocks(self, container):
        """All canonical blocks in a container, including nested paragraphs/tables."""
        return tuple(n.id for n in (self.nodes[container.id], *self.descendants(container.id))
                     if n.id in self.positions)

    def cell_text(self, table_id, cell):
        c = cell.coordinate
        return tuple(self.cell_paragraphs[(table_id, c.row, c.column)])

    def grid(self, table):
        grid = {}
        cells = {c.id: c for c in table.physical_cells}
        for logical in table.logical_cells:
            for slot in logical.grid_slots:
                grid[slot] = cells[logical.anchor_id]
        return grid

    def preceding(self, block_id, limit=4):
        pos = self.flow_positions.get(block_id, 0)
        result = []
        for block in reversed(self.flow[:pos]):
            if block.id in self.tables:
                break
            if block.normalized_text:
                result.append(block)
                if len(result) >= limit:
                    break
        return tuple(reversed(result))

    def flags(self, paragraph):
        if paragraph.id in self.flag_cache:
            return self.flag_cache[paragraph.id]
        runs = [n for n in self.descendants(paragraph.id) if local(n.tag) == 'r'
                and any(local(c.tag) == 't' for c in self.children[n.id])]
        bold = 0
        for run in runs:
            properties = self.child(run.id, 'rPr')
            b = self.child(properties.id, 'b') if properties else None
            if b and self.attribute(b, 'val', '1') not in ('0', 'false', 'off'):
                bold += 1
        properties = self.child(paragraph.id, 'pPr')
        outline = self.property(properties.id, 'outlineLvl') if properties else None
        style = self.styles.get(paragraph.style_id)
        seen = set()
        while style and style.id not in seen:
            seen.add(style.id)
            properties = self.child(style.id, 'pPr')
            if outline is None and properties:
                outline = self.property(properties.id, 'outlineLvl')
            style = self.styles.get(self.property(style.id, 'basedOn'))
        result = {'bold': bool(runs) and bold / len(runs) >= .8,
                  'outline': int(outline) if outline and outline.isdigit() and int(outline) < 9 else None}
        self.flag_cache[paragraph.id] = result
        return result

    def key(self, paragraph):
        return number_prefix(paragraph.normalized_text) or self.number_labels.get(paragraph.id, ())

    def key_evidence(self, paragraph):
        if number_prefix(paragraph.normalized_text):
            return self.evidence('LITERAL_NUMBER_PREFIX', [paragraph.id], str(self.key(paragraph)))
        return self.evidence('NUMBERING_DEFINITION' if paragraph.id in self.number_labels else 'NO_NUMERIC_KEY',
                             (paragraph.id, *self.number_sources.get(paragraph.id, ())), str(self.key(paragraph)))

    def _numbering_labels(self):
        """Decimal structural keys only. Unsupported restart/format rules stay unresolved.

        This never writes a rendered list label into raw_text. Explicit definitions,
        full level overrides and startOverride are retained as source evidence.
        """
        abstract = {self.attribute(n, 'abstractNumId'): n for n in self.document.nodes if local(n.tag) == 'abstractNum'}
        nums = {self.attribute(n, 'numId'): n for n in self.document.nodes if local(n.tag) == 'num'}
        counters, labels = {}, {}
        for paragraph in self.document.paragraphs:
            numbering = paragraph.numbering
            if not numbering or not numbering.level.isdigit():
                continue
            num = nums.get(numbering.num_id)
            definition = abstract.get(self.property(num.id, 'abstractNumId')) if num else None
            if not definition:
                continue
            levels = {self.attribute(n, 'ilvl'): n for n in self.children[definition.id] if local(n.tag) == 'lvl'}
            overrides = [n for n in self.children[num.id] if local(n.tag) == 'lvlOverride']
            starts = {}
            for override in overrides:
                ilvl = self.attribute(override, 'ilvl')
                level_spec = self.child(override.id, 'lvl')
                if level_spec:
                    levels[ilvl] = level_spec
                start = self.property(override.id, 'startOverride')
                if start is not None:
                    starts[ilvl] = start
            level = int(numbering.level)
            spec = levels.get(str(level))
            self.number_sources[paragraph.id] = (num.id, definition.id, *(o.id for o in overrides))
            if not spec or any(self.child(s.id, 'lvlRestart') for s in levels.values()):
                continue
            pattern = self.property(spec.id, 'lvlText') or ''
            tokens = re.findall(r'%(\d+)', pattern)
            if not tokens or any(self.property(levels[str(int(t) - 1)].id, 'numFmt') not in ('decimal', 'decimalZero')
                                 for t in tokens if str(int(t) - 1) in levels):
                continue
            start = starts.get(str(level), self.property(spec.id, 'start') or '1')
            if not start.isdigit():
                continue
            count = counters.setdefault((paragraph.source.part_uri, numbering.num_id), {})
            count[level] = count.get(level, int(start) - 1) + 1
            for deeper in [k for k in count if k > level]:
                del count[deeper]
            values = []
            for token in tokens:
                position = int(token) - 1
                if position in count:
                    values.append(count[position])
                elif str(position) in levels:
                    initial = starts.get(str(position), self.property(levels[str(position)].id, 'start') or '1')
                    if not initial.isdigit():
                        break
                    values.append(int(initial))
                else:
                    break
            else:
                labels[paragraph.id] = tuple(values)
        return labels
