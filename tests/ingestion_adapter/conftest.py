"""Common structural fixtures; production adapter sees only immutable models."""
import runpy
from pathlib import Path
import pytest
from calendar_pedagoga.ingestion_confirmation import apply, assess, build_model, start, State

helpers = runpy.run_path(str(Path(__file__).parents[1] / "ingestion_confirmation/conftest.py"))
structure, p, table, interpret = (helpers[k] for k in ("structure", "p", "table", "interpret"))


def confirm(model):
    session = start(model)
    view = assess(session)
    if view.state == State.BINDING_AMBIGUOUS:
        # Test action only, identity/order supplied explicitly; never a runtime fallback.
        session = apply(session, "bind_topics", {"bindings": {
            q.topic_id: [view.content[i].id] for i, q in enumerate(view.questions)}})
    assert assess(session).state == State.VALID
    return apply(session, "confirm")


@pytest.fixture
def confirmed():
    return confirm(build_model(structure()))
