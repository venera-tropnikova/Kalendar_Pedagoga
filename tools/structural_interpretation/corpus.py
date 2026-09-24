"""Source-pinned real corpus gate and reviewable evidence packet."""
from __future__ import annotations
from dataclasses import fields, is_dataclass
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
from calendar_pedagoga.structural_interpretation.index import DocumentIndex
from .audit import audit_structure
from .source_contracts import SOURCE_CONTRACTS


def encode(item):
    if is_dataclass(item):
        return {f.name: encode(getattr(item, f.name)) for f in fields(item) if f.name != 'source_document'}
    if isinstance(item, Fraction):
        return {'numerator': item.numerator, 'denominator': item.denominator}
    if isinstance(item, (list, tuple)):
        return [encode(i) for i in item]
    if isinstance(item, dict):
        return {k: encode(v) for k, v in item.items()}
    return item


def verify_source_contract(name, a, b):
    contract = SOURCE_CONTRACTS[name]
    index = DocumentIndex(a)
    assert [t.classification for t in b.tables] == contract['classes'], 'Table classification differs from reviewed source'
    expected = contract['content_bounds']
    actual = [(r.years[0], index.paragraph_indices[r.start_block_id], index.paragraph_indices[r.end_block_id_exclusive])
              for r in b.year_regions if r.channel == 'CONTENT' and len(r.years) == 1]
    assert actual == expected, 'Content year boundary differs from source'
    role_map = {r.block_id: r for r in b.block_roles}
    for year, start, end in expected:
        for p in index.main_paragraphs[start:end]:
            assert role_map[p.id].role != 'UNRESOLVED', 'Known content left uninterpreted'
            if p.normalized_text:
                assert role_map[p.id].years == (year,), 'Foreign year in content region'
        headings = [s for s in b.content_sections if s.origin == 'NARRATIVE' and s.years == (year,)]
        assert headings and all(start <= index.paragraph_indices[s.heading_block_id] < end for s in headings)
        fragments = [f for f in b.source_fragments if f.years == (year,) and index.paragraphs[f.block_id].cell is None]
        assert fragments and all(start <= index.paragraph_indices[f.block_id] < end for f in fragments)
    verified_plans = []
    for ti, year, root_rows, total_row, columns, declared, explicit_sums in contract['plans']:
        table = a.tables[ti]
        assert len(b.tables[ti].plans) == 1, 'Unexpected mapping ambiguity'
        candidate = b.tables[ti].plans[0]
        assert candidate.years == (year,)
        assert {c.role: c.column for c in candidate.mapping.columns if c.role in columns} == columns
        def source_number(row, col):
            cells = [c for c in table.physical_cells if c.coordinate.row == row and c.coordinate.column == col]
            assert len(cells) == 1
            paragraphs = [p for p in index.cell_text(table.id, cells[0]) if p.normalized_text]
            if not paragraphs or paragraphs[0].normalized_text in ('-', '–', '—'):
                return None
            return Decimal(paragraphs[0].normalized_text.replace(',', '.'))
        for (role, col), printed, explicit in zip(columns.items(), declared, explicit_sums):
            printed_value = source_number(total_row, col)
            summed = sum((source_number(r, col) or Decimal(0) for r in root_rows), Decimal(0))
            assert printed_value == Decimal(printed), 'Printed source total changed'
            assert summed == Decimal(explicit), 'Source row sum changed'
            assert dict(candidate.totals)[role].value == Fraction(printed_value), 'Interpreter rewrote printed hours'
            grand = next(c for c in candidate.arithmetic if c.kind == 'grand:' + role)
            assert grand.left == Fraction(printed_value) and grand.right == Fraction(summed), 'Interpreter miscounted section totals'
        # Every title paragraph is represented once as a topic heading or nested source.
        title_col = next(c.column for c in candidate.mapping.columns if c.role == 'TITLE')
        source_titles = {p.id for c in table.physical_cells if c.coordinate.row > max(candidate.mapping.header_rows)
                         and c.coordinate.column <= title_col < c.coordinate.column + c.coordinate.column_span for p in index.cell_text(table.id, c) if p.normalized_text}
        mapped_titles = [bid for r in candidate.rows for bid in (*r.title_block_ids, *r.embedded_block_ids)]
        assert len(mapped_titles) == len(set(mapped_titles)), 'Title paragraph collapsed or duplicated'
        assert set(mapped_titles) == source_titles, 'Nested table content lost'
        # Original nonempty hour paragraphs remain referenced even if their alignment is ambiguous.
        for role, col in columns.items():
            source_ids = {p.id for c in table.physical_cells if c.coordinate.row > max(candidate.mapping.header_rows)
                          and c.coordinate.column <= col < c.coordinate.column + c.coordinate.column_span for p in index.cell_text(table.id, c) if p.normalized_text}
            refs = {bid for r in candidate.rows for name, v in r.hours if name == role for bid in v.source_ids}
            assert source_ids <= refs, 'Hour paragraph lost'
        verified_plans.append({'table_index': ti, 'year': year, 'printed_totals': declared,
                               'explicit_row_sums': explicit_sums, 'decision': candidate.decision.status})
    if name == 'orientation':
        assert all(c.accepted_id for c in b.plan_choices)
        conflict = [e for e in b.conflicts if e.code == 'PLAN_CONTENT_HOUR_CONFLICT']
        assert len(conflict) == 1 and conflict[0].observed == 'plan=39; content=33'
        assert index.main_paragraphs[435].id in conflict[0].block_ids
    if name == 'nature':
        assert [c.accepted_id is not None for c in b.plan_choices] == [True, False, False]
        embedded = [f for f in b.source_fragments if index.paragraphs[f.block_id].cell]
        assert len(embedded) == 23 and all(f.years == (3,) for f in embedded)
        assert {index.paragraph_indices[f.block_id] for f in embedded} == set(range(686,693)) | set(range(698,704)) | set(range(709,715)) | set(range(720,724))
    if name == 'climb':
        assert len(b.plan_choices) == 1 and len(b.plan_choices[0].candidate_ids) == 2 and b.plan_choices[0].accepted_id is None
    if name == 'key':
        assert b.plan_choices[0].accepted_id is None
        assert any(v.state == 'UNRESOLVED' for r in b.tables[1].plans[0].rows for _, v in r.hours)
    return {'source_boundaries': expected, 'source_tables': len(a.tables), 'source_plan_checks': verified_plans}


