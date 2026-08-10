from __future__ import annotations

import asyncio
import math
from datetime import date
from typing import Any, Optional

import httpx
import numpy as np
from fastapi import HTTPException
from loguru import logger

from app.schemas.cw import (
    CoveredWarrantAnalysis,
    CoveredWarrantAssumptions,
    CoveredWarrantDetail,
    CoveredWarrantGreeks,
    CoveredWarrantResponse,
)
from app.services.stock_service import _load_delta_stocks, get_current_price


DNSE_CW_DETAIL_URL = "https://api-bo.dnse.com.vn/senses-api/covered-warrants"
DNSE_PRICE_QUERY_URL = "https://api.dnse.com.vn/price-api/query"
DEFAULT_RISK_FREE_RATE = 0.045
DEFAULT_VOLATILITY = 0.35
VOL_LOOKBACK_DAYS = 90


def _pick_positive(*values: Any) -> Optional[float]:
    for value in values:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > 0:
            return numeric
    return None


def _normal_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_price(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call",
) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(S - K, 0) if option_type == "call" else max(K - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == "call":
        return S * _normal_cdf(d1) - K * math.exp(-r * T) * _normal_cdf(d2)
    return K * math.exp(-r * T) * _normal_cdf(-d2) - S * _normal_cdf(-d1)


def _implied_vol(
    market_price: float, S: float, K: float, T: float, r: float,
    option_type: str = "call",
    lo: float = 0.001, hi: float = 5.0, tol: float = 1e-5, max_iter: int = 200,
) -> Optional[float]:
    if T <= 0 or market_price <= 0:
        return None
    f = lambda v: _bs_price(S, K, T, r, v, option_type) - market_price
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        val = f(mid)
        if abs(val) < tol:
            return mid
        if f(lo) * val < 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def _infer_option_style(cw_stock_type: str | None) -> str:
    normalized = (cw_stock_type or "").strip().lower()
    if normalized in {"mua", "call"}:
        return "call"
    if normalized in {"ban", "bán", "put"}:
        return "put"
    return "call"


def _annualized_realized_vol(symbol: str) -> tuple[Optional[float], str]:
    try:
        df = _load_delta_stocks(symbols=[symbol], columns=["date", "close", "symbol"])
        df = df[df["symbol"] == symbol].sort_values("date")
        if df.empty:
            return None, "default"

        closes = df["close"].astype(float).replace([np.inf, -np.inf], np.nan).dropna()
        if len(closes) < 20:
            return None, "default"

        log_returns = np.log(closes / closes.shift(1)).dropna()
        if log_returns.empty:
            return None, "default"

        lookback = log_returns.tail(VOL_LOOKBACK_DAYS)
        if len(lookback) < 20:
            return None, "default"

        annualized = float(lookback.std(ddof=1) * math.sqrt(252))
        if not math.isfinite(annualized) or annualized <= 0:
            return None, "default"
        return annualized, f"realized_{len(lookback)}d"
    except Exception as exc:
        logger.warning("Failed to calculate realized vol for {}: {}", symbol, exc)
        return None, "default"


def _latest_close_from_history(symbol: str) -> tuple[Optional[float], str]:
    try:
        df = _load_delta_stocks(symbols=[symbol], columns=["date", "close", "symbol"])
        df = df[df["symbol"] == symbol].sort_values("date")
        if df.empty:
            return None, "missing"
        close = _pick_positive(df["close"].iloc[-1])
        if close is None:
            return None, "missing"
        return close, "delta_latest_close"
    except Exception as exc:
        logger.warning("Failed to load latest close from history for {}: {}", symbol, exc)
        return None, "missing"


def _normalize_strike_price(
    raw_exercise_price: Any,
    stock_price: Optional[float],
) -> tuple[Optional[float], str]:
    strike = _pick_positive(raw_exercise_price)
    if strike is None:
        return None, "missing"

    if stock_price is None or stock_price <= 0:
        return strike, "raw"

    # Vietnamese equity prices in this app are typically represented in "thousand VND"
    # (e.g. HPG 28.45 => 28,450 VND), while CW exercise prices from DNSE often arrive
    # in raw VND integers (e.g. 23316 => 23.316). Align the strike to the stock-price scale.
    if strike / stock_price > 100:
        return strike / 1000.0, "vnd_to_thousand_vnd"

    return strike, "raw"


async def _fetch_cw_quote(client: httpx.AsyncClient, symbol: str) -> dict[str, Any]:
    query = (
        '{'
        f' GetKrxStockInfoBySymbols(symbols: ["{symbol}"], board: 2) {{'
        '   si {'
        '     symbol'
        '     referencePrice'
        '     expectedTradePrice'
        '     changedValue'
        '     changedRatio'
        '     matchPrice'
        '     totalVolumeTraded'
        '     productGrpId'
        '   }'
        ' }'
        '}'
    )
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://www.dnse.com.vn",
        "referer": "https://www.dnse.com.vn/",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
    }
    response = await client.post(
        DNSE_PRICE_QUERY_URL,
        headers=headers,
        json={"query": query},
    )
    
    response.raise_for_status()
    payload = response.json()

    quotes = (
        payload.get("data", {})
        .get("GetKrxStockInfoBySymbols", {})
        .get("si", [])
    )
    if not isinstance(quotes, list):
        return {}

    for quote in quotes:
        if isinstance(quote, dict) and str(quote.get("symbol", "")).upper() == symbol:
            return quote
    return {}


