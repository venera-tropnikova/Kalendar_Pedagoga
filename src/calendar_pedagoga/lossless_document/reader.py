"""An ordered OOXML evidence graph. This module has no downstream dependencies."""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from pathlib import Path
import re

from lxml import etree

from .formats import ExtractionError, LibreOfficeConverter, detect_format, parse_xml, read_package
from .models import (
    AlternativeBranch, CellCoordinate, Diagnostic, ExtractedDocument, LogicalCell, Numbering, PackagePart,
    Paragraph, PhysicalCell, SourceSpan, Table, TextFragment, XmlNode, YearMarkerCandidate,
    normalize_text,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STRICT_W = "http://purl.oclc.org/ooxml/wordprocessingml/main"
WORD_NAMESPACES = {W, STRICT_W}
MAX_XML_NODES = 300_000
MAX_GRID_SLOTS = 1_000_000


def word_name(element: etree._Element) -> str | None:
    if element is None or not isinstance(element.tag, str):
        return None
    qname = etree.QName(element)
    return qname.localname if qname.namespace in WORD_NAMESPACES else None


def children(element: etree._Element) -> list[etree._Element]:
    return [child for child in element if isinstance(child.tag, str)]


def child(element: etree._Element, name: str) -> etree._Element | None:
    return next((e for e in children(element) if word_name(e) == name), None)


def attr(element: etree._Element | None, name: str) -> str | None:
    if element is None:
        return None
    return next((element.get(f"{{{ns}}}{name}") for ns in (W, STRICT_W) if element.get(f"{{{ns}}}{name}") is not None), None)


def value(element: etree._Element, parent: str, prop: str) -> str | None:
    props = child(element, parent)
    return attr(child(props, prop), "val") if props is not None else None


def stable_id(document_id: str, part: str, path: tuple[int, ...], suffix: str = "") -> str:
    address = part + ":" + "/".join(map(str, path)) + ":" + suffix
    return sha256((document_id + "\0" + address).encode("utf-8")).hexdigest()


def _nearest(element: etree._Element, name: str) -> etree._Element | None:
    return next((p for p in element.iterancestors() if word_name(p) == name), None)


def _owned_descendants(element: etree._Element, name: str, boundary: str):
    return [e for e in element.iterdescendants() if word_name(e) == name and _nearest(e, boundary) is element]


def _numbering(paragraph, styles, default_style, diagnostics, span) -> Numbering | None:
    style_id = value(paragraph, "pPr", "pStyle") or default_style
    num_id = level = None
    inherited = False
    sources = [paragraph]
    visited: set[str] = set()
    while style_id and style_id in styles:
        if style_id in visited:
            diagnostics.append(Diagnostic("STYLE_CYCLE", "Cyclic style inheritance retained", span))
            break
        visited.add(style_id)
        style = styles[style_id]
        sources.append(style)
        style_id = attr(child(style, "basedOn"), "val")
    for index, source in enumerate(sources):
        props = child(source, "pPr")
        numbering = child(props, "numPr") if props is not None else None
        if numbering is not None:
            found_num = attr(child(numbering, "numId"), "val")
            found_level = attr(child(numbering, "ilvl"), "val")
            if num_id is None and found_num is not None:
                num_id, inherited = found_num, index > 0
            if level is None and found_level is not None:
                level = found_level
    if num_id in (None, "0"):
        return None
    return Numbering(num_id, level or "0", inherited)


def _tables(table_elements, ids, spans, diagnostics):
    tables = []
    coordinates = {}
    grid_slot_count = 0
    for table in table_elements:
        table_id = ids[table]
        rows = _owned_descendants(table, "tr", "tbl")
        grid = child(table, "tblGrid")
        widths = tuple(attr(e, "w") for e in children(grid) if word_name(e) == "gridCol") if grid is not None else ()
        physical = []
        headers = []
        # Union-find refers to physical cells. Text never moves into merged-cell copies.
        union = {}
        cell_slots = {}
        previous_row = {}

        def find(key):
            while union[key] != key:
                union[key] = union[union[key]]
                key = union[key]
            return key

        def join(left, right):
            union[find(right)] = find(left)

        def integer(raw, default, source):
            try:
                number = int(raw) if raw is not None else default
                if number < 0 or number > 16384:
                    raise ValueError
                return number
            except ValueError:
                diagnostics.append(Diagnostic("INVALID_GRID_VALUE", f"Uninterpreted grid value: {raw!r}", source))
                return default

        for row_index, row in enumerate(rows):
            row_props = child(row, "trPr")
            header = child(row_props, "tblHeader") if row_props is not None else None
            if header is not None and attr(header, "val") not in ("0", "false", "off"):
                headers.append(ids[row])
            column = integer(value(row, "trPr", "gridBefore"), 0, spans[row])
            current_row = {}
            horizontal_anchor = None
            row_cells = _owned_descendants(row, "tc", "tr")
            for cell in row_cells:
                cell_id = ids[cell]
                props = child(cell, "tcPr")
                width = integer(value(cell, "tcPr", "gridSpan"), 1, spans[cell])
                if width == 0:
                    diagnostics.append(Diagnostic("INVALID_GRID_VALUE", "Zero cell width retained", spans[cell]))
                    width = 1
                vm = child(props, "vMerge") if props is not None else None
                hm = child(props, "hMerge") if props is not None else None
                vertical = (attr(vm, "val") or "continue") if vm is not None else None
                horizontal = (attr(hm, "val") or "continue") if hm is not None else None
                coordinate = CellCoordinate(table_id, row_index, column, width)
                coordinates[cell] = coordinate
                physical.append(PhysicalCell(cell_id, coordinate, vertical, horizontal, spans[cell]))
                union[cell_id] = cell_id
                grid_slot_count += width
                if grid_slot_count > MAX_GRID_SLOTS:
                    raise ExtractionError("Table grid resource limit exceeded")
                slots = {(row_index, c) for c in range(column, column + width)}
                cell_slots[cell_id] = slots
                if widths and column + width > len(widths):
                    diagnostics.append(Diagnostic("GRID_OVERFLOW", "Cell exceeds declared grid; original retained", spans[cell]))
                if horizontal == "restart":
                    horizontal_anchor = cell_id
                elif horizontal == "continue" and horizontal_anchor:
                    join(horizontal_anchor, cell_id)
                else:
                    if horizontal == "continue":
                        diagnostics.append(Diagnostic("ORPHAN_HORIZONTAL_MERGE", "No preceding merge anchor", spans[cell]))
                    horizontal_anchor = None
                if vertical == "continue":
                    targets = {previous_row.get(c) for c in range(column, column + width)}
                    if None in targets or len(targets) != 1:
                        diagnostics.append(Diagnostic("ORPHAN_VERTICAL_MERGE", "No unambiguous preceding merge anchor", spans[cell]))
                    else:
                        target = next(iter(targets))
                        target_columns = {c for r, c in cell_slots[target] if r == row_index - 1}
                        if target_columns != {c for _, c in slots}:
                            diagnostics.append(Diagnostic("NONRECTANGULAR_MERGE", "Vertical merge widths differ", spans[cell]))
                        else:
                            join(target, cell_id)
                if vertical in ("restart", "continue"):
                    current_row.update({c: cell_id for c in range(column, column + width)})
                if vertical not in (None, "restart", "continue") or horizontal not in (None, "restart", "continue"):
                    diagnostics.append(Diagnostic("UNKNOWN_MERGE_VALUE", "Merge value retained without guessing", spans[cell]))
                column += width
            previous_row = current_row
        groups = defaultdict(list)
        for cell in physical:
            groups[find(cell.id)].append(cell.id)
        logical = []
        for member_ids in groups.values():
            slots = tuple(sorted(set().union(*(cell_slots[i] for i in member_ids))))
            anchor = member_ids[0]
            logical.append(LogicalCell(anchor + ":logical", anchor, tuple(member_ids), slots,
                                       len(member_ids) > 1 or len(slots) > 1))
        tables.append(Table(table_id, tuple(ids[row] for row in rows), widths, tuple(headers),
                            tuple(physical), tuple(logical), spans[table]))
    return tuple(tables), coordinates


_YEAR = re.compile(
    r"\b(?P<marker>[1-9]\d?|[IVX]{1,5})(?:[-‑–]?(?:й|ый|ой|ий|го))?\s*(?:год(?:а|у|ом|ы)?|г\.)\s+обучени[яе]",
    re.IGNORECASE,
)
_YEAR_REVERSED = re.compile(r"\bгод\s+обучения\s*[:№–-]?\s*(?P<marker>[1-9]\d?|[IVX]{1,5})\b", re.IGNORECASE)


def extract_bytes(data: bytes, *, converter: LibreOfficeConverter | None = None) -> ExtractedDocument:
    source_format = detect_format(data)
    source_hash = sha256(data).hexdigest()
    document_id = "sha256:" + source_hash
    package = data
    conversion_log = ()
    if source_format == "DOC":
        package, event = (converter or LibreOfficeConverter.discover()).convert(data)
        conversion_log = (event,)
    package_parts = read_package(package)
    parts = tuple(PackagePart(name, raw, sha256(raw).hexdigest()) for name, raw in sorted(package_parts.items()))
    roots = {}
    diagnostics = []
    # Only a part's own order is meaningful; no invented cross-story/page reading order.
    part_names = sorted(package_parts, key=lambda name: (name != "word/document.xml", name))
    for part in part_names:
        if part.endswith((".xml", ".rels")):
            try:
                roots[part] = parse_xml(package_parts[part])
            except ExtractionError as exc:
                if part in ("word/document.xml", "word/styles.xml", "word/numbering.xml"):
                    raise
                diagnostics.append(Diagnostic("OPAQUE_XML_PART", str(exc), SourceSpan("/" + part, ())))
    main = roots["word/document.xml"]
    if word_name(main) != "document":
        raise ExtractionError("Main part has no Word document root")
    nodes, ids, spans = [], {}, {}
    elements = []
    for part, root in roots.items():
        def walk(element, path, parent_id):
            if len(nodes) >= MAX_XML_NODES:
                raise ExtractionError("XML node resource limit exceeded")
            node_id = stable_id(document_id, part, path)
            span = SourceSpan("/" + part, path)
            ids[element], spans[element] = node_id, span
            elements.append(element)
            element_children = children(element)
            child_ids = tuple(stable_id(document_id, part, path + (index,)) for index, _ in enumerate(element_children))
            nodes.append(XmlNode(node_id, parent_id, child_ids, len(nodes), element.tag,
                                 tuple(sorted(element.attrib.items())), element.text, element.tail, span))
            for index, nested in enumerate(element_children):
                walk(nested, path + (index,), node_id)
        walk(root, (), None)
    styles_root = roots.get("word/styles.xml")
    styles = {attr(e, "styleId"): e for e in children(styles_root) if word_name(e) == "style"} if styles_root is not None else {}
    default_style = next((key for key, e in styles.items() if attr(e, "type") == "paragraph" and attr(e, "default") in ("1", "true", "on")), None)
    paragraphs_xml = [e for e in elements if word_name(e) == "p"]
    tables, coordinates = _tables([e for e in elements if word_name(e) == "tbl"], ids, spans, diagnostics)
    fragments = []
    by_owner = defaultdict(list)
    controls = {"tab": "\t", "ptab": "\t", "br": "\n", "cr": "\n", "noBreakHyphen": "\u2011", "softHyphen": "\u00ad"}
    for element in elements:
        # Metadata text is preserved in nodes/parts, not mixed into document content.
        owner = _nearest(element, "p")
        local = word_name(element)
        raw, role = None, "text"
        if local in ("t", "delText", "instrText", "delInstrText"):
            raw, role = element.text or "", local
        elif local in controls and owner is not None and _nearest(element, "r") is not None and _nearest(_nearest(element, "r"), "p") is owner:
            raw, role = controls[local], local
        elif local == "sym" and owner is not None:
            # Font-coded symbols are not guessed as Unicode. XML keeps both font and code.
            raw, role = "\ufffc", "symbol"
            diagnostics.append(Diagnostic("FONT_CODED_SYMBOL", "Font and character code retained in XML", spans[element]))
        elif etree.QName(element).localname == "t" and etree.QName(element).namespace in (
            "http://schemas.openxmlformats.org/drawingml/2006/main",
            "http://schemas.openxmlformats.org/officeDocument/2006/math",
        ):
            raw, role = element.text or "", "drawing_or_math_text"
        elif owner is not None and element.text is not None and element.text.strip():
            raw, role = element.text, "opaque_xml_text"
        if raw is not None:
            source = SourceSpan(spans[element].part_uri, spans[element].element_path, 0, len(raw))
            fragment = TextFragment(ids[element], ids.get(owner), role, raw, normalize_text(raw), source)
            fragments.append(fragment)
            by_owner[ids.get(owner)].append(fragment)
    paragraphs = []
    for element in paragraphs_xml:
        node_id = ids[element]
        owned = by_owner[node_id]
        # Field instructions and deleted text are preserved but explicitly typed. No filtering.
        raw = "".join(f.raw_text for f in owned if f.role != "opaque_xml_text")
        cell = _nearest(element, "tc")
        paragraphs.append(Paragraph(node_id, ids.get(element.getparent()), tuple(f.id for f in owned),
                                    raw, normalize_text(raw), value(element, "pPr", "pStyle"),
                                    _numbering(element, styles, default_style, diagnostics, spans[element]),
                                    coordinates.get(cell), spans[element]))
    known_blocks = {"p", "tbl", "sdt", "sdtContent", "customXml", "sectPr", "tcPr", "trPr", "tblPr", "tblGrid",
                    "bookmarkStart", "bookmarkEnd", "proofErr", "permStart", "permEnd", "commentRangeStart", "commentRangeEnd"}
    containers = {"body", "tc", "sdtContent", "customXml", "txbxContent", "hdr", "ftr", "footnote", "endnote", "comment"}
    unknown = tuple(ids[e] for e in elements if word_name(e.getparent()) in containers
                    and word_name(e) not in known_blocks)
    node_by_id = {node.id: node for node in nodes}
    for node_id in unknown:
        node = node_by_id[node_id]
        diagnostics.append(Diagnostic("UNINTERPRETED_BLOCK", "Block retained with its subtree and original part", node.source))
    compatibility = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    alternatives = tuple(AlternativeBranch(ids[e], ids[e.getparent()], etree.QName(e).localname,
                                          e.get("Requires"), spans[e])
                         for e in elements if e.tag in (f"{{{compatibility}}}Choice", f"{{{compatibility}}}Fallback")
                         and e.getparent() is not None and e.getparent().tag == f"{{{compatibility}}}AlternateContent")
    candidates = []
    for paragraph in paragraphs:
        for pattern in (_YEAR, _YEAR_REVERSED):
            for match in pattern.finditer(paragraph.raw_text):
                candidates.append(YearMarkerCandidate(
                    paragraph.id + f":year:{match.start()}", paragraph.id, match.group(), match.group("marker"),
                    ("explicit_year_of_study_phrase", "candidate_only_no_content_assignment"), 0.8,
                    SourceSpan(paragraph.source.part_uri, paragraph.source.element_path, match.start(), match.end()),
                ))
    return ExtractedDocument("lossless-document/1", document_id, source_format, source_hash,
                             sha256(package).hexdigest(), data, package, parts, tuple(nodes), tuple(fragments),
                             tuple(paragraphs), tables, unknown, alternatives, tuple(candidates), tuple(diagnostics), conversion_log)


def extract_document(path: str | Path, *, converter: LibreOfficeConverter | None = None) -> ExtractedDocument:
    source = Path(path)
    from .formats import MAX_INPUT_BYTES
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise ExtractionError("Input resource limit exceeded")
    return extract_bytes(source.read_bytes(), converter=converter)
