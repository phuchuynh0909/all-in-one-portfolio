# Background TradingAgents runs

**Date:** 2026-08-27
**Status:** approved, not yet implemented

## Problem

A multi-agent analysis takes minutes. It is driven entirely by the SSE request
that started it: `run_analysis_stream` is a generator, and the route iterates it
inside a `StreamingResponse`. When the client goes away — the Stop button, a
navigation, a dropped connection, a proxy timeout — Starlette closes the route's
generator, `finally: events.close()` closes the runner's, and the run dies.

This is observable in production:

```
INFO | app.services.tradingagents.runner:run_analysis_stream:1504
     - Analysis of BCM abandoned by the client after 19 step(s)
```

Nineteen agents' worth of LLM calls, discarded because a browser tab closed.

## Goal

Starting a run and watching a run become separate concerns. A disconnect
detaches the viewer; the run continues to completion and saves to ClickHouse
like any other, where it is found in the history list.

### Decisions taken

| Question | Decision |
|---|---|
| Coming back to a run in progress | It finished; find it in history. No discovery UI, no auto-reattach on page load. |
| The Stop button | Detaches only. There is no cancel endpoint; a started run always runs to completion. |
| Concurrency | Dedupe by ticker+date, plus a small cap on concurrent runs; over the cap is a 429. |

These reconcile because a job that buffers its own events in order to survive
having no listener can be *attached to* almost for free. What was declined is
the discovery surface, not the attachment mechanism.

### Non-goals

- Surviving a process restart. Runs are in-process and die with it.
- Listing in-flight runs, or reattaching to one automatically.
- Cancelling a run once started.
- A second worker service, a queue, or a job table.

## Approach

**In-process job registry, one worker thread per run.**

Considered and rejected:

- *A bare detached thread with no registry* — no dedupe and no cap, both of
  which were asked for.
- *A dedicated worker service with a DB-backed queue* — survives restarts and
  scales past one box, but adds a compose service, a queue, a table and a second
  image carrying the vendored framework and its LLM config. Real cost, for a
  feature with one user, before any benefit is felt.

The registry fits what already exists: a single uvicorn process in both dev and
prod (neither passes `--workers`), no task queue anywhere in the codebase, and a
runner whose SQLite checkpointer must be owned by one thread from start to
finish — which a thread-per-job satisfies exactly as the current
Starlette-threadpool execution does.

Its weakness is that jobs die on process restart. That is materially softened by
machinery already in place: the checkpointer resumes an interrupted run from its
last completed node, keyed by ticker+date+graph-shape, and the runner already
preserves checkpoint rows on cancellation for precisely this reason.

## Design

### `app/services/tradingagents/jobs.py` (new)

```python
class TooManyRuns(RuntimeError): ...

@dataclass
class Job:
    symbol: str
    trade_date: str
    events: list[tuple[str, dict]]   # append-only; every event emitted so far
    done: bool
    stop_requested: bool                  # cooperative shutdown flag
    thread: threading.Thread | None       # set once the worker is spawned
    generator: Iterator | None            # the runner generator, for shutdown
    _cond: threading.Condition
```

Module state: `_running: dict[tuple[str, str], Job]` and a lock. The key is
`(symbol, trade_date)`.

```
start(symbol, trade_date, analysts, *, deep_think_llm, quick_think_llm,
      analyst_llms) -> Job

    under the lock:
        job for this key already running?  -> return it            (dedupe)
        len(_running) >= MAX_CONCURRENT_RUNS and key is new
                                           -> raise TooManyRuns
        otherwise: create Job, register, start a daemon thread, return it

subscribe(job) -> Iterator[tuple[str, dict]]

    i = 0
    loop:
        under job._cond: wait until len(events) > i or job.done
        yield events[i:]  (outside the lock)
        stop when job.done and i == len(events)

shutdown(timeout: float = 5.0) -> None
    set stop_requested on every running job, then join the threads briefly.
    Each worker closes its own generator -- see "Shutdown" below.
```

The thread body drains the runner into the buffer:

```python
def _run(job, **run_kwargs):
    job.generator = run_analysis_stream(job.symbol, job.trade_date, **run_kwargs)
    try:
        for event in job.generator:
            job.append(event)
            if job.stop_requested:      # cooperative shutdown
                break
    except BaseException as exc:
        job.append(("error", {"error": str(exc), "node": None, "step": None}))
    finally:
        job.generator.close()   # this thread owns it; fires the cancellation path
        job.finish()            # done = True, notify_all, drop from _running
```

`append` and `finish` both take `_cond` and `notify_all`.

**No retention window and no reaper.** A job leaves `_running` the moment it
finishes; a subscriber still reading holds a reference to the `Job` object, so it
drains the tail from the buffer and exits on its own. Dedupe therefore means
"in flight right now" — asking for the same ticker again after one completes
starts a fresh run, which is the desired behaviour.

