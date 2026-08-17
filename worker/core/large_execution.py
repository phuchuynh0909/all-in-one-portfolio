"""Large-execution ("block episode") detection core — pure functions.

Ports the standalone MVP ``detect_large_trade_programs.py`` into this codebase
as small, offline-testable functions that operate on canonical ticks (the
output of :func:`core.tick_contract.normalize_tick`, i.e. the rows of the
ClickHouse ``ticks`` table). No I/O, no ClickHouse, no MQTT — feed it a list of
tick dicts and get back three tiers of features:

  1. event features   — one row per trade (rolling 30s signed flow + large print)
  2. bin features     — one row per (symbol, active 1s bin) with a prior-only
                        signed-notional z-score and a flow-cluster flag
  3. block episodes   — nearby same-direction candidate bins stitched together

Side convention
---------------
``side`` is the **aggressor/taker** side, matching the rest of this repo:
``SIDE_BUY = 1`` (buyer-initiated, +) and ``SIDE_SELL = 2`` (seller-initiated,
-). ``SIDE_UNKNOWN = 0`` contributes to notional magnitude but carries no sign.
This is the MVP's default orientation (no ``--side-is-resting`` flip).

A detected episode is a **block-like execution footprint**, not proof of an
institution or a parent order. This layer does not reconstruct L2 depth,
observe cancels, or infer iceberg replenishment.

The parameter names/defaults mirror the MVP CLI so results stay comparable; see
:class:`DetectionParams`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import numpy as np

from core.large_order import bucket_start
from core.tick_contract import SIDE_BUY, SIDE_SELL, SIDE_UNKNOWN

# Episode candidate classifications (mirror the MVP's candidate_type).
FLOW_CLUSTER = "FLOW_CLUSTER"
LARGE_PRINT = "LARGE_PRINT"
FLOW_CLUSTER_AND_LARGE_PRINT = "FLOW_CLUSTER_AND_LARGE_PRINT"
NONE_TYPE = ""


@dataclass(frozen=True)
class DetectionParams:
    """Detection knobs. Defaults mirror ``detect_large_trade_programs.py``.

    Attributes:
        rolling_seconds: Window (s) for row-level rolling signed volume/notional.
        bin_seconds: Bin width (s) for the 1-second aggregation tier.
        baseline_bins: Max prior *active* bins used for the signed-notional
            baseline (MVP default 1,800 ≈ a 30-minute prior-only window).
        min_baseline_bins: Minimum prior active bins before a z-score is valid.
        z_threshold: |signed-notional z| a bin needs to meet the flow gate.
        imbalance_threshold: One-sidedness |imbalance| a bin needs (0..1).
        min_trades_per_bin: Minimum trades in a bin to meet the flow gate.
        run_length: Consecutive same-direction gate-passing bins required for a
            flow-cluster candidate (MVP "two-bin same-direction run" == 2).
        large_print_quantile: Prior-trade notional percentile for a large print.
        large_print_window: Number of preceding trades used for that percentile.
        large_print_min_prior: Minimum preceding trades before large-print
            detection is allowed (avoids nonsense percentiles early in the day).
        episode_gap_bins: Max gap (in bins) between same-direction candidate
            bins that still belong to one episode.
    """

    rolling_seconds: int = 30
    bin_seconds: int = 1
    baseline_bins: int = 1800
    min_baseline_bins: int = 300
    z_threshold: float = 2.5
    imbalance_threshold: float = 0.70
    min_trades_per_bin: int = 3
    run_length: int = 2
    large_print_quantile: float = 0.99
    large_print_window: int = 500
    large_print_min_prior: int = 30
    episode_gap_bins: int = 5


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def signed_sign(side: int) -> int:
    """Aggressor sign: +1 for BUY, -1 for SELL, 0 for unknown."""
    if side == SIDE_BUY:
        return 1
    if side == SIDE_SELL:
        return -1
    return 0


def sign_to_side(sign: int) -> int:
    """Map a direction sign back to the repo's side convention (1/2/0)."""
    if sign > 0:
        return SIDE_BUY
    if sign < 0:
        return SIDE_SELL
    return SIDE_UNKNOWN