def semantic_shape(b):
    index = DocumentIndex(b.source_document)
    return {
        'regions': [(r.channel, r.years, index.positions[r.start_block_id], index.positions.get(r.end_block_id_exclusive)) for r in b.year_regions],
        'tables': [(t.classification, t.subtype, [(p.years, p.decision.status,
                    [(c.role, c.column) for c in p.mapping.columns],
                    [(r.kind, r.key, [(k, v.state, v.value, v.derived_value) for k, v in r.hours]) for r in p.rows]) for p in t.plans]) for t in b.tables],
        'sections': [(s.years, s.key, s.origin, s.decision.status) for s in b.content_sections],
        'fragments': [(f.years, f.kind, f.list_level) for f in b.source_fragments],
        'roles': [(r.role, r.years) for r in b.block_roles],
        'bindings': [(x.years, x.decision.status, len(x.section_ids)) for x in b.bindings],
        'choices': [(x.years, x.decision.status, len(x.candidate_ids)) for x in b.plan_choices],
    }


def report_document(name, path, a, b):
    index = DocumentIndex(a)
    checks = audit_structure(b)
    oracle = verify_source_contract(name, a, b)
    refs = {bid: {'part': index.nodes[bid].source.part_uri, 'path': index.nodes[bid].source.element_path,
                 'paragraph_index': index.paragraph_indices.get(bid),
                 'text': index.paragraphs[bid].raw_text if bid in index.paragraphs else ''} for bid in a.block_ids}
    year_counts = {}
    for years in sorted({s.years for s in b.content_sections} | {f.years for f in b.source_fragments}):
        year_counts[str(years)] = {'sections': sum(s.years == years for s in b.content_sections),
                                  'source_fragments': sum(f.years == years for f in b.source_fragments),
                                  'expected_results': sum(f.years == years for f in b.expected_results)}
    return {'id': name, 'path': str(path), 'sha256': a.source_sha256, 'format': a.source_format,
            'checks': checks, 'source_contract': oracle, 'counts_by_year': year_counts,
            'references': refs, 'interpretation': encode(b)}


