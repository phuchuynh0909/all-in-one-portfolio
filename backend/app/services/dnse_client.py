"""DNSE OpenAPI client — real-time matched prices for VN equities.

Used to keep the chart's current daily bar live: `get_latest_quote` returns the
latest match for a symbol, normalized into the same price scale as the historical
bars (thousands of VND).

Requests are signed with HMAC-SHA256 over a canonical string built from the
request target, the `Date` header and a per-request nonce. The signing secret
must never reach the browser, which is why this proxy exists.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import time
import urllib.parse
import uuid
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

import httpx
from fastapi import HTTPException
from loguru import logger

from app.core.settings import settings
from app.schemas.quote import LatestQuote

DNSE_OPENAPI_BASE = "https://openapi.dnse.com.vn"

#: Boards to prefer when the exchange reports several for one symbol. "G1" is the
#: main continuous order book (round lots) — the one the daily OHLCV bars track.
#: "G7"/"G4" (odd lot) are only used when the main board has not traded.
#: Put-through boards ("T3", "T6", ...) are ignored: their prices are negotiated
#: off-book and would corrupt the bar.
BOARD_PRIORITY = ("G1", "G7", "G4")

#: Real-time endpoint, so a short TTL — enough to collapse the polling of many
#: browser tabs into one upstream call without visibly lagging the price.
CACHE_TTL_SECONDS = 2.0

#: An end-of-day fallback quote only changes once per session, so it is held far
#: longer: without this, polling an index would query ClickHouse on every tick.
EOD_CACHE_TTL_SECONDS = 120.0

#: Upper bound on one batch request, to keep the upstream fan-out bounded.
MAX_BATCH_SYMBOLS = 60

#: Concurrent upstream requests per batch.
BATCH_CONCURRENCY = 8

_cache: dict[str, tuple[float, LatestQuote]] = {}

#: Previous close by (symbol, trading date). The reference price for a given
#: trading date never changes, so this is cached for the process lifetime.
_prev_close_cache: dict[tuple[str, date], Optional[float]] = {}


def _signed_headers(method: str, path: str) -> dict[str, str]:
    """Builds the `Date` / `X-Signature` / `X-API-Key` headers for one request."""
    api_key = settings.dnse_api_key
    api_secret = settings.dnse_api_secret
    if not api_key or not api_secret:
        raise HTTPException(
            status_code=503,
            detail=(
                "DNSE real-time quotes are not configured: set DNSE_API_KEY "
                "and DNSE_API_SECRET in the backend environment."
            ),
        )

    date_value = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    nonce = uuid.uuid4().hex

    signature_string = (
        f"(request-target): {method.lower()} {path}\n"
        f"date: {date_value}\n"
        f"nonce: {nonce}"
    )
    digest = hmac.new(
        api_secret.encode("utf-8"),
        signature_string.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = urllib.parse.quote(base64.b64encode(digest).decode("utf-8"), safe="")

    x_signature = (
        f'Signature keyId="{api_key}",'
        f'algorithm="hmac-sha256",'
        f'headers="(request-target) date",'
        f'signature="{signature}",'
        f'nonce="{nonce}"'
    )
    return {
        "Date": date_value,
        "X-Signature": x_signature,
        "X-API-Key": api_key,
        "version": settings.dnse_api_version,
    }


def _to_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric


def _parse_time(value: Any) -> Optional[datetime]:
    """Parses DNSE's `"YYYY-MM-DD HH:MM:SS.mmm"` match time (VN local, naive)."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _pick_trade(trades: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Picks the trade that represents the symbol's continuous-market price."""
    candidates = [
        t for t in trades
        if _to_float(t.get("matchPrice")) and _parse_time(t.get("time")) is not None
    ]
    if not candidates:
        return None

    for board in BOARD_PRIORITY:
        on_board = [t for t in candidates if t.get("boardId") == board]
        if on_board:
            # More than one entry per board is not expected; newest wins.
            return max(on_board, key=lambda t: _parse_time(t["time"]))
    return None


def _to_quote(symbol: str, trade: dict[str, Any]) -> LatestQuote:
    matched_at = _parse_time(trade["time"])
    assert matched_at is not None  # guaranteed by _pick_trade
    price = float(trade["matchPrice"])

    # The exchange reports 0 for these before the board has traded (and on
    # put-through boards) — treat 0 as "not reported" rather than a real price.
    def positive(key: str) -> Optional[float]:
        value = _to_float(trade.get(key))
        return value if value and value > 0 else None

    return LatestQuote(
        symbol=symbol,
        trading_date=matched_at.date(),
        time=matched_at,
        price=price,
        open=positive("openPrice"),
        high=positive("highestPrice"),
        low=positive("lowestPrice"),
        volume=_to_float(trade.get("totalVolumeTraded")) * 10,
        board_id=trade.get("boardId"),
        market_id=trade.get("marketId"),
    )


def _fetch_prev_closes(symbols: list[str], trading_date: date) -> dict[str, float]:
    """Last EOD close strictly before `trading_date`, per symbol, in one query.

    The reference comes from the project's own ClickHouse history rather than the
    quote provider, so the day's change is measured against the same closes the
    chart's bars are drawn from.
    """
    if not symbols:
        return {}
    # Imported lazily: this module is also used by request paths that never need
    # a ClickHouse connection.
    from app.services.stock_service import _clickhouse_client

    table = os.getenv("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod")
    quoted = ", ".join(f"'{s.replace(chr(39), chr(39) * 2)}'" for s in symbols)
    sql = (
        f"SELECT symbol, argMax(close, date) FROM {settings.clickhouse_db}.{table} FINAL "
        f"WHERE symbol IN ({quoted}) AND date < toDate('{trading_date.isoformat()}') "
        f"GROUP BY symbol"
    )
    try:
        client = _clickhouse_client()
        try:
            rows = client.query(sql).result_rows
        finally:
            client.close()
    except Exception as exc:
        logger.warning("Previous-close lookup failed for {} symbols: {}", len(symbols), exc)
        return {}

    return {
        str(symbol): float(close)
        for symbol, close in rows
        if close is not None
    }


async def _with_prev_close(quotes: list[LatestQuote]) -> None:
    """Fills `prev_close` / `change` / `change_pct` on `quotes`, in place."""
    missing: dict[date, list[str]] = {}
    for quote in quotes:
        key = (quote.symbol, quote.trading_date)
        if key not in _prev_close_cache:
            missing.setdefault(quote.trading_date, []).append(quote.symbol)

    for trading_date, symbols in missing.items():
        found = await asyncio.to_thread(_fetch_prev_closes, symbols, trading_date)
        for symbol in symbols:
            # Cache misses too: a symbol with no history should not be requeried
            # on every poll.
            _prev_close_cache[(symbol, trading_date)] = found.get(symbol)

    for quote in quotes:
        prev = _prev_close_cache.get((quote.symbol, quote.trading_date))
        if prev is None or prev <= 0:
            continue
        quote.prev_close = prev
        quote.change = quote.price - prev
        quote.change_pct = (quote.price - prev) / prev * 100.0


def _fetch_eod_quotes(symbols: list[str]) -> dict[str, LatestQuote]:
    """Last two EOD bars per symbol, shaped as `eod`-sourced quotes.

    The quote provider only serves traded instruments, so indices (VNINDEX,
    VN30) and symbols that have not traded yet today have no live match. Those
    rows fall back to the project's own history rather than rendering empty.
    """
    if not symbols:
        return {}
    from app.services.stock_service import _clickhouse_client

    table = os.getenv("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod")
    quoted = ", ".join(f"'{s.replace(chr(39), chr(39) * 2)}'" for s in symbols)
    sql = (
        f"SELECT symbol, date, open, high, low, close, volume "
        f"FROM {settings.clickhouse_db}.{table} FINAL "
        f"WHERE symbol IN ({quoted}) "
        f"ORDER BY symbol, date DESC LIMIT 2 BY symbol"
    )
    try:
        client = _clickhouse_client()
        try:
            rows = client.query(sql).result_rows
        finally:
            client.close()
    except Exception as exc:
        logger.warning("EOD quote fallback failed for {} symbols: {}", len(symbols), exc)
        return {}

    # Two rows per symbol, newest first: the latest bar plus its reference close.
    by_symbol: dict[str, list[tuple]] = {}
    for row in rows:
        by_symbol.setdefault(str(row[0]), []).append(row)

    quotes: dict[str, LatestQuote] = {}
    for symbol, symbol_rows in by_symbol.items():
        latest = symbol_rows[0]
        _, bar_date, open_, high, low, close, volume = latest
        if close is None:
            continue
        prev_close = None
        if len(symbol_rows) > 1 and symbol_rows[1][5] is not None:
            prev_close = float(symbol_rows[1][5])
        as_of = bar_date if isinstance(bar_date, date) else datetime.fromisoformat(str(bar_date)).date()

        quote = LatestQuote(
            symbol=symbol,
            trading_date=as_of,
            time=datetime.combine(as_of, datetime.min.time()),
            price=float(close),
            open=_to_float(open_),
            high=_to_float(high),
            low=_to_float(low),
            volume=_to_float(volume),
            source="eod",
        )
        if prev_close and prev_close > 0:
            quote.prev_close = prev_close
            quote.change = quote.price - prev_close
            quote.change_pct = (quote.price - prev_close) / prev_close * 100.0
        quotes[symbol] = quote

    return quotes


async def get_latest_quote(symbol: str) -> LatestQuote:
    """Latest matched trade for `symbol`, cached for {CACHE_TTL_SECONDS} seconds."""
    symbol = symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")

    cached = _cache.get(symbol)
    now = time.monotonic()
    if cached:
        ttl = EOD_CACHE_TTL_SECONDS if cached[1].source == "eod" else CACHE_TTL_SECONDS
        if now - cached[0] < ttl:
            return cached[1]

    path = f"/price/{symbol}/trades/latest"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                f"{DNSE_OPENAPI_BASE}{path}",
                headers=_signed_headers("get", path),
            )
    except httpx.HTTPError as exc:
        logger.warning("DNSE latest-trade request failed for {}: {}", symbol, exc)
        raise HTTPException(status_code=502, detail="Upstream quote provider unavailable")

    if response.status_code == 401 or response.status_code == 403:
        logger.error("DNSE rejected the API credentials ({})", response.status_code)
        raise HTTPException(status_code=502, detail="Quote provider rejected credentials")
    if response.status_code >= 400:
        logger.warning(
            "DNSE latest-trade returned {} for {}: {}",
            response.status_code, symbol, response.text[:200],
        )
        raise HTTPException(status_code=502, detail="Upstream quote provider error")

    payload = response.json()
    trades = payload.get("trades") if isinstance(payload, dict) else None
    trade = _pick_trade(trades) if isinstance(trades, list) else None
    if trade is None:
        # No continuous-market trade: an index (the provider only trades
        # instruments), or a symbol that has not matched yet today. Answer from
        # our own last end-of-day bar so callers that poll — the chart's
        # real-time subscription among them — do not see a repeated 404.
        fallback = (await asyncio.to_thread(_fetch_eod_quotes, [symbol])).get(symbol)
        if fallback is None:
            raise HTTPException(status_code=404, detail=f"No recent trade for {symbol}")
        logger.debug("No live trade for {}; answered from the last EOD bar", symbol)
        _cache[symbol] = (now, fallback)
        return fallback

    quote = _to_quote(symbol, trade)
    await _with_prev_close([quote])
    _cache[symbol] = (now, quote)
    return quote


async def get_latest_quotes(symbols: Iterable[str]) -> tuple[list[LatestQuote], list[str]]:
    """Quotes for many symbols, in the order requested.

    Returns `(quotes, unavailable)`. A symbol the provider cannot answer for —
    unknown ticker, no trade yet today, or a transient upstream error — is
    reported in `unavailable` instead of failing the whole batch, so one bad
    ticker in a watchlist cannot blank the rest.
    """
    ordered: list[str] = []
    for raw in symbols:
        symbol = raw.strip().upper()
        if symbol and symbol not in ordered:
            ordered.append(symbol)

    if not ordered:
        return [], []
    if len(ordered) > MAX_BATCH_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_BATCH_SYMBOLS} symbols per request",
        )

    limiter = asyncio.Semaphore(BATCH_CONCURRENCY)

    async def one(symbol: str) -> Optional[LatestQuote]:
        async with limiter:
            try:
                return await get_latest_quote(symbol)
            except HTTPException:
                return None
            except Exception as exc:  # never let one symbol break the batch
                logger.warning("Quote lookup failed for {}: {}", symbol, exc)
                return None

    results = await asyncio.gather(*(one(symbol) for symbol in ordered))
    by_symbol = {symbol: quote for symbol, quote in zip(ordered, results) if quote is not None}

    # Anything without a live match falls back to the last EOD bar.
    missing = [symbol for symbol in ordered if symbol not in by_symbol]
    if missing:
        by_symbol.update(await asyncio.to_thread(_fetch_eod_quotes, missing))

    quotes = [by_symbol[symbol] for symbol in ordered if symbol in by_symbol]
    unavailable = [symbol for symbol in ordered if symbol not in by_symbol]
    return quotes, unavailable
