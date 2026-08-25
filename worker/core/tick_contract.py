"""
Canonical tick data contract module.

This module is the single source of truth for transforming raw tick data
(from either stream or API) into the canonical ClickHouse storage format.

Handles both API payload style (matchPrice, matchQtty, sendingTime, side)
and stream-parsed style (ts, price, size, side as int).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Side mapping constants
SIDE_BUY = 1
SIDE_SELL = 2
SIDE_UNKNOWN = 0

# Board of the order book a trade matched on: "G1" is the main continuous book,
# "G4"/"G7" odd lot, "T1".."T6" put-through (negotiated off-book, and priced
# accordingly). Feeds spell it two ways — the OpenAPI Trade-Extra frames send
# "G1" while the legacy stream and the mock source send "BOARD_ID_G1" — so the
# prefix is stripped here, before the value reaches a column that queries filter
# on. Two spellings in one LowCardinality column would make `board_id = 'G1'`
# quietly miss half the rows.
_BOARD_ID_PREFIX = "BOARD_ID_"

# Rows written before board_id existed carry this. It is deliberately not "G1":
# those rows were ingested from a nine-board subscription, so their board is
# genuinely unknown rather than known to be the main book.
BOARD_UNKNOWN = ""


def normalize_board(value) -> str:
    """Map a raw ``boardId`` to its bare form ("BOARD_ID_G1" -> "G1")."""
    if value is None:
        return BOARD_UNKNOWN
    text = str(value).strip().upper()
    if text.startswith(_BOARD_ID_PREFIX):
        text = text[len(_BOARD_ID_PREFIX):]
    return text


def normalize_tick(raw: dict) -> Optional[dict]:
    """
    Convert raw API/stream tick to canonical form.

    Args:
        raw: Raw tick dict from either API or stream parser

    Returns:
        Canonical tick dict with keys:
        - symbol (str)
        - sending_time (datetime UTC-aware)
        - match_price (float)
        - match_qty (int | None)
        - side (int | None)
        - received_at (datetime UTC-aware)

        Returns None (and logs warning) for malformed/unrecoverable rows.
    """
    try:
        # Detect input style: API style has 'sendingTime', stream style has 'ts'
        if "sendingTime" in raw:
            # API payload style
            symbol = raw.get("symbol")
            if not symbol:
                logger.warning("Missing symbol in API payload")
                return None

            # Parse ISO8601 sendingTime with Z/+00:00 to UTC datetime
            sending_time_str = raw.get("sendingTime")
            if not sending_time_str:
                logger.warning("Missing sendingTime in API payload")
                return None

            try:
                # Handle Z suffix and convert to UTC
                sending_time = datetime.fromisoformat(
                    sending_time_str.replace("Z", "+00:00")
                )
                # Ensure UTC timezone
                if sending_time.tzinfo is None:
                    sending_time = sending_time.replace(tzinfo=timezone.utc)
                else:
                    sending_time = sending_time.astimezone(timezone.utc)
            except (ValueError, AttributeError) as e:
                logger.warning(f"Failed to parse sendingTime '{sending_time_str}': {e}")
                return None

            # Coerce match_price to float
            match_price_raw = raw.get("matchPrice")
            if match_price_raw is None:
                logger.warning("Missing matchPrice in API payload")
                return None
            try:
                match_price = float(match_price_raw)
            except (ValueError, TypeError):
                logger.warning(
                    f"Failed to coerce matchPrice to float: {match_price_raw}"
                )
                return None

            # Coerce match_qty to int, treat None/"" as None (nullable)
            match_qty_raw = raw.get("matchQtty")
            if match_qty_raw is None or match_qty_raw == "":
                match_qty = None
            else:
                try:
                    match_qty = int(float(match_qty_raw))
                except (ValueError, TypeError):
                    logger.warning(
                        f"Failed to coerce matchQtty to int: {match_qty_raw}"
                    )
                    return None

            # Coerce side to int using mapping: BUY=1, SELL=2, unknown=0
            side_raw = raw.get("side")
            if side_raw in ("B", "SIDE_BUY", 1):
                side = SIDE_BUY
            elif side_raw in ("S", "SIDE_SELL", 2):
                side = SIDE_SELL
            else:
                side = SIDE_UNKNOWN

        elif "ts" in raw:
            # Stream-parsed style (already parsed by isp.py)
            symbol = raw.get("symbol")
            if not symbol:
                logger.warning("Missing symbol in stream payload")
                return None

            # ts is already a datetime object from isp.py parse_tick
            sending_time = raw.get("ts")
            if not isinstance(sending_time, datetime):
                logger.warning(f"Invalid ts type: {type(sending_time)}")
                return None

            # Ensure UTC timezone
            if sending_time.tzinfo is None:
                sending_time = sending_time.replace(tzinfo=timezone.utc)
            else:
                sending_time = sending_time.astimezone(timezone.utc)

            # Coerce match_price to float
            price_raw = raw.get("price")
            if price_raw is None:
                logger.warning("Missing price in stream payload")
                return None
            try:
                match_price = float(price_raw)
            except (ValueError, TypeError):
                logger.warning(f"Failed to coerce price to float: {price_raw}")
                return None

            # Coerce match_qty to int, treat None/"" as None (nullable)
            match_qty_raw = raw.get("size")
            if match_qty_raw is None or match_qty_raw == "":
                match_qty = None
            else:
                try:
                    match_qty = int(float(match_qty_raw))
                except (ValueError, TypeError):
                    logger.warning(f"Failed to coerce size to int: {match_qty_raw}")
                    return None

            # side is already an int from isp.py parse_tick (1 or 2)
            side = raw.get("side", SIDE_UNKNOWN)
            if not isinstance(side, int):
                try:
                    side = int(side)
                except (ValueError, TypeError):
                    logger.warning(f"Failed to coerce side to int: {side}")
                    side = SIDE_UNKNOWN

        else:
            logger.warning(
                "Cannot detect input style: missing both 'sendingTime' and 'ts'"
            )
            return None

        received_at = datetime.now(timezone.utc)

        return {
            "symbol": symbol,
            "sending_time": sending_time,
            "match_price": match_price,
            "match_qty": match_qty if match_qty is not None else 0,
            "side": side,
            "received_at": received_at,
            # Both input styles spell the field "boardId"; absent in older
            # stream payloads, which is what BOARD_UNKNOWN records.
            "board_id": normalize_board(raw.get("boardId")),
        }

    except Exception as e:
        logger.warning(f"Unrecoverable parse error in normalize_tick: {e}")
        return None


def to_clickhouse_tuple(tick: dict) -> tuple:
    """
    Convert canonical tick dict to insertion tuple for ClickHouse.

    Args:
        tick: Canonical tick dict from normalize_tick()

    Returns:
        Tuple in TICKS_ARROW_SCHEMA column order:
        (symbol, sending_time, match_price, match_qty, side, received_at,
         board_id)

    ``board_id`` is last because it was added after the other six: the column
    goes at the end of the ClickHouse table too, so an existing table takes it
    as a plain ADD COLUMN and every explicit column list stays valid. Read with
    ``.get`` so a dict built before this field existed still converts.
    """
    return (
        tick["symbol"],
        tick["sending_time"],
        tick["match_price"],
        tick["match_qty"],
        tick["side"],
        tick["received_at"],
        tick.get("board_id", BOARD_UNKNOWN),
    )