**Why a thread per job rather than a pool.** The cap is enforced at admission. A
pool would silently queue the fourth request instead of rejecting it, which is
the opposite of a 429.

`MAX_CONCURRENT_RUNS` is a module constant read from
`TRADINGAGENTS_MAX_CONCURRENT_RUNS`, default 3.

### `app/api/v1/routes/trading_agents.py`

`analyze_stream` becomes:

1. resolve `symbol`, `trade_date`, `analysts`, `_validated_analyst_models` (as today)
2. `check_backend()` — **moved out of the generator**, so an unreachable LLM
   backend is a `503` rather than an SSE `error` event, and no job is created for
   a run that cannot start
3. `jobs.start(...)`, catching `TooManyRuns` -> `HTTPException(429)`
4. return a `StreamingResponse` whose generator is
   `for event_type, data in jobs.subscribe(job): yield _sse(event_type, data)`

**The line that has to go** is `finally: events.close()`. That is what currently
kills the run on disconnect. Nothing replaces it: closing the SSE generator now
ends the subscription and nothing else.

The event vocabulary — `started`, `node`, `report`, `decision`, `saved`,
`error`, `done` — is unchanged, so the frontend needs no edit. A duplicate
request replays the in-flight run from its first event, so a second tab shows
the reports already produced.

### Cancellation

Stop detaches. The runner's `GeneratorExit` path — `cancelled_after`, the
deliberately preserved checkpoint rows, and `_mark_langsmith_cancelled` — is
kept as-is but narrows to a single trigger: application shutdown.

### Shutdown

`@app.on_event("shutdown")` in `main.py` calls `jobs.shutdown()`.

**Shutdown cannot close the generators itself.** A generator may only be closed
from the thread iterating it: calling `close()` on one that is mid-`next()`
raises `ValueError: generator already executing`. So shutdown is cooperative:

```
shutdown(timeout: float = 5.0):
    set job.stop_requested on every running job
    join each thread with the remaining timeout
```

and each worker checks the flag between events:

```python
for event in job.generator:
    job.append(event)
    if job.stop_requested:
        break
finally:
    job.generator.close()      # same thread, so GeneratorExit lands correctly
```

`close()` in the worker's own `finally` is what fires the cancellation path — so
checkpoints survive and the LangSmith run is marked cancelled. When the loop
ended normally the generator is already exhausted and `close()` is a no-op.

The gap between events is a whole graph node, so a job may not notice the flag
for a while; `join` gives up after the timeout and the threads are daemons, so
the process still exits. A run killed that way is resumable from its checkpoint,
which is the same guarantee as any other abrupt stop.

In dev this fires on every `--reload`, i.e. every file save kills in-flight runs.
Recoverable via the checkpointer, but it is the sharpest edge of this design and
will be felt while working on this code.

### Errors

`run_analysis_stream` already yields `("error", …)` for in-run failures, carrying
the failing node. The job thread wraps the whole drain so that anything escaping
the runner still becomes a terminal error event rather than a silently dead
thread that leaves subscribers waiting forever.

### Cost

A run whose viewer never returns still costs a full multi-minute LLM run. With
no cancel, the concurrency cap is the only thing bounding the damage from a
mistyped ticker. This is accepted.

## Testing

Driven by the existing fake-graph harness (`tests/tradingagents_harness.py`),
which already scripts a run node by node.

**jobs module**
- two `start` calls for one ticker+date return the same job (dedupe)
- a new key at the cap raises `TooManyRuns`; an existing key at the cap does not
- a subscriber attaching after several events replays from event zero
- **closing a subscriber leaves the job running; it still reaches `done` and
  still saves** — the regression this whole change exists to prevent
- a job with no subscriber at all still runs to completion
- a runner that raises produces a terminal `error` event and marks the job done
- a finished job is no longer in `_running`
- `shutdown()` signals a running job, which closes its own generator and stops;
  a job that never notices the flag does not block shutdown past the timeout

**route**
- over the cap -> 429
- `check_backend()` failure -> 503, and no job created
- the happy path streams the same event sequence as today

## Risks

| Risk | Mitigation |
|---|---|
| Jobs die on restart / reload | Checkpointer resumes from the last completed node |
| A blocked subscriber holds a Starlette threadpool thread | Condition waits use a timeout; cap of 3 keeps it far from the 40-thread default |
| Event buffer growth (reports carry full markdown) | ~100 KB per job, capped at 3 concurrent |
| A wedged run blocks shutdown | `join` with a timeout; threads are daemons |
| Closing a generator cross-thread raises `ValueError` | Shutdown is cooperative; the worker closes its own generator |
