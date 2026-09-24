"""Table schemas and exact hour constraints over canonical cells only."""
from __future__ import annotations
from dataclasses import replace
from fractions import Fraction
import re
from .index import number_prefix
from .models import (ArithmeticCheck, ColumnMapping, ColumnRole, Decision, HourValue,
                     PlanRow, StudyPlanCandidate, TableInterpretation)
from .years import PLAN, years_in

HOUR_ROLES = ('TOTAL', 'THEORY', 'TRAINING', 'PRACTICE')
TOTAL_ROW = re.compile(r'^\s*(?:итого\b|всего(?:\s+часов|\s*:|\s*$))', re.I)
NUMBER = re.compile(r'^[+-]?\d+(?:[.,]\d+)?(?:/\d+)?$')


def header_role(text):
    text = text.lower().replace('ё', 'е')
    specific = [role for role, pattern in [('TRAINING', r'учебно\s*[-–]?\s*трениров'),
                ('THEORY', r'теор|лекци'), ('PRACTICE', r'практ')] if re.search(pattern, text)]
    if len(specific) > 1:
        return 'UNRESOLVED'
    if re.search(r'учебно\s*[-–]?\s*трениров', text):
        return 'TRAINING'
    if re.search(r'теор|лекци', text):
        return 'THEORY'
    if re.search(r'практ', text):
        return 'PRACTICE'
    if re.search(r'всего|общее.*час|кол(?:ичество|-?во|\.)\s*час', text):
        return 'TOTAL'
    if re.search(r'тем[аы]|раздел|наименован|назван', text):
        return 'TITLE'
    if re.match(r'^\s*(?:№|номер|n\s*[пp]/[пp])', text):
        return 'NUMBER'
    return None


def value(paragraphs):
    raw = '\n'.join(p.raw_text for p in paragraphs)
    text = ' '.join(raw.split())
    ids = tuple(p.id for p in paragraphs)
    spans = tuple(p.source for p in paragraphs)
    if not text:
        return HourValue(raw, 'EMPTY', None, ids, spans)
    if re.fullmatch(r'[-–—−]', text):
        return HourValue(raw, 'DASH', None, ids, spans)
    if NUMBER.fullmatch(text):
        try:
            return HourValue(raw, 'NUMBER', Fraction(text.replace(',', '.')), ids, spans)
        except (ValueError, ZeroDivisionError):
            pass
    return HourValue(raw, 'TEXT', None, ids, spans)


