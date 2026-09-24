"""Read-only projection and explicit column review over the stage B contract."""
from dataclasses import replace

from calendar_pedagoga.structural_interpretation.index import DocumentIndex
from calendar_pedagoga.structural_interpretation.models import (
    ColumnMapping, ColumnRole, Decision, StudyPlanCandidate,
)
from calendar_pedagoga.structural_interpretation.tables import _rows, arithmetic
from .models import Boundary, ContentOption, HourIssue, PlanOption, ROLE_LABELS, canonical


def years(model):
    return tuple(sorted({y for source in model.sources
                         for region in source.document.year_regions
                         if region.channel in ('GENERAL', 'PLAN', 'CONTENT') for y in region.years}
                        | {y for source in model.sources for table in source.document.tables
                           for plan in table.plans for y in plan.years}))


def plan_options(model, kind, year):
    result = []
    for table in model.source(kind).document.tables:
        eligible = tuple(p for p in table.plans if year is None or not p.years or year in p.years)
        # Competing interpretations of the same physical table are a column question.
        if len(eligible) > 1 and len({p.mapping.id for p in eligible}) > 1:
            result.append(PlanOption(kind + ':' + table.table_id, kind, table, None))
        else:
            result.extend(PlanOption(kind + ':' + plan.id, kind, table, plan) for plan in eligible)
        if not table.plans and table.classification in ('UNKNOWN', 'STUDY_PLAN', 'REFERENCE'):
            regions = [r for r in model.source(kind).document.year_regions if table.table_id in r.block_ids]
            if year is None or not regions or not regions[0].years or year in regions[0].years:
                result.append(PlanOption(kind + ':' + table.table_id, kind, table, None))
    recognized = [option for option in result if option.plan is not None or option.table.classification == 'STUDY_PLAN']
    return tuple(recognized or result)


def signature(plan):
    if plan is None:
        return None
    # Coordinates, column order and formatting do not create false source conflicts.
    return tuple((r.kind, r.key, r.title, tuple(sorted(
        (role, value.state, value.arithmetic_value) for role, value in r.hours))) for r in plan.rows)


def sources_differ(left, right):
    if any(option.plan is None for option in (*left, *right)):
        return True  # Unknown schemas cannot establish equivalence between sources.
    return {signature(p.plan) for p in left} != {signature(p.plan) for p in right}


def table_preview(model, option):
    index = DocumentIndex(model.source(option.kind).document.source_document)
    table = index.tables[option.table.table_id]
    grid = index.grid(table)
    width = max((c for r, c in grid), default=-1) + 1
    return tuple(tuple('\n'.join(p.raw_text for p in index.cell_text(table.id, grid[r, c]))
                       if (r, c) in grid else '' for c in range(width))
                 for r in range(len(table.row_ids)))


def reviewed_columns(model, option, payload, year):
    """Reuse B row extraction/arithmetic; an overlay never edits the B document."""
    index = DocumentIndex(model.source(option.kind).document.source_document)
    table = index.tables[option.table.table_id]
    grid = index.grid(table)
    width = max((c for r, c in grid), default=-1) + 1
    roles, header_count = payload['roles'], payload['header_count']
    if type(header_count) is not int or not 1 <= header_count < len(table.row_ids):
        raise ValueError('Укажите число строк шапки перед темами.')
    if len(roles) != width or any(role not in ROLE_LABELS for role in roles):
        raise ValueError('Назначение должно быть указано для каждой колонки.')
    used = [role for role in roles if role != 'IGNORE']
    if len(used) != len(set(used)) or not {'TITLE', 'TOTAL'} <= set(used):
        raise ValueError('Название темы и общее число часов обязательны; назначения не должны повторяться.')
    ev = index.evidence('USER_COLUMN_MAPPING', [table.id], canonical(payload))
    decision = Decision('SUPPORTED', 1.0, (ev,))
    columns = []
    for col, role in enumerate(roles):
        if role == 'IGNORE':
            continue
        ids = tuple(dict.fromkeys(p.id for r in range(header_count) if (r, col) in grid
                                  for p in index.cell_text(table.id, grid[r, col])))
        columns.append(ColumnRole(role, col, ids, decision))
    mapping = ColumnMapping(table.id + ':user-mapping', tuple(range(header_count)), tuple(columns),
                            'NUMBER' not in used, decision,
                            tuple(c for c, r in enumerate(roles) if r == 'IGNORE'))
    rows = _rows(index, table, mapping)
    if not any(r.kind in ('TOPIC', 'SECTION') and r.title for r in rows):
        raise ValueError('В выбранных строках нет тем учебного плана.')
    checks, totals = arithmetic(index, table, rows)
    status = 'SUPPORTED' if checks and all(c.decision.status == 'SUPPORTED' for c in checks) else 'CONFLICT'
    return StudyPlanCandidate(mapping.id + ':plan', table.id, (year,), mapping, rows, totals, checks,
                              replace(decision, status=status))


