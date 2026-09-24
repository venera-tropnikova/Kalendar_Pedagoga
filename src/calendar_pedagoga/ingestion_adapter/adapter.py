"""Pure projection of a confirmed C session. No legacy parser, matching or worker invocation."""
from dataclasses import fields, is_dataclass, replace
from hashlib import sha256

from calendar_pedagoga.ingestion_confirmation import assess, build_model, restore, save, State
from calendar_pedagoga.ingestion_confirmation.machine import answers
from .codec import digest, dumps, loads
from .models import (
    VERSION, AdaptationError, AdapterPacket, BindingRecord, CanonicalRef,
    ConfirmedStudyPlanOverlay, CoverageEntry, CoverageLedger, FragmentRecord,
    PlanTopic, Provenance, SectionRecord,
)


def inventory(model, assessment=None):
    """One ledger key per source-qualified canonical ID, including all A XML nodes."""
    result = {}
    def visit(value, source, path):
        if is_dataclass(value):
            identifier = getattr(value, "id", None)
            if identifier:
                result.setdefault(CanonicalRef(source, identifier), []).append(path)
            for field in fields(value):
                visit(getattr(value, field.name), source, path + "." + field.name)
        elif isinstance(value, (tuple, list)):
            for i, item in enumerate(value):
                visit(item, source, path + "[" + str(i) + "]")
    for source in model.sources:
        visit(source.document, source.kind, "sources." + source.kind)
    if assessment is not None and assessment.plan is not None:
        visit(assessment.plan, assessment.source, "confirmation.assessment.plan")
    return {key: tuple(paths) for key, paths in result.items()}


def _fresh(model):
    values = {s.kind: s.document for s in model.sources}
    if len(values) != len(model.sources) or set(values) - {"EMBEDDED", "EXTERNAL", "MANUAL"}:
        raise ValueError("Duplicate or unknown input source")
    return build_model(values["EMBEDDED"], values.get("EXTERNAL"), values.get("MANUAL"))


