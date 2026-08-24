"""Large-order (Layer 3 block) service.

Aggregates large-order blocks to **one net bubble per trading day**, for
plotting on the 1D candlestick chart in the style of the volume-cluster Pine
indicator:

  - net delta  = buy notional - sell notional  (sign decides the bubble side)
  - the frontend places the bubble at the day's HIGH (net-sell) or LOW
    (net-buy), sizes it by tier, and labels it with the day's large-order
    volume.

Each day's `time` is the unix-seconds of UTC-midnight of the Vietnam trading
date — exactly what the frontend's `formatChartTime` produces for daily bars,
so bubbles line up with the correct 1D candle.

Blocks come from ``large_orders_live`` — a view over the ``large_order_blocks``
materialized view, which ClickHouse maintains from ``ticks`` as they arrive.
Both history and today come from that one source, so no reconciliation pass is
involved and there is nothing to merge.

The view stores *every* block, with no notional floor: a materialized view sees
one INSERT at a time, and a partial block can sit below a threshold while the
finished one clears it, so filtering cannot happen at write time. Pass
``min_value`` to get only large blocks — unfiltered results include every
sub-second fill cluster, which is a much larger population than the retired
``large_orders`` table held.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

from clickhouse_connect.driver import Client
from pydantic import BaseModel

SIDE_BUY = 1
SIDE_SELL = 2

LIVE_VIEW = "large_orders_live"


class LargeOrderDay(BaseModel):
    date: str            # ICT trading date YYYY-MM-DD
    time: int            # unix seconds, UTC-midnight of the VN date (daily candle x)
    side: int            # net side: 1=BUY if net_delta >= 0 else 2=SELL
    net_delta: float     # buy notional - sell notional (signed)
    buy_value: float     # gross buy notional
    sell_value: float    # gross sell notional
    total_value: float   # buy + sell notional (drives bubble size tier)
    buy_qty: int
    sell_qty: int
    total_qty: int       # buy + sell shares (the volume label)
    num_trades: int      # fills across the day's large blocks
    block_count: int     # raw blocks aggregated


class LargeOrdersResponse(BaseModel):
    symbol: str
    blocks: List[LargeOrderDay]


def _day_epoch(d: date) -> int:
    """UTC-midnight of the VN trading date, in unix seconds (daily candle x)."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


# Types are pinned with explicit casts because the two sources disagree:
# `large_orders.num_trades` is Int64 while the live view's is UInt64 (it sums a
# SimpleAggregateFunction(sum, UInt64)). Without these the UNION fails with
# "There is no supertype for types UInt64, Int64" (NO_COMMON_TYPE).
_DAY_AGG_COLUMNS = """
            toDate(sending_time, 'Asia/Ho_Chi_Minh')            AS d,
            toFloat64(sumIf(dollar_value, side = 1))            AS buy_value,
            toFloat64(sumIf(dollar_value, side = 2))            AS sell_value,
            toInt64(sumIf(total_qty, side = 1))                 AS buy_qty,
            toInt64(sumIf(total_qty, side = 2))                 AS sell_qty,
            toInt64(sum(num_trades))                            AS num_trades,
            toInt64(count())                                    AS block_count
"""


class LargeOrdersService:
    def __init__(self, client: Client):
        self.client = client

    def _build_sql(self, min_value: Optional[float]) -> str:
        """Per-day rollup of blocks from the live view.

        `min_value` filters individual blocks, so it belongs in the WHERE —
        before the per-day rollup, not after. No FINAL: the view already
        aggregates the materialized view's partial rows on read.
        """
        min_clause = (
            "AND dollar_value >= {min_value:Float64}" if min_value is not None else ""
        )
        return f"""
            SELECT {_DAY_AGG_COLUMNS}
            FROM {LIVE_VIEW}
            WHERE symbol = {{symbol:String}}
              AND toDate(sending_time, 'Asia/Ho_Chi_Minh')
                  BETWEEN {{from:String}} AND {{to:String}}
              {min_clause}
            GROUP BY d
            ORDER BY d
        """

    def get_blocks(
        self,
        symbol: str,
        from_day: date,
        to_day: date,
        min_value: Optional[float] = None,
    ) -> LargeOrdersResponse:
        params = {
            "symbol": symbol,
            "from": from_day.isoformat(),
            "to": to_day.isoformat(),
        }
        if min_value is not None:
            params["min_value"] = min_value

        result = self.client.query(self._build_sql(min_value), parameters=params)

        blocks: List[LargeOrderDay] = []
        for r in result.result_rows:
            d = r[0]
            buy_value = float(r[1])
            sell_value = float(r[2])
            buy_qty = int(r[3])
            sell_qty = int(r[4])
            net_delta = buy_value - sell_value
            blocks.append(
                LargeOrderDay(
                    date=d.isoformat(),
                    time=_day_epoch(d),
                    side=SIDE_BUY if net_delta >= 0 else SIDE_SELL,
                    net_delta=net_delta,
                    buy_value=buy_value,
                    sell_value=sell_value,
                    total_value=buy_value + sell_value,
                    buy_qty=buy_qty,
                    sell_qty=sell_qty,
                    total_qty=buy_qty + sell_qty,
                    num_trades=int(r[5]),
                    block_count=int(r[6]),
                )
            )

        return LargeOrdersResponse(symbol=symbol, blocks=blocks)
