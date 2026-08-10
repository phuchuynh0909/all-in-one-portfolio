"""Large-order ("Layer 3") block contract.

Builds on the canonical tick from `tick_contract.normalize_tick`. Trades are
merged into fixed-second blocks per (symbol, side, time-bucket): a single
institutional order usually arrives as many sub-second fills, so a block
captures the true size that per-tick filtering would miss.

A block keeps:
  sending_time  = bucket start (floored to the window, UTC)
  vwap          = volume-weighted average price (notional / qty)
  total_qty     = summed quantity
  dollar_value  = summed notional (price * qty)
  num_trades    = fills merged

Only blocks whose ``dollar_value`` clears the threshold are stored.

The accumulator helpers (`new_block_acc`, `fold_tick`, `merge_acc`,
`finalize_block`) are shaped to plug straight into Bytewax `fold_window`
(builder / folder / merger) and are reused by the reconciler's batch group-by,
so the live and historical paths produce identical blocks.
"""

from __future__ import annotations

import math
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

# Bucket boundaries are aligned to this epoch so the streaming windower and the
# batch reconciler floor timestamps to exactly the same instants.
BLOCK_ALIGN_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


def dollar_value(tick: dict) -> float:
    """Notional value of a normalized tick: price * quantity.

    Despite the name (kept for parlance), the units follow the feed — for the
    Vietnamese market this is the VND notional of the trade.
    """
    try:
        return float(tick["match_price"]) * float(tick["match_qty"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def is_large_order(tick: dict, min_value: float) -> bool:
    """True when a single tick's notional value clears ``min_value``.

    Retained for ad-hoc/per-tick use; the block pipeline filters on
    `is_large_block` instead.
    """
    if(float(tick["match_qty"]) > 1000):
        print(f"symbol: {tick['symbol']}, match_price: {tick['match_price']}, match_qty: {tick['match_qty']}, dollar_value: {dollar_value(tick)}, min_value: {min_value}")
    return dollar_value(tick) >= min_value


# ---------------------------------------------------------------------------
# Auction filtering — ATO/ATC trades clear at a single auction price and would
# form one giant artificial block, so they are dropped before aggregation.
# Empirically (HOSE/KRX): the ATO match is stamped at 09:15:00 and the ATC
# match around 14:45:0x; continuous trading sits between. Windows are matched
# on exchange-local time truncated to the whole second (the auction match's
# many sub-second fills all share one second).
# ---------------------------------------------------------------------------
def is_auction_time(
    sending_time: datetime,
    tz_name: str,
    windows: list[tuple[dtime, dtime]],
) -> bool:
    """True when *sending_time* falls in any [start, end] auction window."""
    if not windows:
        return False
    if sending_time.tzinfo is None:
        sending_time = sending_time.replace(tzinfo=timezone.utc)
    local = sending_time.astimezone(ZoneInfo(tz_name))
    t = local.replace(microsecond=0).time()
    return any(start <= t <= end for start, end in windows)


# ---------------------------------------------------------------------------
# Time bucketing
# ---------------------------------------------------------------------------
def bucket_start(
    dt: datetime, window_seconds: int, align: datetime = BLOCK_ALIGN_EPOCH
) -> datetime:
    """Floor *dt* to the start of its ``window_seconds`` bucket (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    elapsed = (dt - align).total_seconds()
    floored = math.floor(elapsed / window_seconds) * window_seconds
    return align + timedelta(seconds=floored)


# ---------------------------------------------------------------------------
# Block accumulator (Bytewax fold_window builder / folder / merger)
# ---------------------------------------------------------------------------
def new_block_acc() -> dict:
    """Empty accumulator — `fold_window` builder."""
    return {
        "symbol": None,
        "side": None,
        "bucket_time": None,
        "sum_qty": 0,
        "sum_notional": 0.0,
        "num_trades": 0,
    }


def fold_tick(acc: dict, tick: dict, window_seconds: int) -> dict:
    """Fold one canonical tick into a block accumulator — `fold_window` folder."""
    acc["symbol"] = tick["symbol"]
    acc["side"] = int(tick["side"])
    bt = bucket_start(tick["sending_time"], window_seconds)
    if acc["bucket_time"] is None or bt < acc["bucket_time"]:
        acc["bucket_time"] = bt
    acc["sum_qty"] += int(tick["match_qty"])
    acc["sum_notional"] += dollar_value(tick)
    acc["num_trades"] += 1
    return acc


def merge_acc(a: dict, b: dict) -> dict:
    """Combine two partial accumulators for one window — `fold_window` merger."""
    buckets = [t for t in (a["bucket_time"], b["bucket_time"]) if t is not None]
    return {
        "symbol": a["symbol"] or b["symbol"],
        "side": a["side"] if a["side"] is not None else b["side"],
        "bucket_time": min(buckets) if buckets else None,
        "sum_qty": a["sum_qty"] + b["sum_qty"],
        "sum_notional": a["sum_notional"] + b["sum_notional"],
        "num_trades": a["num_trades"] + b["num_trades"],
    }


def finalize_block(acc: dict, received_at: datetime | None) -> dict:
    """Turn a finished accumulator into a block dict (vwap computed)."""
    qty = acc["sum_qty"]
    vwap = acc["sum_notional"] / qty if qty else 0.0
    return {
        "symbol": acc["symbol"],
        "sending_time": acc["bucket_time"],
        "side": acc["side"] if acc["side"] is not None else 0,
        "vwap": vwap,
        "total_qty": qty,
        "dollar_value": acc["sum_notional"],
        "num_trades": acc["num_trades"],
        "received_at": received_at,
    }


def merge_ticks_into_blocks(ticks: list[dict], window_seconds: int) -> list[dict]:
    """Batch-aggregate canonical ticks into blocks (reconciler path).

    Groups by (symbol, side, bucket) and finalises each. ``received_at`` is
    left as None for the caller to stamp at insert time.
    """
    acc_by_key: dict[tuple, dict] = {}
    for tick in ticks:
        bt = bucket_start(tick["sending_time"], window_seconds)
        key = (tick["symbol"], int(tick["side"]), bt)
        acc = acc_by_key.get(key)
        if acc is None:
            acc = new_block_acc()
            acc_by_key[key] = acc
        fold_tick(acc, tick, window_seconds)
    return [finalize_block(acc, None) for acc in acc_by_key.values()]


def is_large_block(block: dict, min_value: float) -> bool:
    """True when a block's total notional value clears ``min_value`` (inclusive)."""
    return block["dollar_value"] >= min_value


def to_block_tuple(block: dict) -> tuple:
    """Convert a block to a `large_orders` insertion tuple.

    Column order matches LARGE_ORDERS_ARROW_SCHEMA:
    (symbol, sending_time, side, vwap, total_qty, dollar_value, num_trades, received_at)
    """
    return (
        block["symbol"],
        block["sending_time"],
        block["side"],
        block["vwap"],
        block["total_qty"],
        block["dollar_value"],
        block["num_trades"],
        block["received_at"],
    )