def _compute_greeks(
    *,
    option_style: str,
    stock_price: Optional[float],
    strike_price: Optional[float],
    warrant_price: Optional[float],
    conversion_rate: Optional[float],
    volatility: Optional[float],
    risk_free_rate: float,
    time_to_expiry: float,
) -> tuple[CoveredWarrantGreeks, CoveredWarrantAnalysis]:
    intrinsic_value: Optional[float] = None
    theoretical_price: Optional[float] = None
    time_value: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta_per_day: Optional[float] = None
    vega_per_1pct_vol: Optional[float] = None
    rho_per_1pct_rate: Optional[float] = None
    d1: Optional[float] = None
    d2: Optional[float] = None

    in_the_money: Optional[bool] = None
    moneyness_pct: Optional[float] = None
    break_even_stock_price: Optional[float] = None
    premium_to_break_even_pct: Optional[float] = None
    leverage: Optional[float] = None
    effective_gearing: Optional[float] = None
    theoretical_edge_pct: Optional[float] = None
    parity_price_ratio: Optional[float] = None

    summary_parts: list[str] = []

    if stock_price and strike_price and stock_price > 0 and strike_price > 0:
        if option_style == "call":
            intrinsic_value = max(stock_price - strike_price, 0.0)
            in_the_money = stock_price > strike_price
            moneyness_pct = stock_price / strike_price - 1.0
        else:
            intrinsic_value = max(strike_price - stock_price, 0.0)
            in_the_money = stock_price < strike_price
            moneyness_pct = strike_price / stock_price - 1.0 if stock_price > 0 else None

        if conversion_rate and conversion_rate > 0:
            intrinsic_value = intrinsic_value / conversion_rate

    valid_inputs = (
        stock_price is not None
        and strike_price is not None
        and conversion_rate is not None
        and volatility is not None
        and stock_price > 0
        and strike_price > 0
        and conversion_rate > 0
        and volatility > 0
        and time_to_expiry > 0
    )

    if valid_inputs:
        sqrt_t = math.sqrt(time_to_expiry)
        d1 = (
            math.log(stock_price / strike_price)
            + (risk_free_rate + 0.5 * volatility * volatility) * time_to_expiry
        ) / (volatility * sqrt_t)
        d2 = d1 - volatility * sqrt_t

        if option_style == "call":
            share_price = stock_price * _normal_cdf(d1) - strike_price * math.exp(-risk_free_rate * time_to_expiry) * _normal_cdf(d2)
            delta = _normal_cdf(d1) / conversion_rate
            theta_share = (
                -(stock_price * _normal_pdf(d1) * volatility) / (2.0 * sqrt_t)
                - risk_free_rate * strike_price * math.exp(-risk_free_rate * time_to_expiry) * _normal_cdf(d2)
            )
            rho_share = strike_price * time_to_expiry * math.exp(-risk_free_rate * time_to_expiry) * _normal_cdf(d2)
        else:
            share_price = strike_price * math.exp(-risk_free_rate * time_to_expiry) * _normal_cdf(-d2) - stock_price * _normal_cdf(-d1)
            delta = (_normal_cdf(d1) - 1.0) / conversion_rate
            theta_share = (
                -(stock_price * _normal_pdf(d1) * volatility) / (2.0 * sqrt_t)
                + risk_free_rate * strike_price * math.exp(-risk_free_rate * time_to_expiry) * _normal_cdf(-d2)
            )
            rho_share = -strike_price * time_to_expiry * math.exp(-risk_free_rate * time_to_expiry) * _normal_cdf(-d2)

        gamma = _normal_pdf(d1) / (stock_price * volatility * sqrt_t) / conversion_rate
        vega_share = stock_price * _normal_pdf(d1) * sqrt_t

        theoretical_price = share_price / conversion_rate
        theta_per_day = theta_share / conversion_rate / 365.0
        vega_per_1pct_vol = vega_share / conversion_rate / 100.0
        rho_per_1pct_rate = rho_share / conversion_rate / 100.0

    if warrant_price is not None and intrinsic_value is not None:
        time_value = warrant_price - intrinsic_value
        if intrinsic_value > 0:
            parity_price_ratio = warrant_price / intrinsic_value

    if warrant_price and conversion_rate and strike_price:
        if option_style == "call":
            break_even_stock_price = strike_price + warrant_price * conversion_rate
            if stock_price and stock_price > 0:
                premium_to_break_even_pct = break_even_stock_price / stock_price - 1.0
        else:
            break_even_stock_price = strike_price - warrant_price * conversion_rate
            if stock_price and stock_price > 0:
                premium_to_break_even_pct = 1.0 - break_even_stock_price / stock_price

    if warrant_price and warrant_price > 0 and stock_price and stock_price > 0 and conversion_rate and conversion_rate > 0:
        leverage = stock_price / (warrant_price * conversion_rate)
        if delta is not None:
            effective_gearing = abs(delta) * stock_price / warrant_price
        if theoretical_price is not None:
            theoretical_edge_pct = theoretical_price / warrant_price - 1.0

    if in_the_money is not None:
        summary_parts.append("ITM" if in_the_money else "OTM")
    if leverage is not None:
        summary_parts.append(f"leverage {leverage:.2f}x")
    if effective_gearing is not None:
        summary_parts.append(f"effective gearing {effective_gearing:.2f}x")
    if theoretical_edge_pct is not None:
        if theoretical_edge_pct > 0.05:
            summary_parts.append("trading below theoretical value")
        elif theoretical_edge_pct < -0.05:
            summary_parts.append("trading above theoretical value")
        else:
            summary_parts.append("near theoretical value")
    if premium_to_break_even_pct is not None:
        summary_parts.append(f"break-even premium {premium_to_break_even_pct * 100:.2f}%")

    summary = ", ".join(summary_parts) if summary_parts else "Insufficient pricing inputs for full CW analysis"

    greeks = CoveredWarrantGreeks(
        option_style=option_style,
        theoretical_price=theoretical_price,
        intrinsic_value=intrinsic_value,
        time_value=time_value,
        delta=delta,
        gamma=gamma,
        theta_per_day=theta_per_day,
        vega_per_1pct_vol=vega_per_1pct_vol,
        rho_per_1pct_rate=rho_per_1pct_rate,
        d1=d1,
        d2=d2,
    )
    analysis = CoveredWarrantAnalysis(
        moneyness_pct=moneyness_pct,
        break_even_stock_price=break_even_stock_price,
        premium_to_break_even_pct=premium_to_break_even_pct,
        leverage=leverage,
        effective_gearing=effective_gearing,
        theoretical_edge_pct=theoretical_edge_pct,
        parity_price_ratio=parity_price_ratio,
        in_the_money=in_the_money,
        summary=summary,
    )
    return greeks, analysis