def mappings(index, table):
    grid = index.grid(table)
    width = max((col for row, col in grid), default=-1) + 1
    candidates = []
    paths = {col: [] for col in range(width)}
    for row in range(min(len(table.row_ids), 6)):
        # A row containing actual numeric allocations ends the header search.
        cells = {grid[row, col].id: grid[row, col] for col in range(width) if (row, col) in grid}
        texts = [index.cell_text(table.id, c) for c in cells.values()]
        if row and any(value([p]).state == 'NUMBER' for ps in texts for p in ps):
            break
        for col in range(width):
            cell = grid.get((row, col))
            if cell:
                for p in index.cell_text(table.id, cell):
                    if p.id not in [x.id for x in paths[col]] and p.normalized_text:
                        paths[col].append(p)
        columns = []
        for col, ps in paths.items():
            recognized = [(p, header_role(p.normalized_text)) for p in ps if header_role(p.normalized_text)]
            if not recognized:
                continue
            p, role = recognized[-1]
            evidence = index.evidence('HEADER_PATH', [p.id for p in ps], ' / '.join(p.raw_text for p in ps))
            columns.append(ColumnRole(role, col, tuple(p.id for p in ps),
                           Decision('NEEDS_CONFIRMATION' if role == 'UNRESOLVED' else 'SUPPORTED',
                                    .4 if role == 'UNRESOLVED' else .97, (evidence,),
                                    alternatives=('THEORY', 'TRAINING', 'PRACTICE') if role == 'UNRESOLVED' else ())))
        roles = [c.role for c in columns]
        if 'TITLE' not in roles or 'TOTAL' not in roles:
            continue
        duplicate_roles = {r for r in roles if roles.count(r) > 1}
        complete = not duplicate_roles and 'UNRESOLVED' not in roles
        signature = tuple((c.role, c.column) for c in columns)
        # Refinement of the same merged header is one mapping, not competing plans.
        if candidates and tuple((c.role, c.column) for c in candidates[-1].columns) == signature:
            candidates[-1] = replace(candidates[-1], header_rows=tuple(range(row + 1)))
            continue
        ev = tuple(e for c in columns for e in c.decision.evidence)
        status = 'SUPPORTED' if complete else 'NEEDS_CONFIRMATION'
        title_column = next(c.column for c in columns if c.role == 'TITLE')
        combined = 'NUMBER' not in roles and any(index.key(p) for cell in table.physical_cells
                   if cell.coordinate.row > row and cell.coordinate.column == title_column
                   for p in index.cell_text(table.id, cell))
        candidate = ColumnMapping(table.id + ':mapping:' + str(row), tuple(range(row + 1)), tuple(columns),
                                  combined, Decision(status, .97 if complete else .4, ev,
                                  alternatives=tuple(sorted(duplicate_roles))),
                                  tuple(c for c in range(width) if c not in {item.column for item in columns}))
        # A later header level with additional distinct semantics refines its parent.
        if candidates and set((c.role, c.column) for c in candidates[-1].columns) < set(signature):
            candidates = [candidate]
        elif candidates and not complete:
            candidates.append(candidate)
        elif candidates and candidates[-1].decision.status != 'SUPPORTED':
            candidates = [candidate]
        else:
            candidates.append(candidate)
    return tuple(c for c in candidates if c.decision.status == 'SUPPORTED') or tuple(candidates)


def infer_zeros(index, hours):
    hours = dict(hours)
    total = hours.get('TOTAL')
    parts = [r for r in HOUR_ROLES[1:] if r in hours]
    if total and total.value is not None and len(parts) >= 2:
        known = [hours[r].value for r in parts if hours[r].value is not None]
        unknown = [r for r in parts if hours[r].state in ('EMPTY', 'DASH')]
        if unknown and len(known) + len(unknown) == len(parts) and sum(known, Fraction()) == total.value:
            ids = [b for v in hours.values() for b in v.source_ids]
            ev = index.evidence('ZERO_RESIDUAL_NONNEGATIVE_HOURS', ids,
                                'total equals all known components; each missing nonnegative component must be zero')
            for role in unknown:
                hours[role] = replace(hours[role], derived_value=Fraction(0), derivation=(ev,))
    return tuple((r, hours[r]) for r in HOUR_ROLES if r in hours)


