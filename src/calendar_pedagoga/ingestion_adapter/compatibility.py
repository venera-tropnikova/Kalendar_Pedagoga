"""Explicit legacy projections. No invocation of parsing or generation functions."""
from decimal import Decimal
from fractions import Fraction
from .models import CompatibilityError, LegacyContentRecord, LegacyPlanBundle


def _decimal(value):
    if value is None:
        raise CompatibilityError("Missing hour category cannot become zero")
    value = Fraction(value)
    denominator = value.denominator
    for factor in (2, 5):
        while denominator % factor == 0:
            denominator //= factor
    if denominator != 1:
        raise CompatibilityError("Legacy Decimal cannot represent these rational hours exactly")
    if value.denominator == 1:
        return value.numerator
    # Construct exactly, independent of the process Decimal precision.
    twos = fives = 0
    denominator = value.denominator
    while denominator % 2 == 0:
        twos += 1
        denominator //= 2
    while denominator % 5 == 0:
        fives += 1
        denominator //= 5
    scale = max(twos, fives)
    numerator = abs(value.numerator) * 2 ** (scale - twos) * 5 ** (scale - fives)
    return Decimal((int(value < 0), tuple(map(int, str(numerator))), -scale))


def legacy_plan(packet, workload):
    """Opt-in projection, inseparable from the overlay; never guesses training/week values."""
    from calendar_pedagoga.confirmed_study_plan import ConfirmedStudyPlan
    from calendar_pedagoga.parsing import Hours, Topic
    from .adapter import adapt
    if adapt(packet.session) != packet:
        raise CompatibilityError("Invalid canonical packet")
    plan = packet.plan
    if plan.source not in ("EXTERNAL", "MANUAL"):
        raise CompatibilityError("Legacy source enum cannot represent a confirmed embedded plan; use overlay v1")
    if workload is None or not workload.confirmation or type(workload.study_weeks) is not int or workload.study_weeks <= 0:
        raise CompatibilityError("Explicit confirmed workload is required; weeks are never inferred")
    totals = {k: v.arithmetic_value for k, v in plan.canonical_plan.totals}
    if type(workload.hours_per_week) not in (int, Fraction, Decimal):
        raise CompatibilityError("Workload hours must be exact, not float or bool")
    if Fraction(workload.hours_per_week) <= 0 or workload.study_weeks * Fraction(workload.hours_per_week) != totals["TOTAL"]:
        raise CompatibilityError("Explicit workload does not reconcile with confirmed hours")
    topics = []
    all_rows = {r.id: r for r in plan.canonical_plan.rows}
    for topic in plan.topics:
        row = topic.canonical
        hours = {k: v.arithmetic_value for k, v in row.hours}
        if hours.get("TRAINING", Fraction(0)) != 0:
            raise CompatibilityError("Legacy Hours has no training category; combining it with practice is forbidden")
        if not {"TOTAL", "THEORY", "PRACTICE"} <= hours.keys():
            raise CompatibilityError("Legacy hour categories are absent")
        topics.append(Topic(".".join(map(str, row.key)) or None, row.title,
                            Hours(*(_decimal(hours[k]) for k in ("TOTAL", "THEORY", "PRACTICE"))),
                            all_rows[row.parent_id].title if row.parent_id else None, False))
    if totals.get("TRAINING", Fraction(0)) != 0:
        raise CompatibilityError("Legacy totals have no training category")
    for key in ("TOTAL", "THEORY", "PRACTICE"):
        if sum(dict(t.canonical.hours)[key].arithmetic_value for t in plan.topics) != totals.get(key):
            raise CompatibilityError("Topic-only hours differ from totals; sections cannot become topics")
    if totals["THEORY"] + totals["PRACTICE"] != totals["TOTAL"]:
        raise CompatibilityError("Legacy total categories do not reconcile")
    result = ConfirmedStudyPlan(plan.study_year, tuple(topics),
        *(_decimal(totals[k]) for k in ("TOTAL", "THEORY", "PRACTICE")),
        workload.study_weeks, _decimal(workload.hours_per_week),
        "external_utp" if plan.source == "EXTERNAL" else "manual")
    return LegacyPlanBundle(result, packet, workload)


def legacy_content(packet):
    """One existing ProgramContentItem per fragment, paired with mandatory provenance.

    Never feed this through the old title-deduplicating overlay decoder.
    """
    from calendar_pedagoga.program_parsing import ProgramContentItem
    from .adapter import adapt
    if adapt(packet.session) != packet:
        raise CompatibilityError("Invalid canonical packet")
    sections = {s.canonical.id: s.canonical for s in packet.sections}
    result = []
    for record in packet.fragments:
        fragment = record.canonical
        section = sections[fragment.section_id]
        item = ProgramContentItem(".".join(map(str, section.key)) or None,
                                  section.title, fragment.raw_text, section.title, packet.plan.study_year)
        result.append(LegacyContentRecord(item, record.provenance, fragment.id,
                                         fragment.parent_fragment_id, fragment.list_level))
    return tuple(result)