async def get_covered_warrant(symbol: str) -> CoveredWarrantResponse:
    symbol = symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            detail_response, quote_result = await asyncio.gather(
                client.get(DNSE_CW_DETAIL_URL, params={"symbol": symbol}),
                _fetch_cw_quote(client, symbol),
                return_exceptions=True,
            )
            detail_response.raise_for_status()
            payload = detail_response.json()
            if isinstance(quote_result, Exception):
                logger.warning("Failed to fetch DNSE CW quote for {}: {}", symbol, quote_result)
                quote_payload = {}
            else:
                quote_payload = quote_result
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=f"DNSE CW request failed for {symbol}") from exc
    except Exception as exc:
        logger.error("Failed to fetch covered warrant {}: {}", symbol, exc)
        raise HTTPException(status_code=502, detail=f"Unable to fetch covered warrant {symbol}") from exc

    if not isinstance(payload, dict) or not payload.get("symbol"):
        raise HTTPException(status_code=404, detail=f"Covered warrant {symbol} not found")

    base_stock_code = payload.get("baseStockCode")
    stock_price = _pick_positive(payload.get("baseStockPrice"))
    stock_price_source = "dnse"
    if stock_price is None and base_stock_code:
        stock_price = await get_current_price(base_stock_code)
        stock_price_source = "clickhouse_latest_close" if stock_price is not None else "missing"
    if stock_price is None and base_stock_code:
        stock_price, stock_price_source = _latest_close_from_history(base_stock_code)
    if stock_price is None:
        stock_price_source = "missing"

    exercise_price, _exercise_price_source = _normalize_strike_price(
        payload.get("exercisePrice"),
        stock_price,
    )

    warrant_price = _pick_positive(
        quote_payload.get("matchPrice"),
        quote_payload.get("expectedTradePrice"),
        quote_payload.get("referencePrice"),
        payload.get("lastPrice"),
        payload.get("closePrice"),
        payload.get("basicPrice"),
        payload.get("offeringPrice"),
    )
    warrant_price_source = "missing"
    for key, source in (
        ("matchPrice", "dnse_quote_match_price"),
        ("expectedTradePrice", "dnse_quote_expected_trade_price"),
        ("referencePrice", "dnse_quote_reference_price"),
        ("lastPrice", "dnse_last_price"),
        ("closePrice", "dnse_close_price"),
        ("basicPrice", "dnse_basic_price"),
        ("offeringPrice", "dnse_offering_price"),
    ):
        value = quote_payload.get(key) if key in quote_payload else payload.get(key)
        if _pick_positive(value) is not None:
            warrant_price_source = source
            break

    last_trading_date = payload.get("lastTradingDate")
    expiry_date = None
    if last_trading_date:
        try:
            expiry_date = date.fromisoformat(str(last_trading_date).replace("Z", "").split("T")[0])
        except ValueError:
            expiry_date = None
    days_to_expiry = max((expiry_date - date.today()).days, 0) if expiry_date else 0
    time_to_expiry_years = max(days_to_expiry / 365.0, 1 / 365.0)

    hist_vol: Optional[float] = None
    if base_stock_code:
        hist_vol, _ = _annualized_realized_vol(base_stock_code)

    volatility = None
    volatility_source = "default"
    option_style_for_iv = _infer_option_style(payload.get("cwStockType"))
    conversion_rate_raw = _pick_positive(payload.get("conversionRate"))
    if warrant_price and stock_price and exercise_price and conversion_rate_raw and days_to_expiry > 0:
        iv = _implied_vol(
            warrant_price * conversion_rate_raw,
            stock_price, exercise_price,
            days_to_expiry / 365.0,
            DEFAULT_RISK_FREE_RATE,
            option_style_for_iv,
        )
        if iv is not None:
            volatility = iv
            volatility_source = "implied_vol"
    if volatility is None:
        if hist_vol is not None:
            volatility = hist_vol
            volatility_source = f"realized_{VOL_LOOKBACK_DAYS}d"
        else:
            volatility = DEFAULT_VOLATILITY

    detail = CoveredWarrantDetail(
        symbol=payload.get("symbol", symbol),
        stock_name=payload.get("stockName"),
        base_stock_code=base_stock_code,
        base_stock_name=payload.get("baseStockName"),
        cw_stock_type=payload.get("cwStockType"),
        exercise_price=exercise_price,
        conversion_rate=payload.get("conversionRate"),
        trading_date=payload.get("tradingDate"),
        listing_date=payload.get("listingDate"),
        first_trading_date=payload.get("firstTradingDate"),
        last_trading_date=payload.get("lastTradingDate"),
        period=payload.get("period"),
        issuer_name=payload.get("issuerName"),
        last_price=payload.get("lastPrice"),
        close_price=payload.get("closePrice"),
        basic_price=payload.get("basicPrice"),
        offering_price=payload.get("offeringPrice"),
        total_vol=payload.get("totalVol"),
        total_val=payload.get("totalVal"),
        raw_base_stock_price=payload.get("baseStockPrice"),
        source_url=f"{DNSE_CW_DETAIL_URL}?symbol={symbol}",
    )

    assumptions = CoveredWarrantAssumptions(
        stock_price=stock_price,
        warrant_price=warrant_price,
        annual_volatility=volatility,
        hist_vol=hist_vol,
        risk_free_rate=DEFAULT_RISK_FREE_RATE,
        days_to_expiry=days_to_expiry,
        time_to_expiry_years=time_to_expiry_years,
        underlying_price_source=stock_price_source,
        warrant_price_source=warrant_price_source,
        volatility_source=volatility_source,
    )

    greeks, analysis = _compute_greeks(
        option_style=_infer_option_style(detail.cw_stock_type),
        stock_price=assumptions.stock_price,
        strike_price=detail.exercise_price,
        warrant_price=assumptions.warrant_price,
        conversion_rate=detail.conversion_rate,
        volatility=assumptions.annual_volatility,
        risk_free_rate=assumptions.risk_free_rate,
        time_to_expiry=assumptions.time_to_expiry_years,
    )

    return CoveredWarrantResponse(
        detail=detail,
        assumptions=assumptions,
        greeks=greeks,
        analysis=analysis,
    )
