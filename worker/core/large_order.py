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


# ---------------------------------------------------------------------------
# SQL mirrors of the block contract
#
# The live path is a ClickHouse materialized view over `ticks`, so the same
# bucketing / auction rules exist twice: once in Python above (used by the
# reconciler) and once as SQL below. They are kept adjacent deliberately —
# `tests/test_large_order_mv.py` asserts the two agree on real ticks, so a
# change to one without the other fails the suite.
# ---------------------------------------------------------------------------
def seconds_of_day(t: dtime) -> int:
    """Whole seconds since local midnight — the unit the SQL predicate compares."""
    return t.hour * 3600 + t.minute * 60 + t.second


def auction_predicate_sql(
    windows: list[tuple[dtime, dtime]],
    tz_name: str,
    column: str = "sending_time",
) -> str:
    """SQL that is true inside any auction window — the mirror of `is_auction_time`.

    Truncates to whole seconds in exchange-local time (``toSecond`` floors,
    matching ``local.replace(microsecond=0)``) and compares inclusively.
    Returns ``"0"`` when auction filtering is disabled, so callers can always
    embed ``NOT (<predicate>)``.
    """
    if not windows:
        return "0"
    local = f"toTimeZone({column}, '{tz_name}')"
    sod = f"(toHour({local}) * 3600 + toMinute({local}) * 60 + toSecond({local}))"
    return " OR ".join(
        f"({sod} BETWEEN {seconds_of_day(start)} AND {seconds_of_day(end)})"
        for start, end in windows
    )


def block_aggregation_sql(
    source: str,
    window_seconds: int,
    tz_name: str,
    auction_windows: list[tuple[dtime, dtime]],
    extra_where: str = "",
) -> str:
    """SELECT that folds ticks in *source* into blocks — mirror of
    `merge_ticks_into_blocks`.

    Emits the ``large_order_blocks`` column shape. ``vwap`` is deliberately
    absent: a ratio is not summable, so it cannot be stored as a partial
    aggregate and is derived at read time instead.

    Note the bucket is floored with ``toStartOfInterval``, which aligns to the
    unix epoch rather than ``BLOCK_ALIGN_EPOCH``. Those agree only when
    ``window_seconds`` divides the epoch offset — see
    `verify_bucket_alignment`.
    """
    where = f"NOT ({auction_predicate_sql(auction_windows, tz_name)})"
    if extra_where:
        where = f"({where}) AND ({extra_where})"
    return f"""
SELECT
    symbol,
    bucket AS sending_time,
    side,
    total_qty,
    dollar_value,
    num_trades
FROM (
    SELECT
        symbol,
        toDateTime64(
            toStartOfInterval(sending_time, INTERVAL {window_seconds} SECOND), 6, 'UTC'
        ) AS bucket,
        toInt32(side) AS side,
        sum(toInt64(match_qty)) AS total_qty,
        sum(toFloat64(match_price) * toFloat64(match_qty)) AS dollar_value,
        toUInt64(count()) AS num_trades
    FROM {source}
    WHERE {where}
    GROUP BY symbol, bucket, side
)""".strip()


def verify_bucket_alignment(window_seconds: int) -> None:
    """Raise when SQL bucketing would disagree with `bucket_start`.

    `bucket_start` floors relative to ``BLOCK_ALIGN_EPOCH``; ClickHouse's
    ``toStartOfInterval`` floors relative to the unix epoch. The two land on the
    same instants only if the window divides the offset between them, which
    holds for every sane value (1, 2, 5, 10, 15, 30, 60, …) but not, say, 7.
    """
    offset = int(BLOCK_ALIGN_EPOCH.timestamp())
    if offset % window_seconds != 0:
        raise ValueError(
            f"window_seconds={window_seconds} does not divide the "
            f"BLOCK_ALIGN_EPOCH offset ({offset}); SQL buckets would not match "
            "bucket_start(). Pick a window that divides it (1, 2, 5, 10, 15, 30, 60)."
        )


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
