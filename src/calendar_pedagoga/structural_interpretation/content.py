"""Content hierarchy with exclusive text ownership and structural links."""
from __future__ import annotations
from dataclasses import replace
from fractions import Fraction
import re
from .index import number_prefix
from .models import BlockRole, ContentSection, Decision, SourceFragment, TopicSourceBinding
from .years import CONTENT, PURE_YEAR, RESULT

FRAGMENT_LABEL = re.compile(r'^(?:теория|практика|практическ[а-я]*\s+(?:работ|заняти)|учебно\s*-\s*тренировочн[а-я]*(?:\s+и\s+практическ[а-я]*)?\s+занят)', re.I)
EXPLICIT_HEADING = re.compile(r'^\s*(раздел|тема)\s*(?:№\s*)?(?::|\d)', re.I)
DECLARED_HOURS = re.compile(r'\((\d+(?:[.,]\d+)?)\s*час[а-я]*\.?\)', re.I)


def _fragment_kind(p):
    text = p.normalized_text
    if re.search(r'практическ[а-я]*\s+(?:работ|занят)|^практика', text, re.I):
        return 'PRACTICAL_WORK'
    if re.match(r'^теория', text, re.I):
        return 'THEORY_TEXT'
    return 'LIST_ITEM' if p.numbering or number_prefix(text) else 'PARAGRAPH'


def _heading_span(p):
    # A heading and the following sentence in one paragraph have non-overlapping spans.
    if re.match(r'^\s*тема\s*№', p.raw_text, re.I):
        match = re.search(r'[А-Яа-яA-Za-z][.!?](?=\s+\S)', p.raw_text)
        if match:
            return match.end()
    return len(p.raw_text)


