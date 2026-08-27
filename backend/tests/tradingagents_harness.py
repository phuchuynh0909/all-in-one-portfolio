"""Fakes for driving ``run_analysis_stream`` without a real TradingAgents graph.

Building a real graph needs models, keys and minutes; these stubs stand in for
everything the runner touches so the streaming path — node reporting, failure
attribution, cancellation, LangSmith bookkeeping — can be tested in
milliseconds. The graph is driven by a small script so a test can say what the
run does, step by step, and nothing else has to be arranged.

Script items, in the order the graph performs them:

    node(name)             one node runs and succeeds
    state(dict)            the accumulated state after a step
    fail(name, exception)  one node runs and raises; the run dies there
"""

from __future__ import annotations

import contextlib
import sys
import types
import uuid

# Importing this installs the sys.path shim for ``import tradingagents``.
from app.services.tradingagents import runner, store

# Imported for real, here, before anything below is stubbed. The runner imports
# it during a run, and once it is in sys.modules that import resolves straight
# from the cache — which it has to, because the agent_utils stub installed later
# would otherwise derail the vendor's package __init__ chain on the way to it.
from tradingagents.graph.instrumentation import NodeProgressLogger

# --- the script -------------------------------------------------------------


def node(name: str) -> tuple:
    return ("node", name)


def state(values: dict) -> tuple:
    return ("state", values)


def fail(name: str, exception: BaseException) -> tuple:
    return ("fail", name, exception)


# --- the graph --------------------------------------------------------------


def _tracker_of(args: dict):
    """The NodeProgressLogger the runner attached, if it attached one."""
    callbacks = (args.get("config") or {}).get("callbacks") or []
    for callback in callbacks:
        if isinstance(callback, NodeProgressLogger):
            return callback
    return None


class FakeStream:
    """Stands in for ``Pregel.stream``, which yields ``(mode, chunk)`` tuples.

    Also drives the runner's callback handler the way LangGraph would, so the
    node names and durations the runner reads back are produced by the real
    ``NodeProgressLogger`` rather than mocked.
    """

    def __init__(self, script, tracker) -> None:
        self._script = iter(script)
        self._tracker = tracker
        self.closed = False

    def __iter__(self) -> "FakeStream":
        return self

    def __next__(self) -> tuple:
        while True:
            item = next(self._script)  # StopIteration ends the run
            kind = item[0]

            if kind == "state":
                return ("values", item[1])

            if kind == "node":
                name = item[1]
                self._run_node(name)
                return ("updates", {name: {}})

            if kind == "fail":
                _, name, exception = item
                run_id = uuid.uuid4()
                self._start(name, run_id)
                if self._tracker is not None:
                    self._tracker.on_chain_error(exception, run_id=run_id)
                raise exception

            raise AssertionError(f"unknown script item: {item!r}")

    def _run_node(self, name: str) -> None:
        run_id = uuid.uuid4()
        self._start(name, run_id)
        if self._tracker is not None:
            self._tracker.on_chain_end({}, run_id=run_id)

    def _start(self, name: str, run_id) -> None:
        if self._tracker is None:
            return
        self._tracker.on_chain_start(
            {}, {}, run_id=run_id, metadata={"langgraph_node": name, "langgraph_step": 1}
        )

    def close(self) -> None:
        self.closed = True


class FakeGraph:
    def __init__(self, script) -> None:
        self._script = script
        self.args: dict = {}
        self.stream_obj: FakeStream | None = None

    def stream(self, init_state, **args) -> FakeStream:
        self.args = args
        self.stream_obj = FakeStream(self._script, _tracker_of(args))
        return self.stream_obj


# --- the environment --------------------------------------------------------


def install(monkeypatch, script) -> FakeGraph:
    """Stub out everything ``run_analysis_stream`` needs; return the fake graph."""
    cfg = {
        "llm_provider": "openai",
        "output_language": "en",
        "checkpoint_enabled": False,
        "analyst_llms": {},
        "llm_roles": {
            "deep": {"provider": "openai", "model": "gpt-4o"},
            "quick": {"provider": "openai", "model": "gpt-4o-mini"},
        },
    }
    graph = FakeGraph(script)

    class _FakeTA:
        memory_log = types.SimpleNamespace(get_past_context=lambda symbol: "")

        def __init__(self, **kwargs) -> None:
            self.graph = graph
            self.propagator = types.SimpleNamespace(
                create_initial_state=lambda *a, **k: {},
                # Mirrors the real Propagator: callbacks land in the config.
                get_graph_args=lambda callbacks=None: {
                    "stream_mode": "values",
                    "config": (
                        {"recursion_limit": 100, "callbacks": callbacks}
                        if callbacks
                        else {"recursion_limit": 100}
                    ),
                },
            )

        def process_signal(self, decision):
            return "HOLD"

    trading_graph = types.ModuleType("tradingagents.graph.trading_graph")
    trading_graph.TradingAgentsGraph = _FakeTA
    agent_utils = types.ModuleType("tradingagents.agents.utils.agent_utils")
    agent_utils.build_instrument_context = lambda symbol, asset_type: ""
    for name, module in {
        "tradingagents.graph.trading_graph": trading_graph,
        "tradingagents.agents.utils.agent_utils": agent_utils,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setattr(runner, "register_vn_vendor", lambda: None)
    monkeypatch.setattr(runner, "patch_empty_response_recovery", lambda: None)
    monkeypatch.setattr(runner, "patch_structured_output_recovery", lambda: None)
    monkeypatch.setattr(runner, "build_config", lambda **kwargs: cfg)
    monkeypatch.setattr(runner, "register_configured_models", lambda c: None)
    monkeypatch.setattr(runner, "apply_structured_method", lambda c: None)
    monkeypatch.setattr(runner, "apply_model_overrides", contextlib.nullcontext)
    monkeypatch.setattr(
        runner, "past_runs", types.SimpleNamespace(build_past_context=lambda s, d: "")
    )
    monkeypatch.setattr(store, "save_analysis", lambda **kwargs: "analysis-1")
    return graph


def install_langsmith(monkeypatch) -> list[tuple]:
    """Capture LangSmith side effects in the order the runner performs them."""
    log: list[tuple] = []

    class _Client:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def update_run(self, run_id, **fields) -> None:
            log.append(("patch", run_id, fields))

    module = types.ModuleType("langsmith")
    module.Client = _Client
    monkeypatch.setitem(sys.modules, "langsmith", module)
    monkeypatch.setattr(runner, "langsmith_enabled", lambda: True)
    monkeypatch.setattr(runner, "_langsmith_trace_url", lambda run_id: "")
    monkeypatch.setattr(runner, "_flush_langsmith", lambda: log.append(("flush",)))
    return log