def _notional(tick: dict) -> float:
    """price * qty of a canonical tick, robust to bad values (0.0 on error)."""
    try:
        return float(tick["match_price"]) * float(tick["match_qty"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _by_symbol(ticks: Iterable[dict]) -> dict[str, list[dict]]:
    """Group ticks by symbol, each group sorted ascending by sending_time."""
    groups: dict[str, list[dict]] = {}
    for t in ticks:
        groups.setdefault(t["symbol"], []).append(t)
    for sym in groups:
        groups[sym].sort(key=lambda t: _as_utc(t["sending_time"]))
    return groups


# ---------------------------------------------------------------------------
# Tier 1 — event (per-trade) features
# ---------------------------------------------------------------------------
def compute_event_features(
    ticks: list[dict], params: DetectionParams = DetectionParams()
) -> list[dict]:
    """One feature row per trade for a *single symbol*, in time order.

    Adds a rolling ``rolling_seconds`` signed volume/notional/imbalance (window
    includes the current trade) and an ``is_large_print`` flag: True when the
    trade's notional is at or above the ``large_print_quantile`` percentile of
    the preceding ``large_print_window`` trades (the current print is excluded,
    so it cannot qualify itself).

    Input ``ticks`` must already be one symbol, ascending by ``sending_time``.
    """
    out: list[dict] = []
    # Rolling time window of recent trades: (t, signed_qty, signed_notional, abs_notional)
    window: deque[tuple[datetime, float, float, float]] = deque()
    prior_notional: deque[float] = deque(maxlen=params.large_print_window)

    for t in ticks:
        ts = _as_utc(t["sending_time"])
        qty = float(t.get("match_qty") or 0)
        notional = _notional(t)
        sign = signed_sign(int(t.get("side", SIDE_UNKNOWN)))

        # Large print vs strictly-prior trades (current excluded).
        if len(prior_notional) >= params.large_print_min_prior:
            threshold = float(
                np.quantile(np.asarray(prior_notional), params.large_print_quantile)
            )
            is_large_print = notional >= threshold
        else:
            threshold = float("nan")
            is_large_print = False

        # Evict trades older than the rolling window, then add the current one.
        cutoff = ts.timestamp() - params.rolling_seconds
        while window and window[0][0].timestamp() < cutoff:
            window.popleft()
        window.append((ts, sign * qty, sign * notional, abs(notional)))

        signed_volume = sum(w[1] for w in window)
        signed_notional = sum(w[2] for w in window)
        abs_notional = sum(w[3] for w in window)
        imbalance = signed_notional / abs_notional if abs_notional > 0 else 0.0

        out.append(
            {
                "symbol": t["symbol"],
                "sending_time": ts,
                "side": int(t.get("side", SIDE_UNKNOWN)),
                "match_price": float(t.get("match_price", 0.0)),
                "match_qty": qty,
                "notional": notional,
                "rolling_signed_volume": signed_volume,
                "rolling_signed_notional": signed_notional,
                "rolling_flow_imbalance": imbalance,
                "large_print_threshold": threshold,
                "is_large_print": is_large_print,
            }
        )

        prior_notional.append(notional)

    return out


# ---------------------------------------------------------------------------
# Tier 2 — bin (per active second) features
# ---------------------------------------------------------------------------
def aggregate_bins(
    event_features: list[dict], params: DetectionParams = DetectionParams()
) -> list[dict]:
    """Collapse per-trade event features into active time bins (one symbol).

    Returns bins ordered by time; only bins with at least one trade are emitted
    (matching the MVP's "active time bin" output). Each bin carries its signed
    notional, absolute notional, trade-flow imbalance, direction and large-print
    count. The prior-only z-score and cluster flag are added by
    :func:`compute_bin_features`.
    """
    acc: dict[datetime, dict] = {}
    for ev in event_features:
        bt = bucket_start(ev["sending_time"], params.bin_seconds)
        b = acc.get(bt)
        if b is None:
            b = {
                "symbol": ev["symbol"],
                "bin_time": bt,
                "signed_notional": 0.0,
                "abs_notional": 0.0,
                "num_trades": 0,
                "large_print_count": 0,
            }
            acc[bt] = b
        sign = signed_sign(ev["side"])
        b["signed_notional"] += sign * ev["notional"]
        b["abs_notional"] += abs(ev["notional"])
        b["num_trades"] += 1
        if ev["is_large_print"]:
            b["large_print_count"] += 1

    bins = [acc[k] for k in sorted(acc.keys())]
    for b in bins:
        b["trade_flow_imbalance"] = (
            b["signed_notional"] / b["abs_notional"] if b["abs_notional"] > 0 else 0.0
        )
        b["direction"] = int(np.sign(b["signed_notional"]))
    return bins


def compute_bin_features(
    bins: list[dict], params: DetectionParams = DetectionParams()
) -> list[dict]:
    """Add prior-only ``signed_notional_z``, the flow-cluster flag and the
    ``candidate_type`` to time-ordered active bins (one symbol).

    Baseline: the trailing up-to ``baseline_bins`` *prior* active bins (current
    excluded). A z-score is only produced once at least ``min_baseline_bins``
    prior bins exist; otherwise it is ``None``.

    A bin is a ``flow_cluster_candidate`` when it (and ``run_length`` - 1
    consecutive same-direction predecessors) all pass the flow gate: a valid
    ``|z| >= z_threshold``, ``|imbalance| >= imbalance_threshold`` and
    ``num_trades >= min_trades_per_bin``.
    """
    baseline: deque[float] = deque(maxlen=params.baseline_bins)
    run = 0
    prev_direction = 0

    for b in bins:
        if len(baseline) >= params.min_baseline_bins:
            arr = np.asarray(baseline)
            mean = float(arr.mean())
            std = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
            z = (b["signed_notional"] - mean) / std if std > 0 else 0.0
        else:
            z = None
        b["signed_notional_z"] = z

        meets_gate = (
            z is not None
            and abs(z) >= params.z_threshold
            and abs(b["trade_flow_imbalance"]) >= params.imbalance_threshold
            and b["num_trades"] >= params.min_trades_per_bin
        )
        direction = b["direction"]
        if meets_gate and direction != 0 and direction == prev_direction:
            run += 1
        elif meets_gate and direction != 0:
            run = 1
        else:
            run = 0
        prev_direction = direction if meets_gate else 0

        flow_cluster = meets_gate and run >= params.run_length
        b["meets_flow_gate"] = meets_gate
        b["flow_cluster_candidate"] = flow_cluster

        is_lp = b["large_print_count"] > 0
        if flow_cluster and is_lp:
            b["candidate_type"] = FLOW_CLUSTER_AND_LARGE_PRINT
        elif flow_cluster:
            b["candidate_type"] = FLOW_CLUSTER
        elif is_lp:
            b["candidate_type"] = LARGE_PRINT
        else:
            b["candidate_type"] = NONE_TYPE

        # Append the current bin to the prior-only baseline *after* scoring it.
        baseline.append(b["signed_notional"])

    return bins


# ---------------------------------------------------------------------------
# Tier 3 — episode stitching
# ---------------------------------------------------------------------------
def stitch_episodes(
    bin_features: list[dict], params: DetectionParams = DetectionParams()
) -> list[dict]:
    """Stitch nearby same-direction candidate bins into block episodes.

    A candidate bin has ``candidate_type != ""``. Two candidate bins join the
    same episode when they share a direction and their bin-time gap is at most
    ``episode_gap_bins`` bins. Bins with an ambiguous (zero) direction are
    skipped. Returns one dict per episode in time order.
    """
    gap_seconds = params.episode_gap_bins * params.bin_seconds
    episodes: list[dict] = []
    current: dict | None = None

    def _finalize(ep: dict) -> dict:
        has_fc = ep["_fc"] > 0
        has_lp = ep["large_print_count"] > 0
        if has_fc and has_lp:
            ctype = FLOW_CLUSTER_AND_LARGE_PRINT
        elif has_fc:
            ctype = FLOW_CLUSTER
        else:
            ctype = LARGE_PRINT
        ep["candidate_type"] = ctype
        ep["side"] = sign_to_side(ep["direction"])
        ep.pop("_fc", None)
        return ep

    for b in bin_features:
        if b["candidate_type"] == NONE_TYPE or b["direction"] == 0:
            continue
        direction = b["direction"]

        if (
            current is not None
            and current["direction"] == direction
            and (b["bin_time"].timestamp() - current["end_time"].timestamp())
            <= gap_seconds
        ):
            # Extend the open episode.
            current["end_time"] = b["bin_time"]
            current["signed_notional"] += b["signed_notional"]
            current["abs_notional"] += b["abs_notional"]
            current["num_trades"] += b["num_trades"]
            current["num_bins"] += 1
            current["large_print_count"] += b["large_print_count"]
            current["max_abs_z"] = max(
                current["max_abs_z"], abs(b["signed_notional_z"] or 0.0)
            )
            current["max_abs_imbalance"] = max(
                current["max_abs_imbalance"], abs(b["trade_flow_imbalance"])
            )
            current["_fc"] += 1 if b["flow_cluster_candidate"] else 0
        else:
            if current is not None:
                episodes.append(_finalize(current))
            current = {
                "symbol": b["symbol"],
                "direction": direction,
                "start_time": b["bin_time"],
                "end_time": b["bin_time"],
                "signed_notional": b["signed_notional"],
                "abs_notional": b["abs_notional"],
                "num_trades": b["num_trades"],
                "num_bins": 1,
                "large_print_count": b["large_print_count"],
                "max_abs_z": abs(b["signed_notional_z"] or 0.0),
                "max_abs_imbalance": abs(b["trade_flow_imbalance"]),
                "_fc": 1 if b["flow_cluster_candidate"] else 0,
            }

    if current is not None:
        episodes.append(_finalize(current))

    return episodes


# ---------------------------------------------------------------------------
# Orchestration — full multi-symbol run
# ---------------------------------------------------------------------------
def detect(
    ticks: Iterable[dict], params: DetectionParams = DetectionParams()
) -> dict[str, list[dict]]:
    """Run all three tiers over a (possibly multi-symbol) tick tape.

    Returns ``{"event_features": [...], "bin_features": [...],
    "episodes": [...]}`` with rows for every symbol combined (per-symbol state
    never leaks across symbols).
    """
    all_events: list[dict] = []
    all_bins: list[dict] = []
    all_episodes: list[dict] = []

    for _symbol, group in _by_symbol(ticks).items():
        events = compute_event_features(group, params)
        bins = compute_bin_features(aggregate_bins(events, params), params)
        episodes = stitch_episodes(bins, params)
        all_events.extend(events)
        all_bins.extend(bins)
        all_episodes.extend(episodes)

    return {
        "event_features": all_events,
        "bin_features": all_bins,
        "episodes": all_episodes,
    }


# ---------------------------------------------------------------------------
# Streaming — incremental per-symbol detector (Bytewax stateful_map)
# ---------------------------------------------------------------------------
class SymbolDetector:
    """Incremental, single-symbol block-episode detector.

    Fed one canonical tick at a time via :meth:`push`, it reproduces exactly the
    bin-level scoring and episode stitching of the batch pipeline
    (:func:`aggregate_bins` -> :func:`compute_bin_features` ->
    :func:`stitch_episodes`). After the whole tape has been pushed and
    :meth:`flush` called, the set of emitted episodes — deduplicated to the last
    snapshot per ``(symbol, start_time, side)`` — equals ``detect(...)``'s
    ``episodes`` for that symbol. This keeps the live and batch paths identical.

    A 1-second bin "closes" when a trade arrives in a later bin (event-time,
    like the large-order ingest). Each time a *candidate* bin closes, the
    growing open episode is re-emitted; the ClickHouse ReplacingMergeTree keeps
    the latest snapshot per key, so partial live episodes converge to the same
    aggregates the reconciler would compute.

    State is plain deques/dicts/ints (picklable) so Bytewax can snapshot it.
    """

    def __init__(self, params: DetectionParams = DetectionParams(), symbol: str | None = None):
        self.params = params
        self.symbol = symbol
        self.prior_notional: deque[float] = deque(maxlen=params.large_print_window)
        self.baseline: deque[float] = deque(maxlen=params.baseline_bins)
        self.run = 0
        self.prev_direction = 0
        self.cur_bin: dict | None = None
        self.open_episode: dict | None = None

    def _is_large_print(self, notional: float) -> bool:
        if len(self.prior_notional) >= self.params.large_print_min_prior:
            thr = float(
                np.quantile(
                    np.asarray(self.prior_notional), self.params.large_print_quantile
                )
            )
            return notional >= thr
        return False

    def _finalize_open_episode(self) -> dict:
        """Snapshot the open episode as a standalone, insertable dict."""
        ep = self.open_episode
        assert ep is not None
        has_fc = ep["_fc"] > 0
        has_lp = ep["large_print_count"] > 0
        if has_fc and has_lp:
            ctype = FLOW_CLUSTER_AND_LARGE_PRINT
        elif has_fc:
            ctype = FLOW_CLUSTER
        else:
            ctype = LARGE_PRINT
        return {
            "symbol": self.symbol,
            "direction": ep["direction"],
            "side": sign_to_side(ep["direction"]),
            "start_time": ep["start_time"],
            "end_time": ep["end_time"],
            "signed_notional": ep["signed_notional"],
            "abs_notional": ep["abs_notional"],
            "num_trades": ep["num_trades"],
            "num_bins": ep["num_bins"],
            "large_print_count": ep["large_print_count"],
            "max_abs_z": ep["max_abs_z"],
            "max_abs_imbalance": ep["max_abs_imbalance"],
            "candidate_type": ctype,
        }

    def _close_current_bin(self) -> dict | None:
        """Score the current bin (z / gate / candidate), fold it into the open
        episode, and return an episode snapshot if the bin was a candidate."""
        b = self.cur_bin
        assert b is not None
        b["trade_flow_imbalance"] = (
            b["signed_notional"] / b["abs_notional"] if b["abs_notional"] > 0 else 0.0
        )
        b["direction"] = int(np.sign(b["signed_notional"]))

        if len(self.baseline) >= self.params.min_baseline_bins:
            arr = np.asarray(self.baseline)
            mean = float(arr.mean())
            std = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
            z = (b["signed_notional"] - mean) / std if std > 0 else 0.0
        else:
            z = None
        b["signed_notional_z"] = z

        meets_gate = (
            z is not None
            and abs(z) >= self.params.z_threshold
            and abs(b["trade_flow_imbalance"]) >= self.params.imbalance_threshold
            and b["num_trades"] >= self.params.min_trades_per_bin
        )
        direction = b["direction"]
        if meets_gate and direction != 0 and direction == self.prev_direction:
            self.run += 1
        elif meets_gate and direction != 0:
            self.run = 1
        else:
            self.run = 0
        self.prev_direction = direction if meets_gate else 0
        flow_cluster = meets_gate and self.run >= self.params.run_length
        b["flow_cluster_candidate"] = flow_cluster

        is_lp = b["large_print_count"] > 0
        candidate = flow_cluster or is_lp

        # Prior-only baseline: append *after* scoring this bin.
        self.baseline.append(b["signed_notional"])

        if not candidate or direction == 0:
            return None
        return self._extend_or_start_episode(b, flow_cluster)

    def _extend_or_start_episode(self, b: dict, flow_cluster: bool) -> dict:
        gap_seconds = self.params.episode_gap_bins * self.params.bin_seconds
        direction = b["direction"]
        oe = self.open_episode
        if (
            oe is not None
            and oe["direction"] == direction
            and (b["bin_time"].timestamp() - oe["end_time"].timestamp()) <= gap_seconds
        ):
            oe["end_time"] = b["bin_time"]
            oe["signed_notional"] += b["signed_notional"]
            oe["abs_notional"] += b["abs_notional"]
            oe["num_trades"] += b["num_trades"]
            oe["num_bins"] += 1
            oe["large_print_count"] += b["large_print_count"]
            oe["max_abs_z"] = max(oe["max_abs_z"], abs(b["signed_notional_z"] or 0.0))
            oe["max_abs_imbalance"] = max(
                oe["max_abs_imbalance"], abs(b["trade_flow_imbalance"])
            )
            oe["_fc"] += 1 if flow_cluster else 0
        else:
            self.open_episode = {
                "direction": direction,
                "start_time": b["bin_time"],
                "end_time": b["bin_time"],
                "signed_notional": b["signed_notional"],
                "abs_notional": b["abs_notional"],
                "num_trades": b["num_trades"],
                "num_bins": 1,
                "large_print_count": b["large_print_count"],
                "max_abs_z": abs(b["signed_notional_z"] or 0.0),
                "max_abs_imbalance": abs(b["trade_flow_imbalance"]),
                "_fc": 1 if flow_cluster else 0,
            }
        return self._finalize_open_episode()

    def push(self, tick: dict) -> list[dict]:
        """Feed one canonical tick; return episode snapshots to upsert (0 or 1)."""
        ts = _as_utc(tick["sending_time"])
        qty = float(tick.get("match_qty") or 0)
        notional = _notional(tick)
        sign = signed_sign(int(tick.get("side", SIDE_UNKNOWN)))
        if self.symbol is None:
            self.symbol = tick["symbol"]

        is_lp = self._is_large_print(notional)
        bin_time = bucket_start(ts, self.params.bin_seconds)

        emitted: list[dict] = []
        # Advance to a new bin only when time moves strictly forward; late/equal
        # ticks fold into the current bin (the reconciler is authoritative).
        if self.cur_bin is not None and bin_time > self.cur_bin["bin_time"]:
            ep = self._close_current_bin()
            if ep is not None:
                emitted.append(ep)
            self.cur_bin = None

        if self.cur_bin is None:
            self.cur_bin = {
                "symbol": self.symbol,
                "bin_time": bin_time,
                "signed_notional": 0.0,
                "abs_notional": 0.0,
                "num_trades": 0,
                "large_print_count": 0,
            }

        self.cur_bin["signed_notional"] += sign * notional
        self.cur_bin["abs_notional"] += abs(notional)
        self.cur_bin["num_trades"] += 1
        if is_lp:
            self.cur_bin["large_print_count"] += 1

        self.prior_notional.append(notional)
        return emitted

    def flush(self) -> list[dict]:
        """Close any open bin at end-of-stream/shutdown; return final snapshots."""
        if self.cur_bin is None:
            return []
        ep = self._close_current_bin()
        self.cur_bin = None
        return [ep] if ep is not None else []


def to_episode_row(episode: dict, received_at: datetime) -> tuple:
    """Convert an episode dict to a `block_episodes` insertion tuple.

    Column order matches ``model.BLOCK_EPISODES_COLUMNS`` /
    ``BLOCK_EPISODES_ARROW_SCHEMA``:
    (symbol, start_time, end_time, side, candidate_type, signed_notional,
     abs_notional, num_trades, num_bins, large_print_count, max_abs_z,
     max_abs_imbalance, received_at).
    """
    return (
        episode["symbol"],
        _as_utc(episode["start_time"]),
        _as_utc(episode["end_time"]),
        int(episode["side"]),
        episode["candidate_type"],
        float(episode["signed_notional"]),
        float(episode["abs_notional"]),
        int(episode["num_trades"]),
        int(episode["num_bins"]),
        int(episode["large_print_count"]),
        float(episode["max_abs_z"]),
        float(episode["max_abs_imbalance"]),
        _as_utc(received_at),
    )
