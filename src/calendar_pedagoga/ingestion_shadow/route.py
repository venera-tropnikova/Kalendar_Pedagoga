"""Confirmed canonical packet to the existing calendar downstream, in shadow.

One path for every document. No filename, discipline or program branch.
The source file is read only by ingest(); later steps use the canonical model.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
import re

from calendar_pedagoga.ingestion_adapter import AdaptationError, ExplicitWorkload, adapt
from calendar_pedagoga.ingestion_adapter.models import AdapterPacket, CanonicalRef
from calendar_pedagoga.ingestion_confirmation import State, assess
from calendar_pedagoga.lossless_document import extract_document
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.parsing import Hours, Section, Topic, UtpMetadata, UtpParseResult
from calendar_pedagoga.structural_interpretation import interpret_document

_WEEK_COUNT = re.compile(r"(\d+)\s+учебн\w*\s+недел", re.IGNORECASE)
_CATEGORIES = ("TOTAL", "THEORY", "TRAINING", "PRACTICE")
_CHANNELS = ("theory", "training", "practice")


class ShadowBlocked(Exception):
    """Generation did not start, or a downstream defect stopped it.

    docx is always None: a blocked route never publishes a document.
    """

    def __init__(self, code, message, mechanisms=(), ledger=None, mechanism_counts=None):
        self.code = code
        self.mechanisms = tuple(dict.fromkeys(mechanisms))
        self.mechanism_counts = dict(mechanism_counts or {})
        self.ledger = ledger
        self.docx = None
        super().__init__(code + ": " + message)


@dataclass(frozen=True)
class FieldTrace:
    week_number: int
    field: str
    text: str
    refs: tuple[CanonicalRef, ...]
    spans: tuple
    allocation_index: int | None = None
    topic_number: str | None = None


@dataclass(frozen=True)
class ShadowResult:
    packet: AdapterPacket
    plan_source: str
    study_year: int
    workload: ExplicitWorkload
    utp: UtpParseResult
    lessons: tuple
    traces: tuple[FieldTrace, ...]
    docx: bytes


def ingest(path):
    """Canonical extraction and structural interpretation, once."""
    return interpret_document(extract_document(path))


def run_shadow(session, *, academic_year, workload=None, template=None, publish=True):
    """Block unless the session is confirmed and VALID, then build one DOCX."""
    view = assess(session)
    if not session.confirmed or view.state != State.VALID:
        raise ShadowBlocked(
            "UNCONFIRMED" if not session.confirmed else view.state.value,
            "Downstream accepts only a confirmed VALID model",
        )
    try:
        packet = adapt(session)
    except AdaptationError as error:
        raise ShadowBlocked(error.code, str(error), ledger=error.ledger) from error
    _require_ledger(packet)
    resolved_workload = _workload(packet, workload)
    topics = _topics(packet)
    _require_integral_hours(topics, resolved_workload)
    utp, elements = _schedule(packet, topics, resolved_workload, academic_year)
    content_rows, traces = _content_rows(packet, elements, utp)
    lessons, v2_rows = _lessons(content_rows)
    traces = traces + _lesson_traces(v2_rows, traces)
    _require_distinct_sources(v2_rows, traces, packet.ledger)
    counts = _defects(v2_rows)
    if any(counts.values()):
        raise ShadowBlocked(
            "DOWNSTREAM_DEFECT",
            "Canonical route reproduced an existing downstream defect",
            [code for code, count in counts.items() if count],
            packet.ledger,
            counts,
        )
    docx = b""
    if publish:
        docx = _docx(utp, lessons, academic_year, template)
        _require_docx_match(docx, lessons)
    return ShadowResult(
        packet,
        packet.plan.source,
        packet.plan.study_year,
        resolved_workload,
        utp,
        lessons,
        traces,
        docx,
    )


def _require_ledger(packet):
    fragment_destinations = {}
    for entry in packet.ledger.entries:
        if entry.disposition != "TRANSFERRED":
            continue
        for destination in entry.destinations:
            if destination.startswith("fragments["):
                previous = fragment_destinations.get(entry.ref)
                if previous is not None:
                    raise ShadowBlocked(
                        "DUPLICATE_FRAGMENT",
                        "Fragment destination repeated",
                        ledger=packet.ledger,
                    )
                fragment_destinations[entry.ref] = destination
        if entry.reason == "OTHER_YEAR":
            raise ShadowBlocked(
                "CROSS_YEAR",
                "Transferred entry is scoped to another year",
                ledger=packet.ledger,
            )
    year = packet.plan.study_year
    for record in (*packet.plan.topics, *packet.sections, *packet.fragments):
        if record.provenance.year_scope != (year,):
            raise ShadowBlocked("CROSS_YEAR", "Active provenance leaves the confirmed year", ledger=packet.ledger)


def _amount(pairs, key):
    found = dict(pairs).get(key)
    if found is None:
        return Fraction(0)
    value = found.arithmetic_value
    if value is None:
        raise ShadowBlocked("MISSING_HOURS", "Confirmed hour cell has no exact value: " + key)
    return Fraction(value)


def _workload(packet, workload):
    totals = _amount(packet.plan.canonical_plan.totals, "TOTAL")
    if workload is None:
        weeks = _explicit_weeks(packet)
        if weeks is None:
            raise ShadowBlocked(
                "MISSING_EXPLICIT_WEEKS",
                "Confirmed model has no single explicit week count",
                ledger=packet.ledger,
            )
        if totals <= 0 or weeks <= 0 or totals % weeks != 0:
            raise ShadowBlocked(
                "WORKLOAD_MISMATCH",
                "Explicit week count does not reconcile with confirmed hours",
                ledger=packet.ledger,
            )
        return ExplicitWorkload(weeks, totals / weeks, "canonical-explicit-week-count")
    if (
        not workload.confirmation
        or type(workload.study_weeks) is not int
        or workload.study_weeks <= 0
        or type(workload.hours_per_week) is bool
        or type(workload.hours_per_week) not in (int, Fraction, Decimal)
        or Fraction(workload.hours_per_week) <= 0
        or workload.study_weeks * Fraction(workload.hours_per_week) != totals
    ):
        raise ShadowBlocked(
            "WORKLOAD_MISMATCH",
            "Explicit workload does not reconcile with confirmed hours",
            ledger=packet.ledger,
        )
    return workload


def _explicit_weeks(packet):
    year = packet.plan.study_year
    program = packet.session.model.program
    roles = {role.block_id: role.years for role in program.block_roles}
    found = []
    for paragraph in program.source_document.paragraphs:
        years = roles.get(paragraph.id, ())
        if years and year not in years:
            continue
        for match in _WEEK_COUNT.finditer(paragraph.normalized_text or ""):
            found.append(int(match.group(1)))
    unique = tuple(dict.fromkeys(found))
    if len(unique) != 1 or unique[0] <= 0:
        return None
    return unique[0]


def _topics(packet):
    rows = {row.id: row for row in packet.plan.canonical_plan.rows}
    totals = {key: _amount(packet.plan.canonical_plan.totals, key) for key in _CATEGORIES}
    built = []
    sums = {key: Fraction(0) for key in _CATEGORIES if key != "TOTAL"}
    for topic in packet.plan.topics:
        hours = {key: _amount(topic.canonical.hours, key) for key in sums}
        if hours["THEORY"] + hours["TRAINING"] + hours["PRACTICE"] != hours.get("TOTAL", _amount(topic.canonical.hours, "TOTAL")):
            raise ShadowBlocked("HOURS_DO_NOT_RECONCILE", "Topic categories do not reconcile", ledger=packet.ledger)
        for key in sums:
            sums[key] += hours[key]
        parent = rows.get(topic.canonical.parent_id)
        section = parent.title if parent is not None else topic.canonical.title
        number = ".".join(map(str, topic.canonical.key)) or None
        built.append((topic, number, section, hours))
    for key in sums:
        if sums[key] != totals[key]:
            raise ShadowBlocked(
                "HOURS_DO_NOT_RECONCILE",
                "Topic hours differ from confirmed totals",
                ledger=packet.ledger,
            )
    if totals["THEORY"] + totals["TRAINING"] + totals["PRACTICE"] != totals["TOTAL"]:
        raise ShadowBlocked("HOURS_DO_NOT_RECONCILE", "Confirmed totals do not reconcile", ledger=packet.ledger)
    return tuple(built)


def _require_integral_hours(topics, workload):
    values = [Fraction(workload.hours_per_week)]
    for _topic, _number, _section, hours in topics:
        values.extend(hours[key] for key in ("THEORY", "TRAINING", "PRACTICE"))
    if any(value.denominator != 1 for value in values):
        raise ShadowBlocked(
            "FRACTIONAL_HOUR_GRID",
            "Existing allocator counts whole hours and would drop a fraction",
        )


def _schedule(packet, topics, workload, academic_year):
    from calendar_pedagoga.scheduling import ScheduleValidationError, build_academic_weeks

    weekly = Fraction(workload.hours_per_week)
    try:
        weeks = build_academic_weeks(academic_year, workload.study_weeks)
    except ValueError as error:
        raise ShadowBlocked("ACADEMIC_YEAR", str(error), ledger=packet.ledger) from error
    elements = []
    week_index = 0
    used = Fraction(0)
    projected = []
    for topic, number, section, hours in topics:
        projected.append(Topic(
            number,
            topic.canonical.title,
            Hours(
                int(hours["THEORY"] + hours["TRAINING"] + hours["PRACTICE"]),
                int(hours["THEORY"]),
                int(hours["PRACTICE"]),
            ),
            section,
            False,
        ))
        for channel, key in zip(_CHANNELS, ("THEORY", "TRAINING", "PRACTICE")):
            remaining = hours[key]
            while remaining:
                if week_index >= len(weeks):
                    raise ShadowBlocked("HOURS_DO_NOT_FIT", "Confirmed hours exceed the explicit week grid", ledger=packet.ledger)
                portion = min(remaining, weekly - used)
                elements.append((channel, int(portion), weeks[week_index], number, topic.canonical.title, section, topic))
                remaining -= portion
                used += portion
                if used == weekly:
                    week_index += 1
                    used = Fraction(0)
    if used != 0 or week_index != len(weeks):
        raise ShadowBlocked("PARTIAL_WEEK", "Explicit grid does not fill every confirmed hour", ledger=packet.ledger)
    totals = {key: _amount(packet.plan.canonical_plan.totals, key) for key in _CATEGORIES}
    utp = UtpParseResult(
        UtpMetadata(
            study_year=str(packet.plan.study_year),
            hours_per_week=int(weekly),
            hours_per_year=int(totals["TOTAL"]),
            study_weeks=workload.study_weeks,
            workload_provenance=workload.confirmation,
        ),
        _sections(projected),
        tuple(projected),
        Hours(int(totals["TOTAL"]), int(totals["THEORY"]), int(totals["PRACTICE"])),
    )
    try:
        _check_grid(elements, weeks, weekly, totals)
    except ScheduleValidationError as error:
        raise ShadowBlocked("SCHEDULE", str(error), ledger=packet.ledger) from error
    return utp, tuple(elements)


def _sections(topics):
    sections = []
    seen = set()
    for topic in topics:
        title = topic.parent_section or topic.title
        if title in seen:
            continue
        seen.add(title)
        sections.append(Section(None, title, topic.hours, False))
    return tuple(sections)


def _check_grid(elements, weeks, weekly, totals):
    from calendar_pedagoga.scheduling import ScheduleValidationError

    sums = {channel: 0 for channel in _CHANNELS}
    for channel, hours, _week, _number, _title, _section, _topic in elements:
        sums[channel] += hours
    if sums["theory"] != totals["THEORY"] or sums["practice"] != totals["PRACTICE"] or sums["training"] != totals["TRAINING"]:
        raise ScheduleValidationError("Scheduled channels do not match confirmed hours")
    if sum(sums.values()) != totals["TOTAL"]:
        raise ScheduleValidationError("Scheduled hours do not match the confirmed total")
    loads = []
    for week in weeks:
        loads.append(sum(hours for _channel, hours, item, *_rest in elements if item.number == week.number))
    if any(load != weekly for load in loads):
        raise ScheduleValidationError("A week does not match the explicit weekly load")


def _content_rows(packet, elements, utp):
    from calendar_pedagoga.content_generation import CalendarContentRow, WeekTopicPart
    from calendar_pedagoga.ingestion_shadow.slot_binding import bind_slots

    sources = bind_slots(packet, elements)
    blocked = [source for source in sources if source.blocked]
    if blocked:
        raise ShadowBlocked(
            "AMBIGUOUS_BINDING",
            "A calendar slot has more than one confirmed section owner",
            ledger=packet.ledger,
        )
    repeated_identical = _repeated_identical_slots(sources)
    repeated_titles = _repeated_title_slots(sources)
    parts_by_week = {}
    traces = []
    for source in sources:
        # The calendar table has a theory column and a practice column.
        # Training stays on the slot. The theory column is where non-practice
        # hours are shown, including when the same week also has practice.
        # Practice hours are not increased by training, and training is not
        # omitted from the cells.
        theory_hours = source.theory + source.training
        practice_hours = source.practice
        # Title-only and identical sources have no distinguishing atom.
        # Empty SOURCE units let the existing repeated-title allocator supply
        # phase or catalog data. The canonical text stays on the trace.
        title_only = id(source) in repeated_titles or id(source) in repeated_identical
        part = WeekTopicPart(
            topic_number=source.number,
            topic_title=source.title,
            section=source.section,
            theory_hours=theory_hours,
            practice_hours=practice_hours,
            match_status=MatchStatus.USER_CONFIRMED,
            program_section=source.section,
            program_topic=source.title,
            program_content_full="" if title_only else source.text,
            weekly_content_assigned=title_only,
        )
        parts_by_week.setdefault(source.week_number, []).append((source, part))
        traces.append(FieldTrace(
            source.week_number,
            "canonical_source",
            source.text,
            source.refs,
            source.spans,
            source.allocation_index,
            source.number,
        ))
    rows = []
    for week_number in sorted(parts_by_week):
        packed = parts_by_week[week_number]
        source = packed[0][0]
        parts = tuple(part for _source, part in packed)
        theory = sum(item.theory for item, _part in packed)
        practice = sum(item.practice for item, _part in packed)
        training = sum(item.training for item, _part in packed)
        content = "\n".join(part.program_content_full for part in parts if part.program_content_full)
        row = CalendarContentRow(
            week_number=week_number,
            date_range=source.week.date_range,
            month=source.week.month,
            section=parts[0].section,
            topic_number=parts[0].topic_number,
            topic_title=parts[0].topic_title,
            source_topic_title=parts[0].topic_title,
            theory_hours=sum(part.theory_hours for part in parts),
            practice_hours=practice,
            total_hours=theory + practice + training,
            match_status=MatchStatus.USER_CONFIRMED,
            program_section=parts[0].program_section,
            program_topic=parts[0].program_topic,
            program_content_full=content,
            program_content_preview=content[:240],
            source_program_name="",
            source_utp_name=packet.plan.canonical_plan.id,
            week_parts=parts,
        )
        rows.append(row)
    if sum(row.total_hours for row in rows) != utp.metadata.hours_per_year:
        raise ShadowBlocked("HOURS_DO_NOT_RECONCILE", "Calendar rows lost confirmed hours", ledger=packet.ledger)
    cell_hours = sum(
        int(part.theory_hours) + int(part.practice_hours)
        for row in rows
        for part in row.week_parts
    )
    if cell_hours != int(utp.metadata.hours_per_year):
        raise ShadowBlocked(
            "HOURS_DO_NOT_RECONCILE",
            "DOCX cell hours differ from the confirmed plan total",
            ledger=packet.ledger,
        )
    return tuple(rows), tuple(traces)


def _lesson_traces(v2_rows, content_traces):
    refs_by_week = {}
    spans_by_week = {}
    for trace in content_traces:
        refs_by_week.setdefault(trace.week_number, []).extend(trace.refs)
        spans_by_week.setdefault(trace.week_number, []).extend(trace.spans)
    traces = []
    for row in v2_rows:
        week = row.source.week_number
        refs = tuple(dict.fromkeys(refs_by_week.get(week, ())))
        spans = tuple(spans_by_week.get(week, ()))
        for field in ("lesson_type", "planned_result", "assessment_method", "theory_text", "practice_text"):
            traces.append(FieldTrace(week, field, getattr(row, field) or "", refs, spans))
    return tuple(traces)


def _lessons(content_rows):
    from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2
    from calendar_pedagoga.lesson_content import LessonContentRow
    from calendar_pedagoga.lesson_resolution import resolve_lesson_content

    v2_rows = build_lesson_content_v2(tuple(content_rows))
    lesson_rows = tuple(
        LessonContentRow(
            source=row.source,
            theory_text=row.theory_text,
            practice_text=row.practice_text,
            lesson_type=row.lesson_type,
            planned_result=row.planned_result,
            assessment_method=row.assessment_method,
            warnings=row.warnings,
        )
        for row in v2_rows
    )
    return resolve_lesson_content(lesson_rows, freeze_pedagogical_fields=True), v2_rows


def _normalized_source(text):
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _display_channel(source):
    training_only = source.training and not source.theory and not source.practice
    theory = bool(source.theory or training_only)
    practice = bool(source.practice)
    if theory and practice:
        return "both"
    if practice:
        return "practice"
    return "theory"


def _repeated_title_slots(sources):
    """Title-only slots of one topic and channel. A single slot keeps its title."""
    grouped = {}
    for source in sources:
        if source.blocked or not source.title_fallback:
            continue
        key = (source.topic_id, _display_channel(source))
        grouped.setdefault(key, []).append(id(source))
    repeated = set()
    for members in grouped.values():
        if len(members) >= 2:
            repeated.update(members)
    return repeated


def _repeated_identical_slots(sources):
    """Slots of one topic and channel that repeat one normalized source."""
    grouped = {}
    for source in sources:
        if source.blocked or source.title_fallback:
            continue
        key = (source.topic_id, _display_channel(source), _normalized_source(source.text))
        grouped.setdefault(key, []).append(id(source))
    repeated = set()
    for members in grouped.values():
        if len(members) >= 2:
            repeated.update(members)
    return repeated


def _require_distinct_sources(lessons, traces, ledger):
    """Different canonical sources must not share one RESULT/CONTROL frame.

    Identical and title-only sources are handled by the existing repeated-slot
    allocator. A collapsed difference is not given a stage number.
    """
    texts: dict[tuple, list[str]] = {}
    for trace in traces:
        if trace.field != "canonical_source":
            continue
        texts.setdefault((trace.week_number, trace.topic_number), []).append(
            _normalized_source(trace.text)
        )
    grouped: dict[tuple, list] = {}
    for lesson in lessons:
        source = lesson.source
        identity = (
            source.topic_number,
            source.topic_title,
            lesson.lesson_type,
            (lesson.planned_result or "").strip(),
            (lesson.assessment_method or "").strip(),
        )
        grouped.setdefault(identity, []).append(lesson)
    for members in grouped.values():
        if len(members) < 2:
            continue
        collected = []
        for lesson in members:
            collected.extend(texts.get((lesson.source.week_number, lesson.source.topic_number), []))
        if len(set(collected)) > 1:
            raise ShadowBlocked(
                "SEMANTIC_COLLAPSE",
                "Distinct canonical sources were rendered as one frame",
                ledger=ledger,
            )


def _defects(lessons):
    from calendar_pedagoga.production_readiness import production_readiness_codes

    counts: dict[str, int] = {}

    def add(code):
        counts[code] = counts.get(code, 0) + 1

    seen = {}
    for lesson in lessons:
        for code in production_readiness_codes(lesson):
            add(code)
        result = (lesson.planned_result or "").strip()
        control = (lesson.assessment_method or "").strip()
        source = lesson.source
        identity = (
            source.topic_number,
            source.topic_title,
            lesson.lesson_type,
            result,
            control,
        )
        seen.setdefault(identity, []).append(source.week_number)
    for weeks in seen.values():
        if len(weeks) > 1:
            add("REPEATED_SCHEDULE_RESULT_CONTROL")
    return counts


def _docx(utp, lessons, academic_year, template):
    from calendar_pedagoga.docx_generation import generate_calendar_docx
    from calendar_pedagoga.docx_qa import (
        has_blocking_qa_issues,
        validate_calendar_docx,
        validate_calendar_docx_visual,
    )
    from calendar_pedagoga.organization_template import (
        CalendarTemplateSelection,
        CalendarTemplateSource,
    )

    selected = template or CalendarTemplateSelection(CalendarTemplateSource.STANDARD)
    docx = generate_calendar_docx(
        utp,
        lessons,
        selected,
        academic_year,
        study_year_hints=(),
    )
    issues = validate_calendar_docx(docx, expected_weeks=len_weeks(utp))
    visual = validate_calendar_docx_visual(docx)
    blocking = tuple(
        issue.message for issue in (*issues, *visual) if has_blocking_qa_issues((issue,))
    )
    if blocking:
        raise ShadowBlocked("DOCX_QA", "DOCX QA rejected the canonical document", blocking)
    return docx


def len_weeks(utp):
    return utp.metadata.study_weeks


def _require_docx_match(docx, lessons):
    from calendar_pedagoga.semantic_atom.oracle import extract_docx_weeks

    extracted = extract_docx_weeks(docx)
    if len(extracted) != len(lessons):
        raise ShadowBlocked("DOCX_MODEL_MISMATCH", "DOCX week count differs from the calendar model")
    for left, right in zip(extracted, lessons):
        if left.week_number != right.source.source.week_number:
            raise ShadowBlocked("DOCX_MODEL_MISMATCH", "DOCX week order differs from the calendar model")
        for field, actual, expected in (
            ("lesson_type", left.lesson_type, right.lesson_type),
            ("planned_result", left.planned_result, right.planned_result),
            ("assessment", left.assessment, right.assessment_method),
        ):
            if _compact(actual) != _compact(expected):
                raise ShadowBlocked("DOCX_MODEL_MISMATCH", "DOCX field differs from the calendar model: " + field)


def _compact(text):
    return re.sub(r"\s+", "", text or "")