def _rows(index, table, mapping):
    columns = {c.role: c.column for c in mapping.columns}
    grid = index.grid(table)
    rows = []
    for row in range(max(mapping.header_rows) + 1, len(table.row_ids)):
        def ps(role):
            cell = grid.get((row, columns.get(role, -1)))
            return index.cell_text(table.id, cell) if cell else ()
        title_ps = [p for p in ps('TITLE') if p.normalized_text]
        number_ps = [p for p in ps('NUMBER') if p.normalized_text]
        all_ids = tuple(n.id for n in index.descendants(table.row_ids[row]) if n.id in index.positions)
        if not title_ps:
            if all(not index.paragraphs[b].normalized_text for b in all_ids if b in index.paragraphs):
                continue
            title_ps = list(ps('TITLE'))
        title = '\n'.join(p.normalized_text for p in title_ps)
        raw_hours = {role: ps(role) for role in HOUR_ROLES if role in columns}
        vectors = {role: [p for p in paragraphs if p.normalized_text] for role, paragraphs in raw_hours.items()}
        numeric_counts = {r: sum(value([p]).state in ('NUMBER', 'DASH') for p in pp) for r, pp in vectors.items()}
        stacked = len(title_ps) > 1 and max(numeric_counts.values(), default=0) > 1
        outside = any(re.search(r'вне\s+сетки\s+часов', p.normalized_text, re.I)
                      for paragraphs in raw_hours.values() for p in paragraphs)
        count = len(title_ps) if stacked else 1
        for ordinal in range(count):
            selected = [title_ps[ordinal]] if stacked else title_ps[:1]
            row_title = selected[0].normalized_text if selected else ''
            key = number_prefix(number_ps[0].normalized_text) if number_ps else ()
            if not key and selected:
                key = index.key(selected[0])
            kind = 'TOTAL' if TOTAL_ROW.match(title) else 'OUTSIDE_HOURS' if outside else 'TOPIC'
            if stacked and ordinal == 0:
                kind = 'SECTION'
            row_id = table.id + ':row:' + str(row) + ':' + str(ordinal)
            evidence = [index.evidence('PHYSICAL_ROW', all_ids, f'row={row}; paragraph={ordinal}')]
            if selected:
                evidence.append(index.key_evidence(selected[0]))
            if stacked:
                evidence.append(index.evidence('STACKED_PARAGRAPHS', all_ids, 'parallel ordered title/hour paragraphs'))
            hours = []
            ambiguous = []
            for role, paragraphs in raw_hours.items():
                if not stacked:
                    item = value(paragraphs)
                elif len(vectors[role]) == count:
                    item = value([vectors[role][ordinal]])
                elif ordinal == 0 and vectors[role]:
                    # Parent allocation is the first entry; nested alignment remains unresolved.
                    item = value(vectors[role][:1])
                else:
                    item = replace(value(paragraphs), state='UNRESOLVED', value=None)
                    ambiguous.append(role)
                hours.append((role, item))
            hours = infer_zeros(index, hours)
            source = selected[0].source if selected else index.nodes[table.row_ids[row]].source
            embedded = tuple(p.id for p in title_ps[1:]) if not stacked else ()
            # A multi-paragraph title with a single allocation is preserved as one title + nested content.
            parent_id = rows[-ordinal].id if stacked and ordinal else None
            decision = Decision('NEEDS_CONFIRMATION' if ambiguous else 'SUPPORTED', .45 if ambiguous else .95,
                                tuple(evidence), alternatives=tuple('align:' + r for r in ambiguous))
            rows.append(PlanRow(row_id, row, kind, key, row_title, tuple(p.id for p in selected), parent_id,
                                hours, embedded, source, decision))
    # Explicit numeric ancestry (not text similarity) determines section/topic aggregation.
    by_key = {}
    for i, row in enumerate(rows):
        if not row.key or row.kind in ('TOTAL', 'OUTSIDE_HOURS'):
            continue
        parents = [p for key, entries in by_key.items() if len(key) < len(row.key) and row.key[:len(key)] == key for p in entries]
        if parents and row.parent_id is None:
            depth = max(len(p.key) for p in parents)
            closest = [p for p in parents if len(p.key) == depth]
            if len(closest) == 1:
                rows[i] = replace(row, parent_id=closest[0].id)
            else:
                rows[i] = replace(row, decision=replace(row.decision, status='NEEDS_CONFIRMATION', confidence=.4,
                                  alternatives=tuple(p.id for p in closest)))
        by_key.setdefault(row.key, []).append(rows[i])
    parent_ids = {r.parent_id for r in rows if r.parent_id}
    return tuple(replace(r, kind='SECTION') if r.id in parent_ids and r.kind == 'TOPIC' else r for r in rows)