def interpret_content(index, regions, tables):
    roles = {b: BlockRole(b, 'UNRESOLVED', (), None,
                         Decision('NEEDS_CONFIRMATION', 0, (index.evidence('UNCLASSIFIED_BLOCK', [b]),)))
             for b in index.block_ids}
    sections, fragments, results = [], [], []
    list_stacks = {}
    for p in index.document.paragraphs:
        if p.source.part_uri != '/word/document.xml':
            roles[p.id] = BlockRole(p.id, 'AUXILIARY', (), None,
                                    Decision('SUPPORTED', 1, (index.evidence('PACKAGE_PART', [p.id], p.source.part_uri),)))
        elif not p.normalized_text:
            roles[p.id] = BlockRole(p.id, 'EMPTY', (), None,
                                    Decision('SUPPORTED', 1, (index.evidence('EMPTY_PARAGRAPH', [p.id]),)))

    def fragment(p, section, years, kind=None, start=0, end=None, target=None, evidence=()):
        end = len(p.raw_text) if end is None else end
        span = replace(p.source, char_start=start, char_end=end)
        ev = evidence or (index.evidence('PARAGRAPH_IN_REGION', [p.id], kind or _fragment_kind(p)),)
        contradictions = tuple(e for e in ev if e.code.endswith('_CONFLICT') or e.code == 'CONFLICTING_YEAR_MARKERS')
        supported = len(years) == 1 and not contradictions
        decision = Decision('SUPPORTED' if supported else 'NEEDS_CONFIRMATION', .97 if supported else .4, ev, contradictions)
        level = int(p.numbering.level) if p.numbering and p.numbering.level.isdigit() else None
        scope = (section.id if section else None, years, p.numbering.num_id if p.numbering else None, kind, ev[0].block_ids)
        stack = list_stacks.setdefault(scope, [])
        while stack and (level is None or stack[-1].list_level >= level):
            stack.pop()
        parent_fragment = stack[-1].id if stack and level is not None else None
        item = SourceFragment(p.id + ':fragment:' + str(start) + ':' + str(end), p.id,
                              section.id if section else None, kind or _fragment_kind(p),
                              p.raw_text[start:end], years, span, decision, parent_fragment, level)
        if level is not None:
            stack.append(item)
        (target if target is not None else fragments).append(item)
        return item

    for region in regions:
        if region.channel not in ('CONTENT', 'RESULTS'):
            continue
        stack = []
        active = None
        for bid in region.block_ids:
            p = index.paragraphs.get(bid)
            if not p or p.cell or not p.normalized_text or any(n.tag.rsplit('}', 1)[-1] in ('Choice', 'Fallback') for n in index.ancestors(p.id)):
                continue
            text = p.normalized_text
            ev = (*region.decision.evidence, index.evidence('REGION_MEMBER', [p.id], region.channel))
            if CONTENT.match(text) or PURE_YEAR.match(text):
                roles[p.id] = BlockRole(p.id, 'STRUCTURAL_HEADING', region.years, region.id,
                                        Decision(region.decision.status, region.decision.confidence, ev))
                continue
            if region.channel == 'RESULTS':
                item = fragment(p, None, region.years, 'EXPECTED_RESULT', target=results, evidence=ev)
                roles[p.id] = BlockRole(p.id, 'EXPECTED_RESULT', region.years, item.id, item.decision)
                continue
            key = index.key(p)
            flags = index.flags(p)
            explicit = EXPLICIT_HEADING.match(text)
            heading = not FRAGMENT_LABEL.match(text) and (explicit or
                       (key and (flags['bold'] or flags['outline'] is not None or DECLARED_HOURS.search(text))) or
                       (flags['bold'] and len(text) < 180 and not text.endswith((';', ':')) and not p.numbering))
            if heading:
                major = bool(explicit and explicit.group(1).lower() == 'раздел')
                local_topic = bool(explicit and explicit.group(1).lower() == 'тема')
                parent = None
                if local_topic and len(key) == 1:
                    parent = next((s for s in reversed(stack) if s[1] == 'MAJOR'), None)
                    if parent:
                        key = parent[0].key + key
                        parent = parent[0]
                if parent is None and key:
                    possible = [s for s, kind in stack if s.key and len(s.key) < len(key) and key[:len(s.key)] == s.key]
                    if possible:
                        parent = max(possible, key=lambda s: len(s.key))
                end = _heading_span(p)
                hours = DECLARED_HOURS.search(p.raw_text[:end])
                decision = Decision('SUPPORTED' if len(region.years) == 1 and (explicit or key) and region.decision.status == 'SUPPORTED' else 'NEEDS_CONFIRMATION',
                                    .96 if explicit or key else .7,
                                    (*ev, index.key_evidence(p), index.evidence('HEADING_STRUCTURE', [p.id],
                                      f'key={key}; style={p.style_id}; numbering={p.numbering}; bold={flags["bold"]}')))
                active = ContentSection(p.id + ':section', p.id, p.raw_text[:end], key, parent.id if parent else None,
                                        region.years, 'NARRATIVE', replace(p.source, char_start=0, char_end=end), decision,
                                        Fraction(hours.group(1).replace(',', '.')) if hours else None)
                sections.append(active)
                if major or (key and len(key) == 1):
                    stack = []
                elif key:
                    stack = [(s, k) for s, k in stack if not s.key or len(s.key) < len(key)]
                stack.append((active, 'MAJOR' if major else 'SECTION'))
                roles[p.id] = BlockRole(p.id, 'CONTENT_HEADING', region.years, active.id, decision)
                if end < len(p.raw_text):
                    fragment(p, active, region.years, 'INLINE_CONTENT', start=end, evidence=ev)
                    roles[p.id] = replace(roles[p.id], role='CONTENT_HEADING_AND_FRAGMENT')
            else:
                item = fragment(p, active, region.years, evidence=ev)
                roles[p.id] = BlockRole(p.id, 'SOURCE_FRAGMENT', region.years, item.id, item.decision)

    for table in tables:
        result_columns = {}
        if table.subtype == 'EXPECTED_RESULTS':
            raw_table = index.tables[table.table_id]
            for row_number in range(min(3, len(raw_table.row_ids))):
                cells = [c for c in raw_table.physical_cells if c.coordinate.row == row_number]
                ps = [p for c in cells for p in index.cell_text(table.table_id, c)]
                if row_number and any(re.fullmatch(r'\d+[.]?', p.normalized_text) for p in ps):
                    break
                for c in cells:
                    headings = [p for p in index.cell_text(table.table_id, c) if re.match(r'^(?:знания|умения|навыки|знать|уметь)\b', p.normalized_text, re.I)]
                    if headings:
                        for col in range(c.coordinate.column, c.coordinate.column + c.coordinate.column_span):
                            result_columns[col] = (row_number, tuple(p.id for p in headings))
        for bid in table.block_ids:
            p = index.paragraphs.get(bid)
            role = table.classification if bid == table.table_id else (
                   'UNRESOLVED' if table.classification == 'UNKNOWN' or len(table.plans) > 1 else table.classification + '_CELL')
            roles[bid] = BlockRole(bid, role, (), table.table_id, table.decision)
            result_header = result_columns.get(p.cell.column) if p and p.cell else None
            if p and result_header and p.cell.row > result_header[0] and p.normalized_text:
                # The table owns its text independently of the preceding narrative results.
                region = next((r for r in regions if bid in r.block_ids), None)
                years = region.years if region else ()
                item = fragment(p, None, years, 'EXPECTED_RESULT_TABLE', target=results,
                                evidence=(*table.decision.evidence, index.evidence('EXPECTED_RESULT_COLUMN',
                                         (*result_header[1], p.id), p.raw_text)))
                roles[bid] = BlockRole(bid, 'EXPECTED_RESULT', years, item.id, item.decision)
        if len(table.plans) != 1:
            continue
        plan = table.plans[0]
        header_ids = {b for c in plan.mapping.columns for b in c.header_block_ids}
        for bid in header_ids:
            roles[bid] = BlockRole(bid, 'PLAN_HEADER', plan.years, plan.id, plan.mapping.decision)
        for row in plan.rows:
            for bid in row.title_block_ids:
                roles[bid] = BlockRole(bid, 'PLAN_' + row.kind, plan.years, row.id, row.decision)
            for role, hour in row.hours:
                for bid in hour.source_ids:
                    roles[bid] = BlockRole(bid, 'PLAN_HOURS', plan.years, plan.id, row.decision)
            if row.embedded_block_ids:
                p = index.paragraphs[row.title_block_ids[0]]
                decision = Decision('SUPPORTED', .99, (index.evidence('SAME_PHYSICAL_TITLE_CELL',
                                    (*row.title_block_ids, *row.embedded_block_ids), 'heading and nested paragraphs'),))
                section = ContentSection(row.id + ':embedded-section', p.id, p.raw_text, row.key, None,
                                         plan.years, 'PLAN_CELL', replace(p.source, char_start=0, char_end=len(p.raw_text)), decision)
                sections.append(section)
                roles[p.id] = BlockRole(p.id, 'PLAN_TOPIC_AND_CONTENT_HEADING', plan.years, section.id, decision)
                for bid in row.embedded_block_ids:
                    p = index.paragraphs[bid]
                    item = fragment(p, section, plan.years, evidence=decision.evidence)
                    roles[bid] = BlockRole(bid, 'SOURCE_FRAGMENT', plan.years, item.id, item.decision)
    return tuple(sections), tuple(fragments), tuple(results), tuple(roles[b] for b in index.block_ids)


