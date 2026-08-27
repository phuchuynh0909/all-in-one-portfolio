# Background TradingAgents Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A TradingAgents analysis survives its client disconnecting — the run finishes in the background and saves to ClickHouse instead of dying mid-graph.

**Architecture:** A new in-process job registry (`app/services/tradingagents/jobs.py`) owns a dict of running jobs keyed by `(symbol, trade_date)`. Each job drains the runner's event generator on its own daemon thread into an append-only buffer; the SSE endpoint becomes a *subscriber* to that buffer rather than the thing driving the run. Disconnecting ends the subscription and nothing else.

**Tech Stack:** Python 3.11, FastAPI/Starlette `StreamingResponse`, `threading` (no task queue — the codebase has none), pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-background-trading-agent-runs-design.md`

## Global Constraints

- Single uvicorn process in dev and prod (neither passes `--workers`). In-process state is safe; do not add cross-process assumptions.
- `MAX_CONCURRENT_RUNS` default **3**, from env `TRADINGAGENTS_MAX_CONCURRENT_RUNS`.
- Over the cap → HTTP **429**. Unreachable LLM backend → HTTP **503**.
- SSE event vocabulary is **unchanged**: `started`, `node`, `report`, `decision`, `saved`, `error`, `done`. The frontend must need no edit.
- There is **no cancel endpoint**. Stop detaches only.
- `jobs.py` must not import the runner. Work is injected as a zero-argument factory so the registry stays independently testable and app startup keeps paying no TradingAgents import cost.
- A generator may only be closed by the thread iterating it. Shutdown is cooperative — never call `generator.close()` from another thread.
- Backend tests run in the container: `docker compose exec -T backend sh -c "cd /app && python -m pytest <path> -q"`. `tests/test_block_episodes.py` fails collection for unrelated pre-existing reasons; exclude it from full-suite runs.

---

### Task 1: Job object and registry admission

**Files:**
- Create: `backend/app/services/tradingagents/jobs.py`
- Test: `backend/tests/test_tradingagents_jobs.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `TooManyRuns(RuntimeError)`
  - `Job` dataclass with fields `symbol: str`, `trade_date: str`, `events: list[tuple[str, dict]]`, `done: bool`, `stop_requested: bool`, `thread: threading.Thread | None`, `generator: Iterator | None`, `cond: threading.Condition`; property `key -> tuple[str, str]`; methods `append(event: tuple[str, dict]) -> None`, `finish() -> None`
  - `start(symbol: str, trade_date: str, make_events: Callable[[], Iterator[tuple[str, dict]]]) -> Job`
  - `MAX_CONCURRENT_RUNS: int`
  - module-private `_running: dict[tuple[str, str], Job]`, `_lock: threading.Lock`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_tradingagents_jobs.py`:

```python
"""The job registry: admission, dedupe, the concurrency cap, and draining.

A run outlives the request that started it, so the registry — not the SSE
endpoint — owns it. These tests drive it with plain generators rather than a
real graph; ``jobs`` takes the work as a factory precisely so it never has to
know what a TradingAgents run is.
"""

from __future__ import annotations

import threading

import pytest

from app.services.tradingagents import jobs


@pytest.fixture(autouse=True)
def empty_registry():
    """No job may leak from one test into the next."""
    yield
    for job in list(jobs._running.values()):
        job.stop_requested = True
    for job in list(jobs._running.values()):
        if job.thread is not None:
            job.thread.join(timeout=5)
    jobs._running.clear()


def make_stream(events, gate: threading.Event | None = None):
    """A factory producing a generator over ``events``, optionally paced.

    ``gate`` lets a test hold a run open — the registry only counts jobs that
    have not finished, so the cap and dedupe cannot be observed otherwise.
    """

    def factory():
        def generator():
            for event in events:
                if gate is not None and not gate.wait(timeout=5):
                    raise AssertionError("the gate was never opened")
                yield event

        return generator()

    return factory


def wait_for_done(job, timeout: float = 5.0) -> None:
    with job.cond:
        assert job.cond.wait_for(lambda: job.done, timeout=timeout), "job never finished"


def test_a_started_job_drains_the_whole_stream():
    events = [("started", {}), ("node", {"node": "Market Analyst"}), ("done", {})]

    job = jobs.start("VCG", "2026-08-27", make_stream(events))
    wait_for_done(job)

    assert job.events == events