def adapt(session):
    """Return a complete versioned packet or a blocking error with a complete ledger."""
    model = session.model
    inputs = inventory(model)
    destinations = {}
    selected_refs = set()

    def fail(code, message, blocked=()):
        blocked = set(blocked) or selected_refs or set(inputs)
        ledger = CoverageLedger(tuple(CoverageEntry(ref, locations,
            "BLOCKED" if ref in blocked else "EXCLUDED", (),
            code if ref in blocked else "TRANSFER_ABORTED")
            for ref, locations in sorted(inputs.items())))
        raise AdaptationError(code, message, ledger)

    try:
        fresh = _fresh(model)
        if fresh.fingerprint != model.fingerprint:
            fail("STALE_CONFIRMATION", "Model fingerprint no longer matches its source interpretation")
        for source in model.sources:
            doc = source.document.source_document
            if sha256(doc.original_bytes).hexdigest() != doc.source_sha256 or sha256(doc.package_bytes).hexdigest() != doc.package_sha256:
                fail("SOURCE_INTEGRITY", "Source bytes no longer match the extraction hashes")
        replayed = restore(fresh, save(session))
        if replayed.events != session.events:
            fail("INVALID_CONFIRMATION", "Confirmation evidence or actor differs from validated replay")
        view = assess(replayed)
    except AdaptationError:
        raise
    except (ValueError, KeyError, TypeError, IndexError) as error:
        fail("INVALID_CONFIRMATION", str(error))
    if not session.confirmed or view.state != State.VALID:
        fail("UNCONFIRMED", "Blocking state: " + view.state.value)

    inputs = inventory(model, view)
    year, source = view.year, view.source
    plan = view.plan
    program = model.program
    docs = {s.kind: s.document for s in model.sources}
    nodes = {kind: {n.id: n for n in doc.source_document.nodes} for kind, doc in docs.items()}
    events = tuple(range(len(session.events)))
    section_by_id = {s.id: s for s in program.content_sections}
    content = {item.id: item for item in view.content}
    if len(content) != len(view.content):
        fail("DUPLICATE_SECTION", "Repeated section identity")
    bound_blocks = {block for boundary in view.boundaries for block in boundary.block_ids}
    selected_blocks = {block for item in view.content for block in item.block_ids}
    selected_refs.update(CanonicalRef("EMBEDDED", b) for b in selected_blocks)
    selected_refs.add(CanonicalRef(source, plan.id))

    def mark(kind, identifier, path):
        destinations.setdefault(CanonicalRef(kind, identifier), []).append(path)

    def scope(original, ref):
        if original and tuple(original) != (year,):
            fail("CROSS_YEAR", "An active object is not exclusively scoped to the selected year", (ref,))

    def provenance(kind, identifier, span, anchor, original_years, parent, topics):
        ref = CanonicalRef(kind, identifier)
        scope(original_years, ref)
        node = nodes[kind].get(anchor)
        if node is None:
            fail("MISSING_SOURCE", "Source anchor absent: " + anchor, (ref,))
        return Provenance(ref, (year,), tuple(original_years), (span,), node.order,
                          CanonicalRef(kind, parent) if parent else None,
                          tuple(CanonicalRef(source, topic) for topic in topics), events)

    # Unknown content inside the chosen range is not silently discarded even when C is VALID.
    plan_blocks = {block for table in docs[source].tables if table.table_id == plan.table_id for block in table.block_ids}
    active_by_source = {"EMBEDDED": selected_blocks | bound_blocks}
    active_by_source.setdefault(source, set()).update(plan_blocks)
    reviewed_mapping = "map_columns" in answers(session)
    for kind, active in active_by_source.items():
        for role in docs[kind].block_roles:
            resolved_by_columns = (reviewed_mapping and kind == source and role.block_id in plan_blocks
                                   and not (kind == "EMBEDDED" and role.block_id in selected_blocks))
            if role.block_id in active and role.role == "UNRESOLVED" and not resolved_by_columns:
                fail("UNRESOLVED", "Selected canonical block has no confirmed semantic role",
                     (CanonicalRef(kind, role.block_id),))
        for region in docs[kind].year_regions:
            if region.channel in ("CONTENT", "PLAN") and set(region.block_ids) & active:
                scope(region.years, CanonicalRef(kind, region.id))

    if plan.years:
        scope(plan.years, CanonicalRef(source, plan.id))
    if any(row.kind == "UNRESOLVED" or (row.kind in ("TOPIC", "SECTION") and row.decision.status != "SUPPORTED") for row in plan.rows):
        fail("UNRESOLVED", "Selected plan contains unresolved rows")
    topic_rows = tuple(row for row in plan.rows if row.kind == "TOPIC")
    if not topic_rows:
        fail("NO_TOPIC_ROWS", "Section/total rows cannot become teaching topics")
    if len({r.id for r in plan.rows}) != len(plan.rows):
        fail("DUPLICATE_ROW", "Plan row IDs are not unique")
    rows_by_id = {r.id: r for r in plan.rows}
    physical_table = next(t for t in docs[source].source_document.tables if t.id == plan.table_id)
    topic_ids = {r.id for r in topic_rows}
    section_topics = {key: [] for key in content}
    for topic_id, section_ids in view.bindings:
        if topic_id not in rows_by_id or not set(section_ids) <= content.keys():
            fail("INVALID_BINDING", "Binding references an absent row or content section")
        if topic_id in topic_ids:
            for section_id in section_ids:
                section_topics[section_id].append(topic_id)

    topics = []
    for i, row in enumerate(topic_rows):
        if row.parent_id and row.parent_id not in rows_by_id:
            fail("MISSING_PARENT", "Plan topic parent is absent")
        meta = provenance(source, row.id, row.source, physical_table.row_ids[row.physical_row],
                          plan.years, row.parent_id, (row.id,))
        topics.append(PlanTopic(row, meta))
        mark(source, row.id, "plan.topics[" + str(i) + "]")
    mark(source, plan.id, "plan.canonical_plan")
    for i, row in enumerate(plan.rows):
        mark(source, row.id, "plan.canonical_plan.rows[" + str(i) + "]")
        for block in (*row.title_block_ids, *row.embedded_block_ids):
            mark(source, block, "plan.canonical_plan.rows[" + str(i) + "].source")
        for _, hours in row.hours:
            for block in hours.source_ids:
                mark(source, block, "plan.canonical_plan.rows[" + str(i) + "].hours")

    sections, fragments = [], []
    for section_id, option in content.items():
        section = section_by_id.get(section_id)
        if section is None:
            fail("UNRESOLVED_SECTION", "Explicit range has not yet resolved canonical ContentSection", (CanonicalRef("EMBEDDED", section_id),))
        if section.parent_id and section.parent_id not in content:
            fail("MISSING_PARENT", "Selected content section lost its parent", (CanonicalRef("EMBEDDED", section_id),))
        if section.decision.status != "SUPPORTED":
            fail("UNRESOLVED", "Selected section is unresolved", (CanonicalRef("EMBEDDED", section_id),))
        meta = provenance("EMBEDDED", section.id, section.source, section.heading_block_id,
                          section.years, section.parent_id, section_topics[section_id])
        sections.append(SectionRecord(section, meta))
    sections.sort(key=lambda s: (s.provenance.order, s.canonical.id))
    for i, record in enumerate(sections):
        mark("EMBEDDED", record.canonical.id, "sections[" + str(i) + "]")
        mark("EMBEDDED", record.canonical.heading_block_id, "sections[" + str(i) + "].heading")

    for fragment in program.source_fragments:
        if fragment.section_id in content and fragment.block_id in bound_blocks:
            scope(fragment.years, CanonicalRef("EMBEDDED", fragment.id))
        if fragment.section_id not in content or fragment.block_id not in content[fragment.section_id].block_ids:
            continue
        if fragment.decision.status != "SUPPORTED":
            fail("UNRESOLVED", "Selected source fragment is unresolved", (CanonicalRef("EMBEDDED", fragment.id),))
        meta = provenance("EMBEDDED", fragment.id, fragment.source, fragment.block_id,
                          fragment.years, fragment.section_id, section_topics[fragment.section_id])
        fragments.append(FragmentRecord(fragment, meta))
    fragments.sort(key=lambda f: (f.provenance.order, f.canonical.source.char_start or 0, f.canonical.id))
    fragment_ids = {f.canonical.id for f in fragments}
    if len(fragment_ids) != len(fragments):
        fail("DUPLICATE_FRAGMENT", "A canonical fragment may be transferred only once")
    fragment_by_id = {f.canonical.id: f for f in fragments}
    for i, record in enumerate(fragments):
        if record.canonical.parent_fragment_id:
            parent_record = fragment_by_id.get(record.canonical.parent_fragment_id)
            parent = parent_record.canonical if parent_record else None
            if parent is None:
                fail("MISSING_PARENT", "Nested fragment lost its parent", (record.provenance.ref,))
            if parent.section_id != record.canonical.section_id:
                fail("CROSS_SECTION_PARENT", "Nested source parent belongs to a different section", (record.provenance.ref,))
            if (parent_record.provenance.order, parent.source.char_start or 0) >= (
                    record.provenance.order, record.canonical.source.char_start or 0):
                fail("INVALID_PARENT_ORDER", "Nested source parent must precede its child", (record.provenance.ref,))
        mark("EMBEDDED", record.canonical.id, "fragments[" + str(i) + "]")
        mark("EMBEDDED", record.canonical.block_id, "fragments[" + str(i) + "].block")
    # Every selected content paragraph must be a heading or an actual fragment.
    represented = {s.canonical.heading_block_id for s in sections} | {f.canonical.block_id for f in fragments}
    paragraphs = {p.id: p for p in program.source_document.paragraphs}
    lost = {b for b in selected_blocks if b in paragraphs and paragraphs[b].normalized_text and b not in represented}
    if lost:
        fail("UNREPRESENTED_CONTENT", "Selected text lacks a distinct canonical fragment",
             (CanonicalRef("EMBEDDED", b) for b in lost))

    plan_meta = provenance(source, plan.id, nodes[source][plan.table_id].source,
                           plan.table_id, plan.years, None, tuple(r.id for r in topic_rows))
    row_provenance = tuple(provenance(source, row.id, row.source, physical_table.row_ids[row.physical_row],
                            plan.years, row.parent_id, (row.id,) if row.kind == "TOPIC" else ())
                           for row in plan.rows)
    confirmed_plan = ConfirmedStudyPlanOverlay(plan, plan_meta, source, year, tuple(topics), row_provenance)
    bindings = []
    for topic_id, section_ids in view.bindings:
        if topic_id not in topic_ids:
            continue
        original = next((b for b in program.bindings if source == "EMBEDDED" and
                         b.topic_id == topic_id and b.section_ids == section_ids), None)
        binding_id = original.id if original else "confirmed-binding:" + digest((source, topic_id, section_ids, model.fingerprint))
        row = rows_by_id[topic_id]
        meta = provenance(source, binding_id, row.source, physical_table.row_ids[row.physical_row],
                          plan.years, row.parent_id, (topic_id,))
        meta = replace(meta, spans=(row.source, *(section_by_id[s].source for s in section_ids)))
        bindings.append(BindingRecord(meta, original.id if original else None,
                        CanonicalRef(source, topic_id), tuple(CanonicalRef("EMBEDDED", s) for s in section_ids),
                        (year,), events))
        if original:
            mark("EMBEDDED", original.id, "bindings[" + str(len(bindings) - 1) + "]")
    # A binding from a SECTION is still preserved verbatim in the session; never promoted to a topic.
    selected_ids = {s.canonical.id for s in sections} | fragment_ids
    scoped_ids = {kind: {**{r.block_id: r.years for r in doc.block_roles},
                        **{v.id: v.years for seq in (doc.content_sections, doc.source_fragments, doc.expected_results, doc.year_regions) for v in seq},
                        **{p.id: p.years for t in doc.tables for p in t.plans}}
                  for kind, doc in docs.items()}
    table_kinds = {kind: {t.table_id: t.classification for t in doc.tables} for kind, doc in docs.items()}
    candidate_ids = {kind: {p.id for t in doc.tables for p in t.plans} for kind, doc in docs.items()}
    content_ids = {kind: {v.id for v in (*doc.content_sections, *doc.source_fragments)} for kind, doc in docs.items()}
    result_ids = {kind: {v.id for v in doc.expected_results} for kind, doc in docs.items()}
    entries = []
    for ref, locations in sorted(inputs.items()):
        paths = tuple(dict.fromkeys(destinations.get(ref, ())))
        if paths:
            disposition, reason = "TRANSFERRED", "SELECTED_CONFIRMED_OBJECT"
        elif ref.source != source and ref.source != "EMBEDDED":
            disposition, reason = "EXCLUDED", "UNSELECTED_PLAN_SOURCE"
        else:
            doc = docs[ref.source]
            scoped = scoped_ids[ref.source].get(ref.id, ())
            if scoped and year not in scoped:
                reason = "OTHER_YEAR"
            elif ref.id in result_ids[ref.source]:
                reason = "EXPECTED_RESULTS_NOT_SOURCE"
            elif ref.source == "EMBEDDED" and ref.id in selected_ids:
                fail("COVERAGE_GAP", "Selected canonical ID has no destination", (ref,))
            elif ref.id in table_kinds[ref.source]:
                reason = "TABLE_PROVENANCE_ONLY:" + table_kinds[ref.source][ref.id]
            elif ref.id in candidate_ids[ref.source]:
                reason = "UNSELECTED_PLAN_CANDIDATE"
            elif ref.id in content_ids[ref.source]:
                reason = "OUTSIDE_CONFIRMED_CONTENT_SELECTION"
            else:
                reason = "EXTRACTION_OR_STRUCTURAL_PROVENANCE_ONLY"
            disposition = "EXCLUDED"
        entries.append(CoverageEntry(ref, locations, disposition, paths, reason))
    return AdapterPacket(VERSION, session, digest(model), confirmed_plan, tuple(sections),
                         tuple(fragments), tuple(bindings), CoverageLedger(tuple(entries)))


def to_worker_json(packet):
    """UI-side transport endpoint. No legacy worker or generation route is called."""
    expected = adapt(packet.session)
    if packet != expected:
        raise ValueError("Adapter packet does not match its canonical input")
    return dumps(packet)


def from_worker_json(payload, *, current_model):
    """Worker-side boundary: revalidate against independently supplied current source state."""
    packet = loads(payload)
    if not isinstance(packet, AdapterPacket) or packet.version != VERSION:
        raise ValueError("Unsupported confirmed ingestion contract")
    if packet.input_digest != digest(current_model) or packet.session.model != current_model:
        raise ValueError("Source changed; confirmation is invalid")
    expected = adapt(packet.session)
    if packet != expected:
        raise ValueError("Corrupt or incomplete adapter overlay")
    return packet
