"""Block-episode ("large-execution footprint") read service.

Serves the stitched episodes produced by the worker's ``core.large_execution``
detector from the ClickHouse ``block_episodes`` table. Unlike
``large_orders_service`` (which nets each day to a single daily bubble), this
returns the individual intraday episodes so the frontend can mark each
execution footprint on an intraday chart.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

from clickhouse_connect.driver import Client
from clickhouse_connect.driver.exceptions import DatabaseError
from loguru import logger

from app.schemas.block_episode import (
    CANDIDATE_TYPES,
    BlockEpisode,
    BlockEpisodesResponse,
)

SIDE_BUY = 1
SIDE_SELL = 2
_SIDE_LABEL = {SIDE_BUY: "BUY", SIDE_SELL: "SELL"}

BLOCK_EPISODES_TABLE = "block_episodes"


def _is_unknown_table(exc: Exception) -> bool:
    """True when a ClickHouse error is 'table does not exist' (code 60).

    The block_episodes table is created lazily by the worker/reconciler on first
    write, so a fresh cluster legitimately has no table yet — that is an empty
    result, not a server error.
    """
    msg = str(exc)
    return "UNKNOWN_TABLE" in msg or "code: 60" in msg or "Unknown table" in msg


def _epoch(dt) -> int:
    """Unix seconds (UTC) of a ClickHouse DateTime value."""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _iso(dt) -> str:
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class BlockEpisodesService:
    def __init__(self, client: Client):
        self.client = client

    def get_episodes(
        self,
        symbol: str,
        from_day: date,
        to_day: date,
        side: Optional[int] = None,
        candidate_type: Optional[str] = None,
        min_abs_notional: Optional[float] = None,
        limit: int = 1000,
    ) -> BlockEpisodesResponse:
        if candidate_type is not None and candidate_type not in CANDIDATE_TYPES:
            raise ValueError(
                f"candidate_type must be one of {CANDIDATE_TYPES}, got {candidate_type!r}"
            )

        sql = f"""
            SELECT symbol,
                   start_time,
                   end_time,
                   side,
                   candidate_type,
                   signed_notional,
                   abs_notional,
                   num_trades,
                   num_bins,
                   large_print_count,
                   max_abs_z,
                   max_abs_imbalance
            FROM {BLOCK_EPISODES_TABLE} FINAL
            WHERE symbol = {{symbol:String}}
              AND toDate(start_time, 'Asia/Ho_Chi_Minh') BETWEEN {{from:String}} AND {{to:String}}
              {{side_clause}}
              {{type_clause}}
              {{min_notional_clause}}
            ORDER BY start_time
            LIMIT {{limit:UInt32}}
        """
        params = {
            "symbol": symbol,
            "from": from_day.isoformat(),
            "to": to_day.isoformat(),
            "limit": int(limit),
        }

        side_clause = ""
        if side is not None:
            side_clause = "AND side = {side:Int32}"
            params["side"] = int(side)

        type_clause = ""
        if candidate_type is not None:
            type_clause = "AND candidate_type = {candidate_type:String}"
            params["candidate_type"] = candidate_type

        min_notional_clause = ""
        if min_abs_notional is not None:
            min_notional_clause = "AND abs_notional >= {min_abs_notional:Float64}"
            params["min_abs_notional"] = float(min_abs_notional)

        sql = (
            sql.replace("{side_clause}", side_clause)
            .replace("{type_clause}", type_clause)
            .replace("{min_notional_clause}", min_notional_clause)
        )

        try:
            result = self.client.query(sql, parameters=params)
        except DatabaseError as exc:
            if _is_unknown_table(exc):
                logger.info(
                    "block_episodes table not found (not yet created by the "
                    "worker/reconciler); returning empty result for {}",
                    symbol,
                )
                return BlockEpisodesResponse(symbol=symbol, episodes=[])
            raise

        episodes: List[BlockEpisode] = []
        for r in result.result_rows:
            start_epoch = _epoch(r[1])
            end_epoch = _epoch(r[2])
            side_val = int(r[3])
            episodes.append(
                BlockEpisode(
                    symbol=str(r[0]),
                    start_time=_iso(r[1]),
                    end_time=_iso(r[2]),
                    start_epoch=start_epoch,
                    end_epoch=end_epoch,
                    duration_seconds=max(0, end_epoch - start_epoch),
                    side=side_val,
                    side_label=_SIDE_LABEL.get(side_val, "NA"),
                    candidate_type=str(r[4]),
                    signed_notional=float(r[5]),
                    abs_notional=float(r[6]),
                    num_trades=int(r[7]),
                    num_bins=int(r[8]),
                    large_print_count=int(r[9]),
                    max_abs_z=float(r[10]),
                    max_abs_imbalance=float(r[11]),
                )
            )

        return BlockEpisodesResponse(symbol=symbol, episodes=episodes)
