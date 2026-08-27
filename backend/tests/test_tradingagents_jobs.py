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