def test_a_second_request_for_the_same_run_is_deduped():
    gate = threading.Event()
    try:
        first = jobs.start("VCG", "2026-08-27", make_stream([("done", {})], gate))
        second = jobs.start("VCG", "2026-08-27", make_stream([("done", {})], gate))

        assert second is first, "the same ticker and date must not run twice at once"
    finally:
        gate.set()
    wait_for_done(first)


def test_a_different_date_is_a_different_run():
    gate = threading.Event()
    try:
        first = jobs.start("VCG", "2026-08-27", make_stream([("done", {})], gate))
        second = jobs.start("VCG", "2026-08-26", make_stream([("done", {})], gate))

        assert second is not first
    finally:
        gate.set()
    wait_for_done(first)
    wait_for_done(second)


def test_the_cap_rejects_a_new_run(monkeypatch):
    monkeypatch.setattr(jobs, "MAX_CONCURRENT_RUNS", 2)
    gate = threading.Event()
    try:
        jobs.start("AAA", "2026-08-27", make_stream([("done", {})], gate))
        jobs.start("BBB", "2026-08-27", make_stream([("done", {})], gate))

        with pytest.raises(jobs.TooManyRuns):
            jobs.start("CCC", "2026-08-27", make_stream([("done", {})], gate))
    finally:
        gate.set()


def test_an_already_running_job_is_returned_even_at_the_cap(monkeypatch):
    # Dedupe is checked before the cap: a second viewer of a run that is already
    # going must not be turned away just because the box is busy.
    monkeypatch.setattr(jobs, "MAX_CONCURRENT_RUNS", 1)
    gate = threading.Event()
    try:
        first = jobs.start("AAA", "2026-08-27", make_stream([("done", {})], gate))

        assert jobs.start("AAA", "2026-08-27", make_stream([("done", {})], gate)) is first
    finally:
        gate.set()


def test_a_finished_job_leaves_the_registry():
    job = jobs.start("VCG", "2026-08-27", make_stream([("done", {})]))
    wait_for_done(job)

    # Otherwise the cap would fill up permanently and dedupe would replay a
    # stale run instead of starting a fresh one.
    assert ("VCG", "2026-08-27") not in jobs._running


def test_a_stream_that_raises_becomes_a_terminal_error_event():
    def factory():
        def generator():
            yield ("started", {})
            raise RuntimeError("the graph fell over")

        return generator()

    job = jobs.start("VCG", "2026-08-27", factory)
    wait_for_done(job)

    assert job.events[0] == ("started", {})
    kind, data = job.events[-1]
    assert kind == "error"
    assert "the graph fell over" in data["error"]


