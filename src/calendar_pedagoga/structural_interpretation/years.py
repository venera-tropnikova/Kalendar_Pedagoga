"""Lexical grammar for document structure, never for a discipline or topic."""
from __future__ import annotations
import re
from .models import Decision, YearMention, YearRegion

ORDINALS = {'перв': 1, 'втор': 2, 'трет': 3, 'четверт': 4, 'пят': 5,
            'шест': 6, 'седьм': 7, 'восьм': 8, 'девят': 9, 'десят': 10}
ROMANS = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5, 'vi': 6,
          'vii': 7, 'viii': 8, 'ix': 9, 'x': 10}
YEAR_TOKEN = r'(?:\d{1,2}(?:\s*-?\s*(?:й|ый|ой|ий|го|ого))?|[ivx]+|(?:перв|втор|трет|четверт|пят|шест|седьм|восьм|девят|десят)[а-яё]*)'
YEAR = re.compile(r'(?<![\w.])(' + YEAR_TOKEN + r')\s+год(?:а|у|ом|ы|ов)?\s+обучени[яю]', re.I)
PURE_YEAR = re.compile(r'^\s*\(?\s*' + YEAR_TOKEN + r'\s+год(?:а|у)?\s+обучения\s*[.)]*\s*$', re.I)
PLAN = re.compile(r'^(?:\d+[.)]?\s*)?(?:учебн[а-я-]*\s*(?:-\s*тематическ[а-я]*\s*)?план|вариативный\s+план)', re.I)
CONTENT = re.compile(r'^(?:\d+[.)]?\s*)?(?:содержание\s+(?:учебн[а-я-]*\s+)?программ|примерная\s+учебная\s+программа)', re.I)
RESULT = re.compile(r'^(?:(?:\d+[.)]?\s*)?(?:(?:предполагаемые|планируемые|ожидаемые|педагогические|личностные|предметные|метапредметные)\s+)*результат|механизм\s+оценки.*результат|в\s+результате\s+прохождения|по\s+окончани[ию].*(?:должны|знать|уметь))', re.I)
REFERENCE = re.compile(r'^(?:\d+[.)]?\s*)?(?:описание\s+условий|условия\s+реализации|методическ[а-я-]*\s+обеспечение|организационно\s*-?\s*методическ|список\s+литератур|литература|учебно-методическ[а-я-]*\s+обеспечение|контрольные\s+испытания|предлагаемые\s+контрольные\s+нормативы|критерии\s+оценки|диагности(?:ка|ческ))', re.I)
ASSESSMENT = re.compile(r'^тест(?:ы)?\s+для\s+(?:проведения|проверки|оценки)|^контрольные\s+испытания|^диагностика\s+', re.I)
SINGLE_YEAR = re.compile(r'(?:программ[а-я]*\s+рассчитан[а-я]*\s+на|срок\s+реализации\s*(?:программы)?\s*[-–—:]?)\s*(?:один|1)\s+год\s+обучения', re.I)


def years_in(text):
    group_pattern = re.compile(r'(?<![\w.])(' + YEAR_TOKEN + r'(?:\s*(?:,|и|[–—])\s*' + YEAR_TOKEN + r')*)\s+год(?:а|у|ом|ы|ов)?\s+обучени[яю]', re.I)
    values = []
    for match in group_pattern.finditer(text):
        for token in re.findall(YEAR_TOKEN, match.group(1), re.I):
            token = token.lower().replace('ё', 'е')
            digits = re.match(r'\d+', token)
            value = int(digits.group()) if digits else ROMANS.get(token)
            if value is None:
                value = next((v for k, v in ORDINALS.items() if token.startswith(k)), None)
            if value is not None and value not in values:
                values.append(value)
    return tuple(values)


