"""What a cancelled streaming analysis records in LangSmith.

An SSE client that goes away closes the runner's generator, and Python raises
GeneratorExit at whichever yield it was parked on. That unwinds into LangGraph's
Pregel loop, whose ``except BaseException`` hands the cancellation to the
LangChain callback manager as a *chain error* — so the root run is stamped with
``_get_stacktrace``'s rendering of three chained GeneratorExit tracebacks and
the abandoned analysis is indistinguishable from one that actually failed.

The runner therefore repairs the run itself once the tracer's own export has
landed: a one-line reason in place of the stack trace, and a ``cancelled`` tag
so these can be excluded from an error-rate query.
"""

from __future__ import annotations

import sys
import types
import uuid

import pytest

from app.services.tradingagents import runner
from tests.tradingagents_harness import install, install_langsmith, node


@pytest.fixture
def trace_log(monkeypatch):
    """LangSmith side effects, in the order the runner performs them."""
    return install_langsmith(monkeypatch)


# --- the patch itself -------------------------------------------------------


def test_cancelled_run_is_patched_with_a_readable_reason(trace_log):
    run_id = uuid.uuid4()

    runner._mark_langsmith_cancelled(run_id, "VCG", 7, ["tradingagents", "VCG"])

    (kind, patched_id, fields) = trace_log[0]
    assert (kind, patched_id) == ("patch", run_id)
    # The whole point: a reason a human can read, not a stack trace.
    assert "GeneratorExit" not in fields["error"]
    assert "cancel" in fields["error"].lower()
    assert "7" in fields["error"]
    # The run stays findable by ticker; "cancelled" is what makes it filterable.
    assert fields["tags"] == ["tradingagents", "VCG", "cancelled"]


def test_marking_a_cancelled_run_never_raises(monkeypatch):
    class _Exploding:
        def __init__(self, **kwargs) -> None:
            raise RuntimeError("LangSmith is unreachable")

    module = types.ModuleType("langsmith")
    module.Client = _Exploding
    monkeypatch.setitem(sys.modules, "langsmith", module)

    # This runs from a ``finally`` while GeneratorExit is in flight. Raising here
    # would displace the cancellation that is being carried out.
    runner._mark_langsmith_cancelled(uuid.uuid4(), "VCG", 3, [])


# --- the runner wiring ------------------------------------------------------


def test_client_disconnect_marks_the_trace_cancelled(monkeypatch, trace_log):
    graph = install(
        monkeypatch, [node("Market Analyst"), node("Bull Researcher"), node("Trader")]
    )
    events = runner.run_analysis_stream("VCG", "2026-08-27", ("market",))

    assert next(events)[0] == "started"
    assert next(events)[1]["node"] == "Market Analyst"
    assert next(events)[1]["node"] == "Bull Researcher"

    events.close()  # the SSE client hung up

    assert graph.stream_obj.closed, "the graph stream must be unwound from this thread"
    assert [entry[0] for entry in trace_log] == ["flush", "patch"], (
        "the patch has to land after the tracer's own export, or it is the one "
        "that gets overwritten"
    )
    _, run_id, fields = trace_log[1]
    assert isinstance(run_id, uuid.UUID)
    assert "2" in fields["error"], "the reason names how far the run got"
    assert "cancelled" in fields["tags"]


def test_a_cancelled_run_is_not_also_tagged_as_failed(monkeypatch, trace_log):
    # Cancellation reaches the node tracker as an on_chain_error too, so the two
    # paths have to stay distinguishable or every abandoned run reads as a crash.
    install(monkeypatch, [node("Market Analyst"), node("Bull Researcher")])
    events = runner.run_analysis_stream("VCG", "2026-08-27", ("market",))
    next(events), next(events)

    events.close()

    _, _, fields = trace_log[1]
    assert not any(tag.startswith("failed") for tag in fields["tags"])


def test_a_completed_run_is_not_marked_cancelled(monkeypatch, trace_log):
    install(monkeypatch, [node("Market Analyst")])

    events = list(runner.run_analysis_stream("VCG", "2026-08-27", ("market",)))

    assert [kind for kind, _ in events][-1] == "done"
    assert [entry[0] for entry in trace_log] == ["flush"]
