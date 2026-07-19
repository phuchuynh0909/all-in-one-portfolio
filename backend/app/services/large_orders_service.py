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
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

from clickhouse_connect.driver import Client
from pydantic import BaseModel

SIDE_BUY = 1
SIDE_SELL = 2


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


class LargeOrdersService:
    def __init__(self, client: Client):
        self.client = client

    def get_blocks(
        self,
        symbol: str,
        from_day: date,
        to_day: date,
        min_value: Optional[float] = None,
    ) -> LargeOrdersResponse:
        sql = """
            SELECT toDate(sending_time, 'Asia/Ho_Chi_Minh')        AS d,
                   sumIf(dollar_value, side = 1)                    AS buy_value,
                   sumIf(dollar_value, side = 2)                    AS sell_value,
                   sumIf(total_qty, side = 1)                       AS buy_qty,
                   sumIf(total_qty, side = 2)                       AS sell_qty,
                   sum(num_trades)                                  AS num_trades,
                   count()                                          AS block_count
            FROM large_orders FINAL
            WHERE symbol = {symbol:String}
              AND toDate(sending_time, 'Asia/Ho_Chi_Minh') BETWEEN {from:String} AND {to:String}
              {min_value_clause}
            GROUP BY d
            ORDER BY d
        """
        params = {
            "symbol": symbol,
            "from": from_day.isoformat(),
            "to": to_day.isoformat(),
        }
        min_value_clause = ""
        if min_value is not None:
            min_value_clause = "AND dollar_value >= {min_value:Float64}"
            params["min_value"] = min_value
        sql = sql.replace("{min_value_clause}", min_value_clause)

        result = self.client.query(sql, parameters=params)

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