def test_a_job_runs_with_nobody_watching():
    # The entire point: nothing subscribes here and the run still completes.
    job = jobs.start("VCG", "2026-08-27", make_stream([("started", {}), ("done", {})]))
    wait_for_done(job)

    assert job.done
    assert ("done", {}) in job.events
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose exec -T backend sh -c "cd /app && python -m pytest tests/test_tradingagents_jobs.py -q"`
Expected: collection error — `ModuleNotFoundError: No module named 'app.services.tradingagents.jobs'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/tradingagents/jobs.py`:

```python
"""Background execution for TradingAgents analyses.

An analysis takes minutes, and until this module existed it was driven entirely
by the SSE request that asked for it: the route iterated the runner's generator,
so a closed tab closed the generator and the run died partway through the graph
— nineteen agents deep, in the case that prompted this.

The registry separates the two concerns. A job owns the run and drains it on its
own thread into an append-only buffer; the endpoint merely subscribes to that
buffer. Disconnecting ends the subscription and nothing else.

Deliberately knows nothing about TradingAgents: the work arrives as a
zero-argument factory returning an event iterator. That keeps this module
testable with plain generators, and keeps the heavy vendored import out of app
startup — the route still resolves the runner only when a request arrives.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Callable, Iterator

logger = logging.getLogger(__name__)

#: ``(event_type, payload)``, exactly as ``run_analysis_stream`` yields them.
Event = tuple[str, dict]
EventStream = Iterator[Event]

# A run is minutes of LLM calls. Without a cancel endpoint this cap is the only
# thing bounding the cost of a mistyped ticker, so it is deliberately small.
MAX_CONCURRENT_RUNS = int(os.getenv("TRADINGAGENTS_MAX_CONCURRENT_RUNS", "3"))


class TooManyRuns(RuntimeError):
    """Admission refused: the concurrency cap is full."""


@dataclass
class Job:
    """One background analysis and everything it has emitted so far."""

    symbol: str
    trade_date: str
    events: list[Event] = field(default_factory=list)
    done: bool = False
    #: Cooperative shutdown. The worker checks it between events; nothing else
    #: may stop a run, because a generator can only be closed by its own thread.
    stop_requested: bool = False
    thread: threading.Thread | None = None
    generator: EventStream | None = None
    #: Guards ``events`` and ``done``, and wakes subscribers on either change.
    cond: threading.Condition = field(default_factory=threading.Condition)

    @property
    def key(self) -> tuple[str, str]:
        return (self.symbol, self.trade_date)

    def append(self, event: Event) -> None:
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def finish(self) -> None:
        with self.cond:
            self.done = True
            self.cond.notify_all()


_running: dict[tuple[str, str], Job] = {}
_lock = threading.Lock()


def start(
    symbol: str, trade_date: str, make_events: Callable[[], EventStream]
) -> Job:
    """Begin a background run, or return the one already going for this key.

    Dedupe is checked before the cap, so a second viewer of a run in progress is
    never turned away for being over the limit — it is not a new run.
    """
    key = (symbol, trade_date)
    with _lock:
        existing = _running.get(key)
        if existing is not None:
            logger.info("Attaching to the analysis of %s already in flight", symbol)
            return existing
        if len(_running) >= MAX_CONCURRENT_RUNS:
            raise TooManyRuns(
                f"{len(_running)} analyses already running "
                f"(limit {MAX_CONCURRENT_RUNS}); try again when one finishes."
            )
        job = Job(symbol=symbol, trade_date=trade_date)
        _running[key] = job
        job.thread = threading.Thread(
            target=_run,
            args=(job, make_events),
            name=f"tradingagents-{symbol}-{trade_date}",
            daemon=True,
        )
        job.thread.start()
        logger.info("Started a background analysis of %s on %s", symbol, trade_date)
        return job


def _run(job: Job, make_events: Callable[[], EventStream]) -> None:
    """Drain the run into the job's buffer. Runs on the job's own thread."""
    try:
        job.generator = make_events()
        for event in job.generator:
            job.append(event)
            if job.stop_requested:
                break
    except BaseException as exc:  # noqa: BLE001 — a dead thread must not hang subscribers
        logger.exception("Background analysis of %s failed", job.symbol)
        job.append(("error", {"error": str(exc), "node": None, "step": None}))
    finally:
        # This thread owns the generator, so closing it here is what makes the
        # runner's GeneratorExit path (checkpoints preserved, LangSmith run
        # marked cancelled) fire correctly. A generator that ran to exhaustion
        # is already closed and this is a no-op.
        if job.generator is not None:
            try:
                job.generator.close()
            except Exception:  # noqa: BLE001 — teardown must not mask the run
                logger.exception("Failed to close the run for %s", job.symbol)
        _unregister(job)
        # Last: a subscriber woken by this must find the registry already tidy.
        job.finish()


def _unregister(job: Job) -> None:
    with _lock:
        # Identity-checked so a newer run for the same key is never evicted by
        # its predecessor's teardown.
        if _running.get(job.key) is job:
            del _running[job.key]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose exec -T backend sh -c "cd /app && python -m pytest tests/test_tradingagents_jobs.py -q"`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/tradingagents/jobs.py backend/tests/test_tradingagents_jobs.py
git commit -m "feat: job registry for background TradingAgents runs"
```

---

### Task 2: Subscribing to a job

**Files:**
- Modify: `backend/app/services/tradingagents/jobs.py` (append `subscribe`)
- Test: `backend/tests/test_tradingagents_jobs.py` (append)

**Interfaces:**
- Consumes: `Job`, `start` from Task 1.
- Produces: `subscribe(job: Job) -> Iterator[tuple[str, dict]]` — replays every event already buffered, then follows live until the job is done.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_tradingagents_jobs.py`:

```python
# --- subscribing ------------------------------------------------------------


def test_a_subscriber_receives_the_whole_run():
    job = jobs.start(
        "VCG", "2026-08-27", make_stream([("started", {}), ("node", {}), ("done", {})])
    )

    assert list(jobs.subscribe(job)) == [("started", {}), ("node", {}), ("done", {})]


def test_a_late_subscriber_replays_from_the_first_event():
    # The second tab on a run already in progress must see the reports that have
    # already been produced, not just whatever happens next.
    job = jobs.start("VCG", "2026-08-27", make_stream([("started", {}), ("done", {})]))
    wait_for_done(job)

    assert list(jobs.subscribe(job)) == [("started", {}), ("done", {})]


def test_closing_a_subscriber_leaves_the_job_running():
    # The regression this whole change exists to prevent. Before it, closing the
    # consumer closed the runner's generator and killed the analysis.
    gate = threading.Event()
    events = [("started", {}), ("node", {}), ("done", {})]
    job = jobs.start("VCG", "2026-08-27", make_stream(events, gate))

    stream = jobs.subscribe(job)
    gate.set()
    assert next(stream) == ("started", {})
    stream.close()  # the client hung up

    wait_for_done(job)
    assert job.events == events, "the run must finish regardless of who is watching"


def test_two_subscribers_both_see_everything():
    gate = threading.Event()
    job = jobs.start("VCG", "2026-08-27", make_stream([("started", {}), ("done", {})], gate))
    gate.set()

    first = list(jobs.subscribe(job))
    second = list(jobs.subscribe(job))

    assert first == second == [("started", {}), ("done", {})]


def test_a_subscriber_of_a_failing_run_sees_the_error_and_stops():
    def factory():
        def generator():
            yield ("started", {})
            raise RuntimeError("boom")

        return generator()

    job = jobs.start("VCG", "2026-08-27", factory)

    received = list(jobs.subscribe(job))

    assert received[0] == ("started", {})
    assert received[-1][0] == "error"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose exec -T backend sh -c "cd /app && python -m pytest tests/test_tradingagents_jobs.py -q -k 'subscriber or subscribers'"`
Expected: FAIL with `AttributeError: module 'app.services.tradingagents.jobs' has no attribute 'subscribe'`

- [ ] **Step 3: Write the implementation**

Append to `backend/app/services/tradingagents/jobs.py`:

```python
def subscribe(job: Job) -> EventStream:
    """Yield everything the job has emitted, then follow it live until done.

    Always starts from event zero, so attaching to a run already in progress
    replays the reports it has already produced rather than dropping the viewer
    into the middle.

    Closing this generator ends the subscription and nothing else — that is the
    whole point of the module. The job keeps running.

    The wait is given a timeout so a subscriber cannot be stranded forever by a
    worker that died without notifying; every state change notifies, so the
    timeout is a backstop rather than a poll interval.
    """
    index = 0
    while True:
        with job.cond:
            while len(job.events) == index and not job.done:
                job.cond.wait(timeout=1.0)
            pending = job.events[index:]
            index += len(pending)
            exhausted = job.done and index == len(job.events)

        # Yielded outside the lock: a slow consumer must not block the worker
        # from appending, nor other subscribers from reading.
        for event in pending:
            yield event

        if exhausted:
            return
```

- [ ] **Step 4: Run the whole file to verify it passes**

Run: `docker compose exec -T backend sh -c "cd /app && python -m pytest tests/test_tradingagents_jobs.py -q"`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/tradingagents/jobs.py backend/tests/test_tradingagents_jobs.py
git commit -m "feat: subscribe to a running job's event buffer"
```

---

### Task 3: Cooperative shutdown

**Files:**
- Modify: `backend/app/services/tradingagents/jobs.py` (append `shutdown`)
- Modify: `backend/app/main.py` (add a shutdown event handler beside the existing `startup` one)
- Test: `backend/tests/test_tradingagents_jobs.py` (append)

**Interfaces:**
- Consumes: `Job`, `start`, `_running`, `_lock` from Task 1.
- Produces: `shutdown(timeout: float = 5.0) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_tradingagents_jobs.py`:

```python
# --- shutdown ---------------------------------------------------------------


def test_shutdown_stops_a_running_job():
    gate = threading.Event()
    events = [("started", {}), ("node", {}), ("done", {})]
    job = jobs.start("VCG", "2026-08-27", make_stream(events, gate))
    gate.set()
    with job.cond:
        job.cond.wait_for(lambda: job.events, timeout=5)

    jobs.shutdown(timeout=5)

    assert job.done
    assert not jobs._running


def test_shutdown_does_not_hang_on_a_job_that_never_notices():
    # A worker only checks the flag between events, and an event is a whole
    # graph node. Shutdown must give up rather than block the process exit;
    # the threads are daemons and the run is resumable from its checkpoint.
    gate = threading.Event()  # never set: the job is wedged on its first event
    job = jobs.start("VCG", "2026-08-27", make_stream([("done", {})], gate))
    try:
        jobs.shutdown(timeout=0.2)

        assert not job.done, "the job is still stuck, which is expected"
    finally:
        gate.set()
        if job.thread is not None:
            job.thread.join(timeout=5)


def test_shutdown_with_no_jobs_is_harmless():
    jobs.shutdown(timeout=0.1)
```

And create `backend/tests/test_tradingagents_shutdown_hook.py`:

```python
"""The app must actually call jobs.shutdown() — the hook is easy to forget."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.tradingagents import jobs