def markdown_report(records):
    lines = ['# Этап B: полный разбор реального corpus', '',
             'Индексы P/T/R/C ниже начинаются с нуля. Все ссылки ведут в канонический /word/document.xml; точный SourceSpan и полные evidence находятся в JSON каждого документа.', '',
             'SUPPORTED означает доказанного кандидата. Принятый источник определяется отдельно через PlanChoice.accepted_id. NEEDS_CONFIRMATION и CONFLICT не исправляются автоматически.', '']
    def clean(text):
        return str(text).replace('|', '\\|').replace('\n', '<br>')
    for rec in records:
        doc = rec['interpretation']; refs = rec['references']
        def label(bid):
            r = refs.get(bid)
            return 'P' + str(r['paragraph_index']) if r and r['paragraph_index'] is not None else bid[:12]
        def hour(v):
            n = v['value']
            val = str(Fraction(n['numerator'], n['denominator'])) if n else v['state']
            if v['derived_value'] is not None:
                val += ' (выведено 0; исходное значение отсутствует)'
            return val
        lines += ['## ' + rec['id'], '', f"Файл: {rec['path']}", '', f"Формат: {rec['format']}; SHA-256: `{rec['sha256']}`.", '',
                  'Проверки: `' + json.dumps(rec['checks'], ensure_ascii=False) + '`.', '', '### Области годов', '',
                  '| Канал | Годы | Начало | Конец, не включая | Статус | Основание |', '|---|---|---|---|---|---|']
        for r in doc['year_regions']:
            lines.append('| ' + ' | '.join(map(clean, [r['channel'], r['years'], label(r['start_block_id']), label(r['end_block_id_exclusive']) if r['end_block_id_exclusive'] else 'EOF', r['decision']['status'], '; '.join(e['observed'] for e in r['decision']['evidence'])])) + ' |')
        lines += ['', '### Таблицы и ColumnMapping', '']
        for ti, t in enumerate(doc['tables']):
            lines += [f"#### T{ti}: {t['classification']} / {t['subtype']}; {t['decision']['status']}", '']
            for m in t['mappings']:
                lines += [f"Шапка: строки {m['header_rows']}; номер и название в одной колонке: {m['combined_number_title']}; колонки без назначенного смысла: {m['unmapped_columns']}.", '', '| Колонка | Роль | Evidence |', '|---|---|---|']
                for c in m['columns']:
                    lines.append('| ' + ' | '.join(map(clean, [c['column'], c['role'], '; '.join(label(b) + ': ' + refs[b]['text'] for b in c['header_block_ids'])])) + ' |')
                lines.append('')
            for plan in t['plans']:
                lines += [f"Годы {plan['years']}; план: {plan['decision']['status']}.", '', '| Строка | Тип | Номер | Тема / раздел | Часы (исходные состояния сохранены) | Источник |', '|---|---|---|---|---|---|']
                for r in plan['rows']:
                    lines.append('| ' + ' | '.join(map(clean, [r['physical_row'], r['kind'], r['key'], r['title'], '; '.join(k + '=' + hour(v) for k,v in r['hours']), ', '.join(label(b) for b in r['title_block_ids'])])) + ' |')
                lines += ['', 'Арифметические проблемы:', '']
                problems = [c for c in plan['arithmetic'] if c['decision']['status'] != 'SUPPORTED']
                lines.extend('- ' + clean(c['kind']) + ': ' + c['decision']['status'] + '; ' + clean(c['decision']['evidence'][0]['observed']) for c in problems)
                if not problems:
                    lines.append('Нет: все проверяемые равенства сходятся точно.')
                lines.append('')
        lines += ['### Разделы и вложенные фрагменты', '', '`' + json.dumps(rec['counts_by_year'], ensure_ascii=False) + '`', '', '| Год | Раздел | Источник | Вложенные фрагменты | Статус |', '|---|---|---|---|---|']
        for s in doc['content_sections']:
            nested = [f for f in doc['source_fragments'] if f['section_id'] == s['id']]
            lines.append('| ' + ' | '.join(map(clean, [s['years'], s['title'], label(s['heading_block_id']), ', '.join(label(f['block_id']) for f in nested), s['decision']['status']])) + ' |')
        lines += ['', '### UNRESOLVED', '']
        for role in doc['block_roles']:
            if role['role'] == 'UNRESOLVED':
                lines.append('- ' + label(role['block_id']) + ': ' + clean(refs[role['block_id']]['text']))
        lines += ['', '### Выбор плана и альтернативы связей', '']
        for choice in doc['plan_choices']:
            lines.append('- Годы ' + str(choice['years']) + ': ' + choice['decision']['status'] + '; кандидатов ' + str(len(choice['candidate_ids'])) + '; принят: ' + str(choice['accepted_id']))
        sections = {s['id']: s for s in doc['content_sections']}
        row_labels = {r['id']: r['title'] for t in doc['tables'] for p in t['plans'] for r in p['rows']}
        for link in doc['bindings']:
            if link['decision']['status'] != 'SUPPORTED':
                alternatives = ', '.join(label(sections[s]['heading_block_id']) for s in link['section_ids']) or 'нет структурного кандидата'
                lines.append('- ' + clean(row_labels[link['topic_id']]) + '; годы ' + str(link['years']) + ': ' + alternatives + '. ' + clean('; '.join(e['observed'] for e in link['decision']['contradictions'])))
        lines += ['', '### Все вложенные фрагменты (контроль текста)', '']
        for f in doc['source_fragments']:
            lines.append('- ' + label(f['block_id']) + ' ' + str(f['years']) + ' ' + f['kind'] + ': ' + clean(f['raw_text']))
        lines.append('')
    return '\n'.join(lines) + '\n'
