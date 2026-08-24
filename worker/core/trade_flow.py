"""Trade-flow feature contract — SQL over `ticks`, computed by ClickHouse.

This replaces the removed z-score block-episode detector. The
feed is a trade/ticker tape, not Market-By-Order: there is no resting book, no
order IDs, no quotes, no adds/cancels. So OFI, book imbalance, replenishment
and queue depletion are not computable. What *is* available is trade flow, and
these features exploit it: size concentration, temporal clustering, directional
imbalance and price impact.

Two levels, because a materialized view sees only the rows of one INSERT:

    ticks ──MV──▶ trade_flow_seconds   (1-second bars, all columns mergeable)
                        │
                        └──view──▶ trade_flow_windows   (N-second features)

**Why two levels.** Anything needing tick-to-tick ordering — inter-arrival
deltas, per-tick returns — is *wrong* inside an incremental MV, because
consecutive trades routinely land in different INSERTs (`tick_ingest` flushes
every ~2s). Level 1 therefore stores only order-independent aggregates
(sums, min/max, and `argMin`/`argMax`/`quantiles` **states**, which merge
exactly). Level 2 reads a table rather than an insert block, so it can order
seconds and derive returns, realized volatility and burst intensity safely.

Consequences worth knowing:

* True inter-arrival percentiles (`median_interarrival_ms`, `p10`) are not
  derivable this way. Intensity is measured from per-second trade counts
  instead — `max_trades_per_second`, `burstiness`, `active_ratio` — which is
  exact and captures the same temporal clustering.
* Large-trade features are **threshold-free**: `size_hhi` (Herfindahl on trade
  sizes, exact because Σq² is additive), `top_trade_share` and
  `p95_to_median`. A ratio against a trailing per-symbol threshold would need
  per-tick classification at aggregation time, which the MV cannot know. The
  scorer normalizes per symbol instead, which achieves the same end.
* Direction uses the feed's **real aggressor side** (`side` 1=BUY / 2=SELL),
  populated on ~99.7% of ticks. The tick rule is not needed and not used;
  `side = 0` trades count toward volume but neither direction, so imbalance is
  over classified volume only.
"""

from __future__ import annotations

from datetime import time as dtime

from core.large_order import auction_predicate_sql

# Columns of `trade_flow_seconds`, in DDL order — the MV must produce these names.
SECOND_BAR_COLUMNS = (
    "symbol",
    "sec",
    "trades",
    "volume",
    "notional",
    "qty_sq",
    "buy_volume",
    "sell_volume",
    "buy_trades",
    "sell_trades",
    "max_qty",
    "hi",
    "lo",
    "open_px",
    "close_px",
    "qty_q",
    "ms_offsets",
)

# Trade-size quantiles kept as a mergeable state on the 1-second bars.
QTY_QUANTILES = (0.5, 0.95)


def second_bar_sql(
    source: str,
    tz_name: str,
    auction_windows: list[tuple[dtime, dtime]],
    extra_where: str = "",
) -> str:
    """SELECT folding raw ticks into 1-second bars (the materialized view body).

    Every output column is mergeable: plain sums, min/max, or an aggregate
    *state*. ``argMin``/``argMax`` states give the bar's true first/last traded
    price once merged, which a plain ``min``/``max`` could not.
    """
    where = f"NOT ({auction_predicate_sql(auction_windows, tz_name)})"
    if extra_where:
        where = f"({where}) AND ({extra_where})"
    qs = ", ".join(str(q) for q in QTY_QUANTILES)
    return f"""
SELECT
    symbol,
    toDateTime(toStartOfInterval(sending_time, INTERVAL 1 SECOND), 'UTC') AS sec,
    toUInt64(count())                                        AS trades,
    sum(toInt64(match_qty))                                  AS volume,
    sum(toFloat64(match_price) * toFloat64(match_qty))        AS notional,
    sum(pow(toFloat64(match_qty), 2))                        AS qty_sq,
    sumIf(toInt64(match_qty), side = 1)                      AS buy_volume,
    sumIf(toInt64(match_qty), side = 2)                      AS sell_volume,
    toUInt64(countIf(side = 1))                              AS buy_trades,
    toUInt64(countIf(side = 2))                              AS sell_trades,
    max(toInt64(match_qty))                                  AS max_qty,
    max(toFloat64(match_price))                              AS hi,
    min(toFloat64(match_price))                              AS lo,
    argMinState(toFloat64(match_price), sending_time)         AS open_px,
    argMaxState(toFloat64(match_price), sending_time)         AS close_px,
    quantilesState({qs})(toInt64(match_qty))                  AS qty_q,
    -- Millisecond offset within the second, one per trade. Order-independent
    -- (each value depends only on its own tick), and the array merges by
    -- concatenation — so level 2 can sort the window's offsets back into true
    -- order and difference them for *exact* inter-arrival gaps. This is what
    -- makes timing recoverable despite the MV never seeing tick order.
    groupArray(toUInt16(toUnixTimestamp64Milli(sending_time) % 1000)) AS ms_offsets
FROM {source}
WHERE {where}
GROUP BY symbol, sec
""".strip()


