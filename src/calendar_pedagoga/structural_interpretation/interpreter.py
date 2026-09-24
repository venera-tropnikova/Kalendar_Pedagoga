"""Pure stage B entry point. Does not read paths or import production parsing."""
from collections import defaultdict
from calendar_pedagoga.lossless_document.models import ExtractedDocument
from .content import bind_topics, interpret_content
from .index import DocumentIndex
from .models import Decision, PlanChoice, StructuralDocument
from .tables import interpret_tables
from .years import interpret_years


def interpret_document(document: ExtractedDocument) -> StructuralDocument:
    index = DocumentIndex(document)
    mentions, regions = interpret_years(index)
    tables = interpret_tables(index, regions)
    sections, fragments, results, roles = interpret_content(index, regions, tables)
    bindings = bind_topics(index, tables, sections)
    grouped = defaultdict(list)
    for table in tables:
        for plan in table.plans:
            grouped[plan.years].append(plan)
    choices = []
    for years, plans in sorted(grouped.items()):
        accepted = plans[0].id if len(plans) == 1 and plans[0].decision.status == 'SUPPORTED' and len(years) == 1 else None
        choices.append(PlanChoice(years, tuple(p.id for p in plans), accepted,
                        Decision('SUPPORTED' if accepted else 'NEEDS_CONFIRMATION', .99 if accepted else .5,
                                 tuple(e for p in plans for e in p.decision.evidence),
                                 tuple(e for p in plans for e in p.decision.contradictions),
                                 tuple(p.id for p in plans) if not accepted else ())))
    conflicts = tuple(e for table in tables for p in table.plans for e in p.decision.contradictions)
    conflicts += tuple(e for b in bindings for e in b.decision.contradictions)
    return StructuralDocument('structural-interpretation/1', document, mentions, regions, tables,
                              sections, fragments, results, roles, bindings, tuple(choices), conflicts)