def arithmetic(index, table, rows):
    checks = []
    totals = [r for r in rows if r.kind == 'TOTAL']

    def check(name, left, right, ids):
        status = 'NEEDS_CONFIRMATION' if left is None or right is None else 'SUPPORTED' if left == right else 'CONFLICT'
        ev = index.evidence('EXACT_HOUR_EQUATION', ids, f'{name}: {left} = {right}')
        checks.append(ArithmeticCheck(table.id + ':' + name, name, left, right,
                                     Decision(status, 1.0 if status != 'NEEDS_CONFIRMATION' else .3, (ev,),
                                              (ev,) if status == 'CONFLICT' else ())))

    def amount(row, role):
        return dict(row.hours).get(role)

    def sum_rows(items, role):
        values = [amount(r, role).arithmetic_value if amount(r, role) else None for r in items]
        return sum(values, Fraction()) if values and all(v is not None for v in values) else None

    for row in rows:
        if row.kind == 'OUTSIDE_HOURS':
            continue
        hours = dict(row.hours)
        components = [v.arithmetic_value for r, v in hours.items() if r != 'TOTAL']
        ids = [b for v in hours.values() for b in v.source_ids]
        if 'TOTAL' in hours and components:
            check('row:' + row.id, hours['TOTAL'].arithmetic_value,
                  sum(components, Fraction()) if all(v is not None for v in components) else None, ids)
        for role, item in hours.items():
            if item.arithmetic_value is not None and item.arithmetic_value < 0:
                check('nonnegative:' + row.id + role, item.arithmetic_value, Fraction(), ids)
        children = [r for r in rows if r.parent_id == row.id and r.kind != 'OUTSIDE_HOURS']
        if children:
            for role, item in hours.items():
                check('section:' + row.id + ':' + role, item.arithmetic_value, sum_rows(children, role),
                      [b for r in (row, *children) for b in (*r.title_block_ids, *(i for k, v in r.hours if k == role for i in v.source_ids))])
    if len(totals) == 1:
        root_rows = [r for r in rows if not r.parent_id and r.kind not in ('TOTAL', 'OUTSIDE_HOURS')]
        for role, item in totals[0].hours:
            check('grand:' + role, item.arithmetic_value, sum_rows(root_rows, role),
                  [b for r in (*root_rows, totals[0]) for b in (*r.title_block_ids, *(i for k, v in r.hours if k == role for i in v.source_ids))])
    else:
        check('unique-total-row', None, Fraction(1), [table.id])
    return tuple(checks), totals[0].hours if len(totals) == 1 else ()