def interpret_years(index):
    """Return disjoint channel regions. A results mention never changes content scope."""
    events = {}
    mentions = []
    default_ids = [p.id for p in index.main_paragraphs if SINGLE_YEAR.search(p.normalized_text)]
    boundary_years = {y for p in index.main_paragraphs if not p.cell
                      and (PURE_YEAR.match(p.normalized_text) or PLAN.match(p.normalized_text)
                           or CONTENT.match(p.normalized_text)) for y in years_in(p.normalized_text)}
    default_years = (1,) if default_ids and not (boundary_years - {1}) else ()
    default_evidence = tuple(index.evidence('EXPLICIT_SINGLE_YEAR_DURATION', [b], index.paragraphs[b].raw_text)
                             for b in default_ids)
    current_years = default_years
    content_years = default_years
    channel = 'GENERAL'
    previous = None
    for item in index.flow:
        if item.id not in index.paragraphs:
            events[item.id] = (current_years, channel, (), False)
            continue
        p = item
        text = p.normalized_text
        years = years_in(text)
        inline_result = False
        evidence = ()
        boundary = False
        effect = 'REFERENCE'
        flags = index.flags(p)
        caption_like = len(text) < 240 and (flags['bold'] or flags['outline'] is not None or bool(years) or len(text.split()) <= 5 or '«' in text)
        if PLAN.match(text) and caption_like:
            channel = 'PLAN'
            current_years = years or content_years
            boundary = True
            effect = 'PLAN_CAPTION'
        elif CONTENT.match(text) and caption_like:
            channel = 'CONTENT'
            current_years = years or content_years
            content_years = current_years
            boundary = True
            effect = 'CONTENT_CAPTION'
        elif RESULT.match(text):
            channel = 'RESULTS'
            current_years = years or current_years or content_years
            if not caption_like:
                current_years = ()
                inline_result = True
            boundary = True
            effect = 'RESULTS_ONLY'
        elif ASSESSMENT.match(text) and caption_like:
            channel = 'ASSESSMENT'
            current_years = years
            boundary = True
            effect = 'ASSESSMENT_ONLY'
        elif PURE_YEAR.match(text):
            # The second line of a multi-line result heading qualifies only that heading.
            if previous and RESULT.match(previous.normalized_text) and not years_in(previous.normalized_text) and len(previous.normalized_text) < 160:
                channel = 'RESULTS'
                current_years = years
                effect = 'RESULTS_ONLY'
            elif previous and ASSESSMENT.match(previous.normalized_text):
                channel = 'ASSESSMENT'
                current_years = years
                effect = 'ASSESSMENT_ONLY'
            elif previous and PLAN.match(previous.normalized_text):
                channel = 'PLAN'
                current_years = years
                effect = 'PLAN_CAPTION'
            else:
                channel = 'CONTENT' if channel == 'CONTENT' else 'GENERAL'
                current_years = years
                content_years = years
                effect = 'YEAR_HEADING'
            boundary = True
        elif channel == 'RESULTS' and re.match(r'^\d+[.)]\s+', text) and flags['bold'] and not p.numbering:
            channel = 'REFERENCE'
            current_years = ()
            boundary = True
            effect = 'NEW_NUMBERED_DOCUMENT_SECTION'
        elif REFERENCE.match(text) and len(text) < 240:
            channel = 'REFERENCE'
            current_years = ()
            content_years = default_years
            boundary = True
            effect = 'REFERENCE'
        elif channel == 'PLAN' and years and previous and PLAN.match(previous.normalized_text):
            # Multi-line plan caption: e.g. a separate line "для групп 1-го года ...".
            current_years = years
            effect = 'PLAN_CAPTION'
            boundary = True
        if boundary:
            evidence = (index.evidence(effect, [p.id], p.raw_text),)
            if default_ids and any(y != 1 for y in current_years):
                evidence += (index.evidence('SINGLE_YEAR_DURATION_CONFLICT', [*default_ids, p.id],
                             f'single-year duration versus explicit years {current_years}'),)
            if PURE_YEAR.match(text) and previous and (PLAN.match(previous.normalized_text) or CONTENT.match(previous.normalized_text)):
                prior_years = years_in(previous.normalized_text)
                if prior_years and prior_years != years:
                    current_years = tuple(sorted(set(prior_years) | set(years)))
                    evidence += (index.evidence('CONFLICTING_YEAR_MARKERS', [previous.id, p.id],
                                 f'{prior_years} <> {years}'),)
                    effect = 'CONFLICTING_YEAR_MARKERS'
        if boundary and channel == 'PLAN' and current_years and current_years != content_years:
            # A new plan cannot silently leave subsequent unqualified content in the old year.
            content_years = ()
        events[p.id] = (current_years, channel, evidence, boundary)
        if years or SINGLE_YEAR.search(text):
            years = years or (1,)
            if SINGLE_YEAR.search(text):
                effect = 'DURATION_ONLY'
            contradictions = ()
            if effect in ('REFERENCE', 'DURATION_ONLY', 'RESULTS_ONLY'):
                contradictions = (index.evidence('NOT_A_CONTENT_BOUNDARY', [p.id], effect),)
            mentions.append(YearMention(p.id + ':year', p.id, years, effect, p.source,
                                        Decision('SUPPORTED' if len(years) == 1 else 'NEEDS_CONFIRMATION',
                                                 .98 if boundary else .65,
                                                 (index.evidence('YEAR_EXPRESSION', [p.id], p.raw_text),),
                                                 contradictions)))
        if inline_result:
            channel = 'GENERAL'
            current_years = ()
        if text:
            previous = p
    # In-cell mentions are retained but cannot switch the surrounding document channel.
    for p in index.main_paragraphs:
        if p.cell and years_in(p.normalized_text):
            mentions.append(YearMention(p.id + ':year', p.id, years_in(p.normalized_text), 'TABLE_LOCAL',
                                        p.source, Decision('NEEDS_CONFIRMATION', .65,
                                        (index.evidence('IN_CELL_YEAR', [p.id], p.raw_text),),
                                        (index.evidence('NOT_A_CONTENT_BOUNDARY', [p.id], 'table-local mention'),))))
    regions = []
    group = []
    scope = None
    boundary_evidence = default_evidence

    def finish(next_id):
        if not group:
            return
        ids = []
        for b in group:
            ids.extend(index.table_blocks(b))
        ids = tuple(dict.fromkeys(ids))
        years, kind = scope
        ev = boundary_evidence or (index.evidence('NO_EXPLICIT_YEAR', [group[0].id], kind),)
        permitted = {'YEAR_HEADING', 'PLAN_CAPTION', 'CONTENT_CAPTION'}
        if kind == 'RESULTS':
            permitted.add('RESULTS_ONLY')
        markers = [m for m in mentions if m.years == years and m.effect in permitted
                   and index.positions[m.block_id] <= index.positions[group[0].id]]
        if years and markers:
            marker = max(markers, key=lambda m: index.positions[m.block_id])
            ev += (index.evidence('YEAR_SCOPE_SOURCE', [marker.block_id], str(years)),)
        elif years == default_years and years:
            ev += default_evidence
        regions.append(YearRegion(group[0].id + ':region:' + kind, years, kind, group[0].id,
                                  next_id, ids, group[0].source,
                                  Decision('SUPPORTED' if len(years) == 1 and not any(e.code.endswith('_CONFLICT') or e.code == 'CONFLICTING_YEAR_MARKERS' for e in ev) else 'NEEDS_CONFIRMATION',
                                           .97 if len(years) == 1 else .3, ev,
                                           tuple(e for e in ev if e.code in ('CONFLICTING_YEAR_MARKERS', 'SINGLE_YEAR_DURATION_CONFLICT')), alternatives=tuple('year:' + str(y) for y in years) if len(years) > 1 else ())))
    for item in index.flow:
        years, kind, evidence, boundary = events[item.id]
        if scope != (years, kind) or boundary:
            finish(item.id)
            group = []
            scope = (years, kind)
            boundary_evidence = evidence or default_evidence
        group.append(item)
    finish(None)
    mentions.sort(key=lambda m: index.positions[m.block_id])
    return tuple(mentions), tuple(regions)
