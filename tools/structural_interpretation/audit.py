"""Stage B admission invariants; evidence must resolve to the unchanged stage A graph."""
from collections import Counter, defaultdict
from dataclasses import fields, is_dataclass
from calendar_pedagoga.structural_interpretation.models import Decision, Evidence
from calendar_pedagoga.structural_interpretation.index import DocumentIndex


def audit_structure(document):
    source = document.source_document
    index = DocumentIndex(source)
    roles = {r.block_id: r for r in document.block_roles}
    assert tuple(r.block_id for r in document.block_roles) == source.block_ids, 'A block coverage or order changed'
    assert len(roles) == len(source.block_ids), 'Duplicate block ownership'
    assert set(t.table_id for t in document.tables) == set(index.tables), 'A table disappeared'
    assert len(document.tables) == len(index.tables)
    spans = defaultdict(list)
    sections = {s.id: s for s in document.content_sections}
    fragment_by_id = {f.id: f for f in (*document.source_fragments, *document.expected_results)}
    assert len(fragment_by_id) == len(document.source_fragments) + len(document.expected_results)
    for section in document.content_sections:
        p = index.paragraphs[section.heading_block_id]
        assert section.title == p.raw_text[section.source.char_start:section.source.char_end]
        assert section.source.part_uri == p.source.part_uri and section.source.element_path == p.source.element_path
        spans[p.id].append((section.source.char_start, section.source.char_end))
        if section.parent_id:
            assert section.parent_id in sections and sections[section.parent_id].years == section.years
    for fragment in fragment_by_id.values():
        p = index.paragraphs[fragment.block_id]
        start, end = fragment.source.char_start, fragment.source.char_end
        assert start is not None and end is not None and 0 <= start <= end <= len(p.raw_text)
        assert fragment.raw_text == p.raw_text[start:end], 'Invented or modified content'
        assert fragment.source.part_uri == p.source.part_uri and fragment.source.element_path == p.source.element_path
        spans[p.id].append((start, end))
        assert fragment.years == roles[p.id].years, 'Fragment crosses block year scope'
        if fragment.section_id:
            assert sections[fragment.section_id].years == fragment.years, 'Cross-year source parent'
        if fragment.parent_fragment_id:
            parent = fragment_by_id[fragment.parent_fragment_id]
            assert parent.years == fragment.years and parent.section_id == fragment.section_id
            assert parent.list_level < fragment.list_level
    for bid, intervals in spans.items():
        position = 0
        for start, end in sorted(intervals):
            assert start == position, f'Text gap or duplicate at {bid}'
            position = end
        assert position == len(index.paragraphs[bid].raw_text), 'Trailing content lost'
    for role in document.block_roles:
        if role.role in ('SOURCE_FRAGMENT', 'EXPECTED_RESULT', 'CONTENT_HEADING', 'CONTENT_HEADING_AND_FRAGMENT', 'PLAN_TOPIC_AND_CONTENT_HEADING'):
            assert role.block_id in spans, 'Claimed content has no source-backed entity'
    for region in document.year_regions:
        start = index.positions[region.start_block_id]
        end = index.positions.get(region.end_block_id_exclusive, len(source.block_ids))
        assert all(start <= index.positions[b] < end for b in region.block_ids), 'Invalid region bounds'
    member_counts = Counter(b for r in document.year_regions for b in r.block_ids)
    assert max(member_counts.values(), default=0) <= 1, 'Overlapping selected regions'
    plans = {p.id: p for table in document.tables for p in table.plans}
    rows = {r.id: r for p in plans.values() for r in p.rows}
    for binding in document.bindings:
        assert binding.topic_id in rows
        assert all(sections[s].years == binding.years and len(binding.years) == 1 for s in binding.section_ids)
        if binding.decision.status == 'SUPPORTED':
            assert len(binding.section_ids) == 1 and not binding.decision.contradictions
    for choice in document.plan_choices:
        if choice.accepted_id:
            assert len(choice.candidate_ids) == 1 and len(choice.years) == 1, 'Guessed plan selection'
            plan = plans[choice.accepted_id]
            assert plan.decision.status == 'SUPPORTED'
            assert all(c.decision.status == 'SUPPORTED' and c.left == c.right for c in plan.arithmetic), 'Accepted inconsistent hours'
            table = next(t for t in document.tables if t.table_id == plan.table_id)
            assert table.classification == 'STUDY_PLAN'
        else:
            assert choice.decision.status == 'NEEDS_CONFIRMATION'
    # Traverse interpretation only, not the unchanged multi-megabyte source graph.
    def walk(item):
        if isinstance(item, Decision):
            assert 0 <= item.confidence <= 1
            assert item.evidence, 'A decision has no source evidence'
        if isinstance(item, Evidence):
            assert len(item.block_ids) == len(item.spans) and item.block_ids
            for bid, span in zip(item.block_ids, item.spans):
                node = index.nodes[bid]
                assert span.part_uri == node.source.part_uri and span.element_path == node.source.element_path
        if is_dataclass(item):
            for field in fields(item):
                if field.name != 'source_document':
                    walk(getattr(item, field.name))
        elif isinstance(item, (tuple, list)):
            for child in item:
                walk(child)
    walk(document)
    return {'blocks_a': len(source.block_ids), 'blocks_b': len(roles), 'lost_blocks': 0,
            'duplicate_block_ownership': 0, 'invented_text': 0, 'cross_year_links': 0,
            'sections': len(sections), 'source_fragments': len(document.source_fragments),
            'expected_result_fragments': len(document.expected_results), 'unresolved_blocks': len(document.unresolved)}