def hour_issues(model, option, plan):
    index = DocumentIndex(model.source(option.kind).document.source_document)
    checks, _ = arithmetic(index, index.tables[plan.table_id], plan.rows)
    issues = []
    rows = {r.id: r for r in plan.rows}
    for check in checks:
        if check.left is not None and check.right is not None and check.left == check.right:
            continue
        label = 'Итог учебного плана'
        if check.kind.startswith('grand:'):
            label = ROLE_LABELS.get(check.kind.split(':')[-1], label) + ': итог'
        elif check.kind.startswith('row:'):
            label = rows[check.kind[4:]].title + ': сумма видов занятий'
        elif check.kind.startswith('section:'):
            row_id, role = check.kind[8:].rsplit(':', 1)
            label = rows[row_id].title + ': ' + ROLE_LABELS.get(role, role)
        elif check.kind.startswith('nonnegative:'):
            label = 'Отрицательное количество часов'
        issues.append(HourIssue(label, check.left, check.right, check.decision.evidence))
    if not checks or not any(r.kind in ('TOPIC', 'SECTION') for r in plan.rows):
        issues.append(HourIssue('Недостаточно данных для проверки часов', None, None, plan.decision.evidence))
    # Unknown row values/alignment cannot be approved just because root totals reconcile.
    for row in plan.rows:
        if row.kind == 'OUTSIDE_HOURS':
            continue
        for role, value in row.hours:
            if value.arithmetic_value is None:
                issues.append(HourIssue(row.title + ': ' + ROLE_LABELS[role], None, None, row.decision.evidence))
    return tuple(issues)


def boundary_options(model, year):
    document = model.program
    positions = {b: i for i, b in enumerate(document.source_document.block_ids)}
    result = []
    for region in document.year_regions:
        if region.channel != 'CONTENT' or (region.years and year not in region.years):
            continue
        ids = tuple(s.id for s in document.content_sections if s.heading_block_id in region.block_ids)
        result.append(Boundary(region.id, region.start_block_id, region.end_block_id_exclusive,
                               region.block_ids, ids, region.decision.evidence))
    return tuple(sorted(result, key=lambda r: positions[r.start]))


def custom_boundary(model, year, start, end):
    document = model.program
    index = DocumentIndex(document.source_document)
    if start not in index.positions or (end is not None and end not in index.positions):
        raise ValueError('Граница должна ссылаться на фрагмент исходной программы.')
    lo, hi = index.positions[start], index.positions[end] if end else len(index.block_ids)
    if lo >= hi:
        raise ValueError('Конец раздела должен находиться после начала.')
    blocks = index.block_ids[lo:hi]
    if any(r.channel == 'CONTENT' and r.years and year not in r.years
           and set(blocks).intersection(r.block_ids) for r in document.year_regions):
        raise ValueError('Граница захватывает содержание другого года обучения.')
    if any(s.years and year not in s.years and s.heading_block_id in blocks for s in document.content_sections):
        raise ValueError('В выбранной области есть раздел другого года обучения.')
    evidence = (index.evidence('USER_CONTENT_BOUNDARY', [start, *([end] if end else [])],
                              'inclusive start; exclusive end; year=' + str(year)),)
    return Boundary('range:' + start + ':' + (end or 'END'), start, end, tuple(blocks),
                    tuple(s.id for s in document.content_sections if s.heading_block_id in blocks), evidence)


def content_options(model, year, boundaries, plan, kind):
    document = model.program
    blocks = {b for boundary in boundaries for b in boundary.block_ids}
    options = []
    for section in document.content_sections:
        embedded = section.origin == 'PLAN_CELL' and (kind != 'EMBEDDED' or any(
            section.id == row.id + ':embedded-section' for row in plan.rows))
        if section.years and year not in section.years:
            continue
        if section.heading_block_id not in blocks and not embedded:
            continue
        fragments = [f for f in document.source_fragments if f.section_id == section.id
                     and (not f.years or year in f.years) and (embedded or f.block_id in blocks)]
        options.append(ContentOption(section.id, section.title,
                       tuple(dict.fromkeys([section.heading_block_id, *(f.block_id for f in fragments)])),
                       section.decision.evidence))
    # A teacher can identify an unrecognized section by a range of original blocks.
    # It is an explicit review selection, not newly generated content or a B rewrite.
    paragraphs = {p.id: p for p in document.source_document.paragraphs}
    for boundary in boundaries:
        if not boundary.section_ids:
            title = next((paragraphs[b].raw_text for b in boundary.block_ids
                          if b in paragraphs and paragraphs[b].normalized_text), '')
            if title:
                options.append(ContentOption(boundary.id, title, boundary.block_ids, boundary.evidence))
    return tuple(options)
