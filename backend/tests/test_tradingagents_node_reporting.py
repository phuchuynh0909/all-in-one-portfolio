"""Which agent is running, and which one broke.

``stream_mode="values"`` hands back accumulated state and nothing else, so the
runner could only ever count chunks — every progress event said "step 3" and a
failure said nothing at all about where it happened. Streaming ``updates``
alongside ``values`` names the node that just ran, and the NodeProgressLogger
attached to the run names the one that raised.
"""

from __future__ import annotations

import logging

import pytest

from app.services.tradingagents import runner
from tests.tradingagents_harness import fail, install, install_langsmith, node, state


def _events(symbol="VCG", trade_date="2026-08-27"):
    return list(runner.run_analysis_stream(symbol, trade_date, ("market",)))


def _of_type(events, kind):
    return [data for event_type, data in events if event_type == kind]


# --- progress ---------------------------------------------------------------


def test_node_events_carry_the_agent_name(monkeypatch):
    install(monkeypatch, [node("Market Analyst"), node("Bull Researcher")])

    nodes = _of_type(_events(), "node")

    assert [n["node"] for n in nodes] == ["Market Analyst", "Bull Researcher"]
    assert [n["step"] for n in nodes] == [1, 2]


def test_node_events_carry_the_time_the_node_took(monkeypatch):
    install(monkeypatch, [node("Market Analyst")])

    (first,) = _of_type(_events(), "node")

    # Measured by the callback handler, which is the only thing that sees the
    # node's start; the stream only ever reports completions.
    assert isinstance(first["duration_ms"], int)


def test_state_chunks_still_produce_reports(monkeypatch):
    # The values stream is what section reports are built from, so adding the
    # updates stream must not cost them.
    install(
        monkeypatch,
        [node("Market Analyst"), state({"market_report": "RSI is 71"})],
    )

    reports = _of_type(_events(), "report")

    assert any(r["section"] == "market" and "RSI is 71" in r["content"] for r in reports)


def test_a_clean_run_still_reaches_a_decision(monkeypatch):
    install(
        monkeypatch,
        [node("Portfolio Manager"), state({"final_trade_decision": "HOLD — wait"})],
    )

    events = _events()

    assert [kind for kind, _ in events][-1] == "done"
    (decision,) = _of_type(events, "decision")
    assert decision["signal"] == "HOLD"


# --- failure ----------------------------------------------------------------


def test_a_failing_node_is_named_in_the_error_event(monkeypatch):
    install(
        monkeypatch,
        [node("Market Analyst"), fail("Bull Researcher", ValueError("model refused"))],
    )

    (error,) = _of_type(_events(), "error")

    assert error["node"] == "Bull Researcher"
    assert "model refused" in error["error"]
    # How far the run got, so a failure on step 1 reads differently from one
    # eleven nodes deep.
    assert error["step"] == 1


def test_a_failing_run_logs_the_node_it_died_on(monkeypatch, caplog):
    install(monkeypatch, [node("Market Analyst"), fail("Trader", RuntimeError("boom"))])

    with caplog.at_level(logging.ERROR, logger="app.services.tradingagents.runner"):
        _events()

    assert "Trader" in caplog.text
    assert "VCG" in caplog.text


def test_a_failure_with_no_attributable_node_still_reports(monkeypatch):
    # Not every failure happens inside a node — the graph itself can fail to
    # start. The error event must still be well formed.
    class _Exploding(list):
        def __iter__(self):
            raise RuntimeError("graph would not start")

    install(monkeypatch, _Exploding())

    (error,) = _of_type(_events(), "error")

    assert error["node"] is None
    assert "graph would not start" in error["error"]


def test_a_failing_node_is_tagged_on_the_langsmith_run(monkeypatch):
    trace_log = install_langsmith(monkeypatch)
    install(monkeypatch, [fail("News Analyst", RuntimeError("429 from provider"))])

    _events()

    assert [entry[0] for entry in trace_log] == ["flush", "patch"]
    _, _, fields = trace_log[1]
    assert "failed:News Analyst" in fields["tags"]
    # The error itself is left alone: LangGraph already recorded the real
    # exception on the run, and it is more informative than anything set here.
    assert fields.get("error") is None


def test_a_clean_run_is_not_tagged(monkeypatch):
    trace_log = install_langsmith(monkeypatch)
    install(monkeypatch, [node("Market Analyst")])

    _events()

    assert [entry[0] for entry in trace_log] == ["flush"]


# --- wiring -----------------------------------------------------------------


def test_the_graph_is_streamed_for_both_values_and_updates(monkeypatch):
    graph = install(monkeypatch, [node("Market Analyst")])

    _events()

    assert graph.args["stream_mode"] == ["values", "updates"]


def test_the_progress_logger_is_attached_to_the_run(monkeypatch):
    from tradingagents.graph.instrumentation import NodeProgressLogger

    graph = install(monkeypatch, [node("Market Analyst")])

    _events()

    callbacks = graph.args["config"]["callbacks"]
    assert any(isinstance(c, NodeProgressLogger) for c in callbacks)
    # The vendor's own config must survive being handed a callback.
    assert graph.args["config"]["recursion_limit"] == 100