def test_app_shutdown_drains_running_jobs(monkeypatch):
    called: list[bool] = []
    monkeypatch.setattr(jobs, "shutdown", lambda *a, **k: called.append(True))

    # Entering and leaving the context manager fires startup and shutdown.
    with TestClient(app):
        pass

    assert called, "leaving the app without draining jobs strands running threads"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose exec -T backend sh -c "cd /app && python -m pytest tests/test_tradingagents_jobs.py -q -k shutdown; python -m pytest tests/test_tradingagents_shutdown_hook.py -q"`
Expected: `AttributeError: module ... has no attribute 'shutdown'`, then the hook test fails on an empty `called`

- [ ] **Step 3: Write the implementations**

Append to `backend/app/services/tradingagents/jobs.py` (and add `import time` to the imports at the top, after `import threading`):

```python
def shutdown(timeout: float = 5.0) -> None:
    """Ask every running job to stop, and wait briefly for the threads.

    Cooperative by necessity, not by preference: a generator may only be closed
    by the thread iterating it — calling ``close()`` on one parked in ``next()``
    raises ``ValueError: generator already executing`` — so this sets a flag and
    each worker closes its own generator on the way out.

    A worker only checks that flag between events, and an event is a whole graph
    node, so a job may not notice before the deadline. That is accepted: the
    threads are daemons so the process still exits, and an interrupted run
    resumes from its checkpoint on the next request for the same ticker and date.
    """
    with _lock:
        running = list(_running.values())
    if not running:
        return

    logger.info("Shutting down with %d analysis(es) in flight", len(running))
    for job in running:
        job.stop_requested = True
        # Wakes any subscriber parked on the condition so it can notice too.
        with job.cond:
            job.cond.notify_all()

    deadline = time.monotonic() + timeout
    for job in running:
        if job.thread is None:
            continue
        job.thread.join(max(0.0, deadline - time.monotonic()))
        if job.thread.is_alive():
            logger.warning(
                "Analysis of %s did not stop in time; it is resumable from its "
                "checkpoint",
                job.symbol,
            )