def _literal_heading(text):
    text = re.sub(r'^\s*(?:(?:тема|раздел)\s*(?:№\s*)?)?\d+(?:\.\d+)*[.)]?\s*', '', text, flags=re.I)
    text = DECLARED_HOURS.sub('', text)
    return ' '.join(text.lower().replace('ё', 'е').split()).strip(' .:;')


def bind_topics(index, tables, sections):
    bindings = []
    for table in tables:
        for plan in table.plans:
            for row in plan.rows:
                if row.kind not in ('TOPIC', 'SECTION'):
                    continue
                candidates = [s for s in sections if s.years == plan.years and len(s.years) == 1
                              and ((row.key and row.key == s.key) or s.id == row.id + ':embedded-section')]
                evidence = [index.evidence('STRUCTURAL_KEY_AND_YEAR', row.title_block_ids,
                                          f'key={row.key}; years={plan.years}')]
                contradictions = []
                for section in candidates:
                    evidence.append(index.evidence('CANDIDATE_SECTION', [section.heading_block_id], section.title))
                    total = dict(row.hours).get('TOTAL')
                    if total and section.declared_hours is not None and total.value is not None and section.declared_hours != total.value:
                        contradictions.append(index.evidence('PLAN_CONTENT_HOUR_CONFLICT',
                                              (*row.title_block_ids, *total.source_ids, section.heading_block_id),
                                              f'plan={total.value}; content={section.declared_hours}'))
                    if _literal_heading(row.title) != _literal_heading(section.title):
                        contradictions.append(index.evidence('DIFFERENT_LITERAL_HEADINGS',
                                              (*row.title_block_ids, section.heading_block_id),
                                              row.title + ' <> ' + section.title))
                supported = len(candidates) == 1 and not contradictions
                bindings.append(TopicSourceBinding(row.id + ':binding', row.id, tuple(s.id for s in candidates), plan.years,
                                Decision('SUPPORTED' if supported else 'NEEDS_CONFIRMATION', .98 if supported else .4,
                                         tuple(evidence), tuple(contradictions), tuple(s.id for s in candidates) if not supported else ())))
    return tuple(bindings)
