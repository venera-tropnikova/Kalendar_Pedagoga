"""Independent XML inventory and invariant checks (stdlib ET, not reader helpers)."""
from __future__ import annotations
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from io import BytesIO
import json
import re
import xml.etree.ElementTree as ET
import zipfile

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
S = '{http://purl.oclc.org/ooxml/wordprocessingml/main}'


def inventory(package):
    with zipfile.ZipFile(BytesIO(package)) as z:
        parts = {n: z.read(n) for n in z.namelist() if not n.endswith('/')}
    nodes = []
    parents = {}
    for part in sorted(parts, key=lambda n: (n != 'word/document.xml', n)):
        if not part.endswith(('.xml', '.rels')):
            continue
        try:
            root = ET.fromstring(parts[part])
        except ET.ParseError:
            continue
        stack = [(root, (), None)]
        while stack:
            e, path, parent = stack.pop()
            address = ('/' + part, path)
            nodes.append((address, e))
            parents[address] = parent
            stack.extend((c, path + (i,), address) for i, c in reversed(list(enumerate(e))))
    return parts, nodes, parents


def structure_digest(document):
    """Excludes provenance hashes/log timing; keeps every structural/content decision."""
    address = {n.id: (n.source.part_uri, n.source.element_path) for n in document.nodes}
    def cell(c):
        return (address[c.id], c.coordinate.row, c.coordinate.column, c.coordinate.column_span,
                c.vertical_merge, c.horizontal_merge)
    payload = {
        'nodes': [(address[n.id], address.get(n.parent_id), n.tag, n.attributes, n.text, n.tail) for n in document.nodes
                  if n.source.part_uri.startswith('/word/')],
        'paragraphs': [(address[p.id], p.raw_text, p.normalized_text, p.style_id, asdict(p.numbering) if p.numbering else None) for p in document.paragraphs],
        'tables': [(address[t.id], [cell(c) for c in t.physical_cells],
                    [(tuple(address[i] for i in c.physical_cell_ids), c.grid_slots) for c in t.logical_cells]) for t in document.tables],
        'years': [(address[y.paragraph_id], y.raw_text, y.marker, y.source.char_start, y.source.char_end) for y in document.year_candidates],
    }
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def audit(document):
    parts, expected, parents = inventory(document.package_bytes)
    actual = {(n.source.part_uri, n.source.element_path): n for n in document.nodes}
    assert len(actual) == len(document.nodes), 'duplicate node address'
    assert len({n.id for n in document.nodes}) == len(document.nodes), 'duplicate node id'
    assert {p.name: p.data for p in document.parts} == parts, 'package part loss/change'
    for part in document.parts:
        assert sha256(part.data).hexdigest() == part.sha256
    expected_addresses = [a for a, _ in expected]
    assert list(actual) == expected_addresses, 'missing/extra/reordered XML node'
    id_to_address = {n.id: a for a, n in actual.items()}
    expected_map = dict(expected)
    for address, e in expected:
        n = actual[address]
        assert (n.tag, dict(n.attributes), n.text, n.tail) == (e.tag, e.attrib, e.text, e.tail), address
        assert id_to_address.get(n.parent_id) == parents[address], address
        assert tuple(id_to_address[i] for i in n.child_ids) == tuple((address[0], address[1] + (i,)) for i in range(len(e)))
    expected_paragraphs = [a for a, e in expected if e.tag in (W+'p', S+'p')]
    expected_tables = [a for a, e in expected if e.tag in (W+'tbl', S+'tbl')]
    assert [(p.source.part_uri, p.source.element_path) for p in document.paragraphs] == expected_paragraphs
    assert [(t.source.part_uri, t.source.element_path) for t in document.tables] == expected_tables
    by_id = {f.id: f for f in document.fragments}
    assert len(by_id) == len(document.fragments), 'duplicate text atom id'
    owned = []
    for p in document.paragraphs:
        assert p.normalized_text == re.sub(r'\s+', ' ', p.raw_text).strip()
        owned.extend(p.fragment_ids)
        assert all(by_id[i].owner_id == p.id for i in p.fragment_ids)
        assert p.raw_text == ''.join(by_id[i].raw_text for i in p.fragment_ids if by_id[i].role != 'opaque_xml_text')
    assert len(owned) == len(set(owned)), 'nested text duplicated in parent'
    assert set(owned) == {f.id for f in document.fragments if f.owner_id is not None}
    # Independently count original text atoms and enforce nearest-paragraph ownership.
    text_addresses = [a for a, e in expected if e.tag in tuple(ns+tag for ns in (W,S) for tag in ('t','delText','instrText','delInstrText'))]
    actual_text = [(f.source.part_uri, f.source.element_path) for f in document.fragments if f.role in ('t','delText','instrText','delInstrText')]
    assert actual_text == text_addresses, 'lost/duplicated Word text atom'
    for f in document.fragments:
        address = (f.source.part_uri, f.source.element_path)
        owner = parents[address]
        while owner is not None and expected_map[owner].tag not in (W+'p', S+'p'):
            owner = parents[owner]
        assert id_to_address.get(f.owner_id) == owner
        if f.role in ('t','delText','instrText','delInstrText'):
            assert f.raw_text == (expected_map[address].text or '')
    all_cells = [c for t in document.tables for c in t.physical_cells]
    assert Counter((c.source.part_uri,c.source.element_path) for c in all_cells) == Counter(a for a,e in expected if e.tag in (W+'tc',S+'tc'))

    for table in document.tables:
        cell_ids = [c.id for c in table.physical_cells]
        members = [i for c in table.logical_cells for i in c.physical_cell_ids]
        assert Counter(members) == Counter(cell_ids), 'physical cell lost/duplicated by merge'
        seen_slots = set()
        for logical in table.logical_cells:
            assert logical.anchor_id == logical.physical_cell_ids[0]
            slots = set(logical.grid_slots)
            assert not slots & seen_slots, 'overlapping logical cells'
            seen_slots |= slots
            expected_slots = set()
            for c in table.physical_cells:
                if c.id in logical.physical_cell_ids:
                    expected_slots |= {(c.coordinate.row,j) for j in range(c.coordinate.column,c.coordinate.column+c.coordinate.column_span)}
            assert slots == expected_slots
    nonempty = Counter(p.raw_text for p in document.paragraphs if p.raw_text.strip())
    for text, count in nonempty.items():
        if count > 1:
            assert len({p.id for p in document.paragraphs if p.raw_text == text}) == count
    for candidate in document.year_candidates:
        p = next(p for p in document.paragraphs if p.id == candidate.paragraph_id)
        assert p.raw_text[candidate.source.char_start:candidate.source.char_end] == candidate.raw_text
    assert not any(hasattr(p, name) for p in document.paragraphs for name in ('study_year','year_scope','assigned_year'))
    return {'unexplained_losses': 0, 'unexplained_duplicates': 0,
            'repeated_nonempty_paragraphs': sum(n for n in nonempty.values() if n > 1),
            'paragraphs_by_part': dict(Counter(p.source.part_uri for p in document.paragraphs)),
            'structure_sha256': structure_digest(document)}