```

In `backend/app/main.py`, immediately after the existing `startup` handler and before `return app`, add:

```python
    @app.on_event("shutdown")
    async def shutdown():
        # Analyses now outlive the request that started them, so leaving without
        # this strands worker threads mid-graph. Imported here so app startup
        # keeps paying no TradingAgents import cost.
        from app.services.tradingagents import jobs

        jobs.shutdown()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose exec -T backend sh -c "cd /app && python -m pytest tests/test_tradingagents_jobs.py tests/test_tradingagents_shutdown_hook.py -q"`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/tradingagents/jobs.py backend/app/main.py backend/tests/test_tradingagents_jobs.py backend/tests/test_tradingagents_shutdown_hook.py
git commit -m "feat: cooperative shutdown for in-flight analyses"
```

---

### Task 4: Rewire the SSE endpoint

**Files:**
- Modify: `backend/app/api/v1/routes/trading_agents.py:221-275` (the whole `analyze_stream` body)
- Test: `backend/tests/test_tradingagents_route.py`

**Interfaces:**
- Consumes: `jobs.start`, `jobs.subscribe`, `jobs.TooManyRuns` from Tasks 1-2.
- Produces: no new Python interface. HTTP contract: `503` when `check_backend()` fails, `429` when over the cap, otherwise an unchanged SSE stream.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_tradingagents_route.py`:

```python
"""The analyze/stream endpoint's contract.

The endpoint no longer drives the run — it starts a job and subscribes. What is
worth pinning here is the shape that changed: the two failure codes, and that a
disconnect is no longer fatal.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.tradingagents import jobs

URL = "/api/v1/trading-agents/analyze/stream"
BODY = {"symbol": "VCG", "trade_date": "2026-08-27", "analysts": ["market"]}


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def backend_is_up(monkeypatch):
    from app.services.tradingagents import runner

    monkeypatch.setattr(runner, "check_backend", lambda *a, **k: (True, "ok"))


def test_an_unreachable_backend_is_a_503(client, monkeypatch):
    from app.services.tradingagents import runner

    monkeypatch.setattr(runner, "check_backend", lambda *a, **k: (False, "Ollama down"))
    started: list = []
    monkeypatch.setattr(jobs, "start", lambda *a, **k: started.append(a))

    response = client.post(URL, json=BODY)

    assert response.status_code == 503
    assert "Ollama down" in response.json()["detail"]
    # A run that cannot start must not occupy a slot in the registry.
    assert not started


def test_over_the_cap_is_a_429(client, monkeypatch):
    def refuse(*args, **kwargs):
        raise jobs.TooManyRuns("3 analyses already running (limit 3)")

    monkeypatch.setattr(jobs, "start", refuse)

    response = client.post(URL, json=BODY)

    assert response.status_code == 429
    assert "already running" in response.json()["detail"]


def test_the_stream_carries_the_job_events(client, monkeypatch):
    job = jobs.Job(symbol="VCG", trade_date="2026-08-27")
    monkeypatch.setattr(jobs, "start", lambda *a, **k: job)
    monkeypatch.setattr(
        jobs,
        "subscribe",
        lambda j: iter([("started", {"symbol": "VCG"}), ("done", {})]),
    )

    response = client.post(URL, json=BODY)

    assert response.status_code == 200
    body = response.text
    assert "event: started" in body
    assert 'data: {"symbol": "VCG"}' in body
    assert "event: done" in body


def test_the_endpoint_does_not_close_the_job(client, monkeypatch):
    # The old code closed the runner's generator in a finally. If anything still
    # does, this run would be cut short rather than left alone.
    job = jobs.Job(symbol="VCG", trade_date="2026-08-27")
    monkeypatch.setattr(jobs, "start", lambda *a, **k: job)
    monkeypatch.setattr(jobs, "subscribe", lambda j: iter([("done", {})]))

    client.post(URL, json=BODY)

    assert not job.stop_requested
    assert not job.done
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose exec -T backend sh -c "cd /app && python -m pytest tests/test_tradingagents_route.py -q"`
Expected: FAIL — the current endpoint returns 200 with an SSE `error` event instead of 503, and never consults `jobs`

- [ ] **Step 3: Write the implementation**

Replace the body of `analyze_stream` in `backend/app/api/v1/routes/trading_agents.py` with:

```python
@router.post("/analyze/stream")
def analyze_stream(request: AnalyzeRequest) -> StreamingResponse:
    """Start a multi-agent analysis and stream its progress via SSE.

    The response streams the run but no longer drives it: the job outlives this
    request, so closing the connection detaches this viewer and leaves the
    analysis to finish and save itself. There is deliberately no cancel — a
    started run always completes, and the concurrency cap is what bounds the cost.
    """
    # Imported here so a heavy/broken TradingAgents install can't crash app
    # startup — only requests to this endpoint pay the import cost.
    from app.services.tradingagents import jobs
    from app.services.tradingagents.runner import (
        DEFAULT_ANALYSTS,
        check_backend,
        run_analysis_stream,
    )

    symbol = request.symbol.strip().upper()
    trade_date = request.trade_date or date.today().strftime("%Y-%m-%d")
    # Fall back to the runner's list rather than a second hardcoded copy, so
    # enabling an analyst there actually reaches this endpoint.
    analysts = tuple(request.analysts) if request.analysts else DEFAULT_ANALYSTS
    # Raised before the StreamingResponse so a bad key is a 400, not an SSE
    # error event the caller has to dig out of the stream.
    analyst_models = _validated_analyst_models(request)

    logger.info("TradingAgents analyze: {} on {}", symbol, trade_date)

    # Checked before the job is created, for the same reason: an unreachable LLM
    # backend is a 503 the caller can act on, not an error event mid-stream, and
    # a run that cannot start must not occupy one of the concurrency slots.
    ok, message = check_backend()
    if not ok:
        raise HTTPException(status_code=503, detail=message)

    def make_events():
        return run_analysis_stream(
            symbol,
            trade_date,
            analysts,
            deep_think_llm=request.deep_think_llm,
            quick_think_llm=request.quick_think_llm,
            analyst_llms=analyst_models,
        )

    try:
        job = jobs.start(symbol, trade_date, make_events)
    except jobs.TooManyRuns as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    def event_generator() -> Generator[str, None, None]:
        # No try/finally closing anything: this generator owns the subscription,
        # not the run. A disconnect closes it at the yield and the job carries on.
        for event_type, data in jobs.subscribe(job):
            yield _sse(event_type, data)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose exec -T backend sh -c "cd /app && python -m pytest tests/test_tradingagents_route.py -q"`
Expected: 4 passed

- [ ] **Step 5: Run the full suite to check nothing regressed**

Run: `docker compose exec -T backend sh -c "cd /app && python -m pytest tests/ -q --ignore=tests/test_block_episodes.py"`
Expected: all pass. The cancellation tests from earlier still pass — `run_analysis_stream`'s `GeneratorExit` path is untouched; only its trigger moved from "client disconnected" to "app shutting down".

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/routes/trading_agents.py backend/tests/test_tradingagents_route.py
git commit -m "feat: analyses survive client disconnect"
```

---

### Task 5: Document the new behaviour

**Files:**
- Modify: `backend/app/services/tradingagents/README.md`
- Modify: `backend/app/services/tradingagents/runner.py` — the `run_analysis_stream` docstring's cancellation note

**Interfaces:**
- Consumes: everything above. Produces: nothing executable.

- [ ] **Step 1: Update the service README**

Add to `backend/app/services/tradingagents/README.md`, before the "Cancelled runs" section:

```markdown
**Background runs.** `analyze/stream` starts a job (`jobs.py`) and subscribes to
it; the job drains the runner on its own thread. Closing the connection detaches
the viewer — the analysis finishes and saves to ClickHouse regardless, and is
found afterwards in the history list. There is no cancel endpoint: a started run
always completes, so `TRADINGAGENTS_MAX_CONCURRENT_RUNS` (default 3) is what
bounds the cost of a mistyped ticker. Over the cap is a 429; an unreachable LLM
backend is a 503. Asking for a ticker+date already in flight attaches to that run
and replays it from its first event rather than starting a second.

Jobs are in-process and die with it — including on every `--reload` in dev. The
checkpointer makes that recoverable: a re-run resumes from the last completed
node.
```

- [ ] **Step 2: Update the runner docstring's cancellation note**

In `backend/app/services/tradingagents/runner.py`, the comment block above `stream = graph.stream(...)` says a disconnect closes the generator. Amend that sentence to name the real trigger:

```
        # ... An SSE client that goes away no longer reaches here — the job in
        # jobs.py owns the run — so the only thing that closes this generator
        # now is application shutdown, which the job's worker performs from this
        # same thread. Python raises GeneratorExit at whichever yield is parked.
```

- [ ] **Step 3: Verify the docs match the code**

Run: `docker compose exec -T backend sh -c "cd /app && python -m pytest tests/ -q --ignore=tests/test_block_episodes.py"`
Expected: all pass (docs-only change; this is a guard against an accidental edit)

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/tradingagents/README.md backend/app/services/tradingagents/runner.py
git commit -m "docs: background run behaviour and its cancellation trigger"
```

---

## Manual verification

After Task 4, confirm the actual behaviour end to end — the tests use fake streams, so this is the first time a real run is involved.

1. Start an analysis from the UI.
2. Wait for two or three `node` events, then close the tab.
3. Watch the logs: `docker compose logs -f backend | grep -E "node .*: (start|ok)|abandoned|Started a background"`.
4. Expect the per-node lines to **keep arriving** after the tab is gone, and **no** `abandoned by the client` line.
5. When it finishes, the analysis appears in the history list with its final decision.