def interpret_tables(index, regions):
    scope = {b: r for r in regions for b in r.block_ids}
    result = []
    for table in index.document.tables:
        preceding = index.preceding(table.id)
        caption = '\n'.join(p.normalized_text for p in preceding)
        maps = mappings(index, table)
        header_end = max((max(m.header_rows) for m in maps), default=1)
        first_ps = [p for c in table.physical_cells if c.coordinate.row <= header_end for p in index.cell_text(table.id, c)]
        header = ' / '.join(p.normalized_text for p in first_ps)
        ev = (index.evidence('TABLE_HEADER_AND_CONTEXT', [table.id, *(p.id for p in (*preceding, *first_ps))], caption + '\n' + header),)
        class_name, subtype = 'UNKNOWN', None
        plans = []
        region = scope.get(table.id)
        years = region.years if region else ()
        if region:
            ev += region.decision.evidence
        norm_header = bool(re.search(r'физические\s+качества|мальчики|девочки|^тест$|\bзнания\b.*\bумения|показател|критери|уров(?:ни|ень)\s+', header, re.I))
        norm_caption = bool(region and region.channel == 'ASSESSMENT') or bool(re.search(r'контрольн[а-я]*\s+норматив|диагностическ|критери[а-я]*\s+(?:оценки|показателей)', caption, re.I))
        reference_header = bool(re.search(r'дидактич|формы\s+(?:работы|занятий)|деятельность.*(?:педагога|учителя|преподавателя|учащихся)|паспорт|тип\s+программы|год\s+разработки', header, re.I))
        summary = len(set(y for p in first_ps for y in years_in(p.normalized_text.replace('обуч.', 'обучения')))) > 1
        for mapping in maps:
            roles = {c.role for c in mapping.columns}
            pedagogical_schema = 'TITLE' in roles and 'TOTAL' in roles and (len(roles & set(HOUR_ROLES[1:])) >= 2 or any(PLAN.match(p.normalized_text) for p in preceding))
            if mapping.decision.status != 'SUPPORTED' or not pedagogical_schema:
                continue
            rows = _rows(index, table, mapping)
            checks, totals = arithmetic(index, table, rows)
            if not any(r.kind in ('TOPIC', 'SECTION') for r in rows):
                continue
            statuses = [c.decision.status for c in checks] + [r.decision.status for r in rows]
            status = 'CONFLICT' if 'CONFLICT' in statuses else 'NEEDS_CONFIRMATION' if 'NEEDS_CONFIRMATION' in statuses or len(years) != 1 else 'SUPPORTED'
            contradiction = tuple(e for c in checks for e in c.decision.contradictions)
            if region and region.decision.contradictions:
                contradiction += region.decision.contradictions
                if status == 'SUPPORTED':
                    status = 'NEEDS_CONFIRMATION'
            if norm_header or norm_caption:
                status = 'NEEDS_CONFIRMATION'
                contradiction += (index.evidence('ASSESSMENT_SCHEMA_CONFLICT', [table.id], header),)
            plans.append(StudyPlanCandidate(mapping.id + ':plan', table.id, years, mapping, rows, totals, checks,
                                            Decision(status, .98 if status == 'SUPPORTED' else .5, ev, contradiction)))
        if plans:
            class_name = 'STUDY_PLAN'
            subtype = 'VARIANT' if re.search(r'вариативн', caption, re.I) else 'DETAILED'
        elif norm_header or norm_caption:
            class_name = 'NORMATIVE_CONTROL'
            subtype = 'EXPECTED_RESULTS' if re.search(r'знания.*умения', header, re.I) and not re.search(r'показатели|критерии|уровень', header, re.I) else 'ASSESSMENT'
        elif summary:
            class_name, subtype = 'REFERENCE', 'MULTI_YEAR_SUMMARY'
        elif reference_header or re.search(r'паспорт|методическ.*обеспечение', caption, re.I):
            class_name, subtype = 'REFERENCE', 'SUPPORT'
        elif result and result[-1].classification == 'REFERENCE' and not caption.strip() and table.grid_widths == index.tables[result[-1].table_id].grid_widths:
            class_name, subtype = 'REFERENCE', 'CONTINUATION_CANDIDATE'
            ev += (index.evidence('POSSIBLE_REFERENCE_CONTINUATION', [result[-1].table_id, table.id],
                                  'same column widths and no intervening text; header absent'),)
        status = 'SUPPORTED' if class_name != 'UNKNOWN' and subtype != 'CONTINUATION_CANDIDATE' else 'NEEDS_CONFIRMATION'
        # Classification and plan validity are independent: an invalid plan is still a plan candidate.
        if plans and any(p.decision.contradictions for p in plans):
            status = 'NEEDS_CONFIRMATION'
        if plans and (norm_header or norm_caption):
            class_name, subtype = 'UNKNOWN', 'CONFLICTING_SCHEMAS'
        result.append(TableInterpretation(table.id, class_name, subtype, Decision(status, .95 if status == 'SUPPORTED' else .4, ev,
                                          tuple(e for p in plans for e in p.decision.contradictions), alternatives=('REFERENCE', 'NORMATIVE_CONTROL', 'STUDY_PLAN') if class_name == 'UNKNOWN' else ('REFERENCE', 'UNKNOWN') if subtype == 'CONTINUATION_CANDIDATE' else ()),
                                          maps, tuple(plans), index.table_blocks(table)))
    return tuple(result)