def window_features_sql(
    seconds_table: str,
    window_seconds: int,
    extra_where: str = "",
) -> str:
    """SELECT deriving N-second trade-flow features from the 1-second bars.

    Reads a table, not an insert block, so ordering across seconds is safe —
    that is what makes returns, realized volatility and burst intensity
    computable here but not in the MV.

    Two branches are joined because they aggregate at different levels:
    ``base`` merges states over every partial row in the window (the only
    correct way to get quantiles and first/last price), while ``pace`` first
    collapses each second and then measures the *shape* across seconds.
    """
    where = f"WHERE {extra_where}" if extra_where else ""
    qs = ", ".join(str(q) for q in QTY_QUANTILES)
    w = window_seconds
    return f"""
WITH
base AS (
    SELECT
        symbol,
        toDateTime(toStartOfInterval(sec, INTERVAL {w} SECOND), 'UTC') AS window_start,
        sum(trades)                          AS trade_count,
        sum(volume)                          AS volume,
        sum(notional)                        AS notional,
        sum(qty_sq)                          AS qty_sq,
        sum(buy_volume)                      AS buy_volume,
        sum(sell_volume)                     AS sell_volume,
        sum(buy_trades)                      AS buy_trades,
        sum(sell_trades)                     AS sell_trades,
        max(max_qty)                         AS max_trade_size,
        max(hi)                              AS high,
        min(lo)                              AS low,
        argMinMerge(open_px)                 AS open,
        argMaxMerge(close_px)                AS close,
        quantilesMerge({qs})(qty_q)          AS qty_quantiles
    FROM {seconds_table}
    {where}
    GROUP BY symbol, window_start
),
-- Collapse partial rows per second first, then describe the pace across the
-- window's seconds. `groupArray` order is not guaranteed, so the closes are
-- sorted by their second before differencing.
per_sec AS (
    SELECT symbol, sec, sum(trades) AS trades, argMaxMerge(close_px) AS close_sec,
           groupArrayArray(ms_offsets) AS offs
    FROM {seconds_table}
    {where}
    GROUP BY symbol, sec
),
pace AS (
    SELECT
        symbol,
        toDateTime(toStartOfInterval(sec, INTERVAL {w} SECOND), 'UTC') AS window_start,
        toUInt64(count())    AS active_seconds,
        max(trades)          AS max_trades_per_second,
        sqrt(arraySum(arrayMap(x -> x * x, arrayDifference(
            arrayMap(v -> log(greatest(v, 1e-12)),
                     arrayMap(t -> t.2, arraySort(x -> x.1, groupArray((sec, close_sec)))))
        )))) AS realized_vol,
        -- Rebuild absolute milliseconds for every trade in the window, sort into
        -- true arrival order, and difference. The leading element of
        -- arrayDifference is always 0 (no predecessor), so it is sliced off:
        -- `gaps` holds exactly one inter-arrival per trade after the first.
        arraySlice(arrayDifference(arraySort(arrayFlatten(groupArray(
            arrayMap(o -> toInt64(toUnixTimestamp(sec)) * 1000 + o, offs)
        )))), 2) AS gaps
    FROM per_sec
    GROUP BY symbol, window_start
)
SELECT
    b.symbol                                              AS symbol,
    b.window_start                                        AS window_start,
    -- activity
    b.trade_count                                         AS trade_count,
    b.volume                                              AS volume,
    b.notional                                            AS notional,
    b.trade_count / {w}                                   AS trades_per_second,
    b.volume / {w}                                        AS volume_per_second,
    -- intensity / temporal clustering (exact, from per-second counts)
    p.active_seconds                                      AS active_seconds,
    p.active_seconds / {w}                                AS active_ratio,
    p.max_trades_per_second                               AS max_trades_per_second,
    p.max_trades_per_second
        / nullIf(b.trade_count / nullIf(p.active_seconds, 0), 0)
                                                          AS burstiness,
    -- Exact inter-arrival, recovered from the merged ms offsets. A window with
    -- one trade has no gap, hence the length guard.
    if(length(p.gaps) = 0, NULL,
       arrayReduce('quantile(0.5)', p.gaps))              AS median_interarrival_ms,
    if(length(p.gaps) = 0, NULL,
       arrayReduce('quantile(0.9)', p.gaps))              AS p90_interarrival_ms,
    -- Share of trades arriving in the *same millisecond* as their predecessor.
    -- This replaces a p10 percentile, which is useless here: the exchange
    -- stamps at millisecond resolution and 32% of gaps are already 0, so p10 is
    -- pinned at zero for almost every window. As a ratio it stays informative —
    -- simultaneous fills are a strong algo-execution tell.
    if(length(p.gaps) = 0, NULL,
       arrayCount(x -> x = 0, p.gaps) / length(p.gaps))    AS same_ms_share,
    -- size distribution
    b.volume / nullIf(b.trade_count, 0)                   AS avg_trade_size,
    b.qty_quantiles[1]                                    AS median_trade_size,
    b.qty_quantiles[2]                                    AS p95_trade_size,
    b.max_trade_size                                      AS max_trade_size,
    b.qty_quantiles[2] / nullIf(b.qty_quantiles[1], 0)    AS p95_to_median,
    -- size concentration (threshold-free large-trade clustering)
    b.qty_sq / nullIf(pow(toFloat64(b.volume), 2), 0)     AS size_hhi,
    b.max_trade_size / nullIf(toFloat64(b.volume), 0)     AS top_trade_share,
    -- price
    b.notional / nullIf(toFloat64(b.volume), 0)           AS vwap,
    b.open                                                AS open,
    b.high                                                AS high,
    b.low                                                 AS low,
    b.close                                               AS close,
    b.close / nullIf(b.open, 0) - 1                       AS ret,
    (b.high - b.low) / nullIf(b.low, 0)                   AS price_range,
    p.realized_vol                                        AS realized_vol,
    -- directional flow (real aggressor side)
    b.buy_volume                                          AS buy_volume,
    b.sell_volume                                         AS sell_volume,
    (toFloat64(b.buy_volume) - b.sell_volume)
        / nullIf(toFloat64(b.buy_volume + b.sell_volume), 0)
                                                          AS trade_imbalance,
    (toFloat64(b.buy_trades) - b.sell_trades)
        / nullIf(toFloat64(b.buy_trades + b.sell_trades), 0)
                                                          AS count_imbalance,
    -- impact / absorption: much volume moving price little is the interesting case
    abs(b.close / nullIf(b.open, 0) - 1)
        / nullIf(log1p(toFloat64(b.volume)), 0)           AS impact,
    log1p(toFloat64(b.volume))
        / nullIf(abs(b.close / nullIf(b.open, 0) - 1) * 10000 + 1, 0)
                                                          AS absorption
FROM base AS b
INNER JOIN pace AS p
    ON b.symbol = p.symbol AND b.window_start = p.window_start
""".strip()


# Feature columns the scorer consumes, in a stable order. `window_start` and
# `symbol` are keys, not features; prices are excluded (levels, not behaviour).
FEATURE_COLUMNS = (
    "trade_count",
    "volume",
    "trades_per_second",
    "volume_per_second",
    "active_ratio",
    "max_trades_per_second",
    "burstiness",
    "median_interarrival_ms",
    "p90_interarrival_ms",
    "same_ms_share",
    "avg_trade_size",
    "median_trade_size",
    "p95_trade_size",
    "max_trade_size",
    "p95_to_median",
    "size_hhi",
    "top_trade_share",
    "ret",
    "price_range",
    "realized_vol",
    "trade_imbalance",
    "count_imbalance",
    "impact",
    "absorption",
)
