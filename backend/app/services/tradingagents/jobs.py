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
import time
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
