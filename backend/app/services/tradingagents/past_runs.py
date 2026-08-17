"""Prior analyses of a ticker, scored by what actually happened, as run context.

The framework already has a slot for this: ``propagator.create_initial_state``
takes a ``past_context`` string, and the Portfolio Manager — the node that makes
the final call — renders it under "Lessons from prior decisions and outcomes".
Upstream fills that slot from ``TradingMemoryLog``, a markdown file written
inside ``TradingAgentsGraph.propagate()``; our streaming runner bypasses
``propagate()``, and upstream's outcome resolver prices positions through
yfinance against SPY, which cannot value a Vietnamese ticker at all. So the log
stays empty and the slot stays unused.

This module fills it from the two sources we actually own:

  * **What we said** — the ``trading_agent_analyses`` ClickHouse table, which
    ``runner`` already writes on every completed run (see ``store``).
  * **What happened** — our own ``ohlc_eod`` closes, with VNINDEX as the alpha
    benchmark.

Feeding the *outcome* and not just the prior conclusion is the point. A bare
"last time you rated this Buy" is an anchor that invites the next run to echo
it; "last time you rated this Buy and it lost 3% to the index over 12 sessions"
is evidence the model can revise against.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

# How many prior sessions' decisions to carry. Small on purpose: this text lands
# in the Portfolio Manager's prompt next to the full risk debate.
_MAX_RUNS = int(os.getenv("TRADINGAGENTS_PAST_RUNS_MAX", "5"))
# Per-decision prose budget. The rating and the realized numbers carry the
# signal; the prose is there for the reasoning, not the full report.
_DECISION_CHARS = int(os.getenv("TRADINGAGENTS_PAST_RUNS_DECISION_CHARS", "700"))
_BENCHMARK = os.getenv("TRADINGAGENTS_PAST_RUNS_BENCHMARK", "VNINDEX")


def enabled() -> bool:
    """Whether prior runs are fed back in (default yes)."""
    return os.getenv("TRADINGAGENTS_PAST_RUNS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _closes(symbol: str, start: datetime, end: datetime) -> pd.Series | None:
    """Daily closes indexed by date, ascending — or None when unavailable."""
    from .vn_data import _load_ohlcv_frame

    try:
        df = _load_ohlcv_frame(symbol, start=start, end=end)
    except Exception as exc:  # noqa: BLE001 — scoring is best-effort
        logger.warning("Could not load closes for %s: %s", symbol, exc)
        return None
    if df is None or df.empty:
        return None
    series = pd.Series(
        df["Close"].to_numpy(dtype=float), index=pd.to_datetime(df["Date"])
    )
    return series.sort_index()


def _return_since(closes: pd.Series | None, day: pd.Timestamp) -> tuple[float, int] | None:
    """Return and session count from ``day``'s close to the frame's last close.

    Entry is the close of the session the decision was made on — the first bar a
    reader of that report could actually have traded — so a decision published
    on a holiday scores from the next session, not from a stale price.
    """
    if closes is None:
        return None
    after = closes.loc[closes.index >= day]
    if len(after) < 2:
        return None  # too recent to have an outcome yet
    return float(after.iloc[-1] / after.iloc[0] - 1.0), len(after) - 1


def _fmt_pct(value: float) -> str:
    return f"{value * 100:+.1f}%"


def _outcome_tag(
    ticker_closes: pd.Series | None,
    bench_closes: pd.Series | None,
    day: pd.Timestamp,
) -> str:
    """The realized half of an entry's tag: raw return, alpha, holding length."""
    scored = _return_since(ticker_closes, day)
    if scored is None:
        return "outcome pending (too recent to score)"

    raw, sessions = scored
    parts = [f"{_fmt_pct(raw)} raw"]
    bench = _return_since(bench_closes, day)
    if bench is not None:
        parts.append(f"{_fmt_pct(raw - bench[0])} vs {_BENCHMARK}")
    parts.append(f"{sessions} session{'s' if sessions != 1 else ''} later")
    return " | ".join(parts)


def _trim(text: str) -> str:
    body = " ".join(str(text or "").split())
    if len(body) <= _DECISION_CHARS:
        return body
    return body[:_DECISION_CHARS].rstrip() + " …"


def build_past_context(symbol: str, trade_date: str) -> str:
    """Prior decisions for ``symbol`` before ``trade_date``, scored by outcome.

    Returns "" when there is no usable history, when the feature is switched
    off, or on any failure — the caller treats this as optional enrichment.
    """
    if not enabled():
        return ""

    sym = str(symbol).upper()
    try:
        from . import store

        rows = store.list_prior_decisions(sym, trade_date, limit=_MAX_RUNS)
    except Exception as exc:  # noqa: BLE001 — never fail a run over its own history
        logger.warning("Prior decisions unavailable for %s: %s", sym, exc)
        return ""

    rows = [r for r in rows if str(r.get("trade_date") or "").strip()]
    if not rows:
        return ""

    try:
        curr = pd.to_datetime(trade_date)
        earliest = min(pd.to_datetime(r["trade_date"]) for r in rows)
        ticker_closes = _closes(sym, earliest.to_pydatetime(), curr.to_pydatetime())
        bench_closes = _closes(
            _BENCHMARK, earliest.to_pydatetime(), curr.to_pydatetime()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not score prior decisions for %s: %s", sym, exc)
        ticker_closes = bench_closes = None

    blocks: list[str] = []
    for row in rows:
        day = pd.to_datetime(row["trade_date"])
        rating = str(row.get("signal") or "n/a").strip() or "n/a"
        outcome = _outcome_tag(ticker_closes, bench_closes, day)
        blocks.append(
            f"[{row['trade_date']} | {sym} | {rating} | {outcome}]\n"
            f"DECISION: {_trim(row.get('final_decision'))}"
        )

    header = (
        f"Your own prior analyses of {sym}, newest first, each tagged with what "
        f"the price actually did afterwards (raw return from that session's "
        f"close, and the same figure net of {_BENCHMARK}). These are your track "
        f"record on this ticker, not a recommendation: where a past call was "
        f"wrong, say what the outcome changes about today's view rather than "
        f"repeating the earlier rating; where it was right, do not treat that as "
        f"proof the setup still holds."
    )
    return "\n\n".join([header, *blocks])
