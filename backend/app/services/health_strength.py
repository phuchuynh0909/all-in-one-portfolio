"""Financial-quality peer scores and market-wide, liquidity-filtered price strength."""
from datetime import timedelta
from math import isfinite

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.financial import Company, ItemValue, Period, Statement, StatementItem
from app.db.models.market import StockSymbol
from app.services.stock_service import _load_benchmark_closes, _load_constituent_ohlcv, _sector_constituents

KEYS = {
    "assets": ("tongcongtaisan",),
    "equity": ("vonchusohuu_tong", "vonchusohuu"),
    "profit": ("loinhuansauthuethunhapdoanhnghiep",),
    "revenue": ("doanhthuthuanvebanhangvacungcapdichvu",),
}


def financial_quality(records, as_of):
    """Equal-weight peer percentiles; at least three complete peers per period."""
    periods = {}
    for ticker, period_type, end, key, value in records:
        if end is None or end > as_of or end < as_of - timedelta(days=550) or not isfinite(value):
            continue
        periods.setdefault((ticker, period_type, end), {})[key] = float(value)
    latest = {}
    for (ticker, kind, end), values in periods.items():
        if ticker not in latest or (end, kind == "quarter") > (latest[ticker][1], latest[ticker][0] == "quarter"):
            latest[ticker] = (kind, end, values)
    metrics = []
    for ticker, (kind, end, values) in latest.items():
        current = {name: next((values[key] for key in keys if key in values), None) for name, keys in KEYS.items()}
        previous = periods.get((ticker, kind, (pd.Timestamp(end) - pd.DateOffset(years=1)).date()), {})
        revenue_before = next((previous[key] for key in KEYS["revenue"] if key in previous), None)
        if any(current[name] is None for name in KEYS) or not revenue_before or revenue_before <= 0 or current["assets"] <= 0 or current["equity"] <= 0:
            continue
        metrics.append({"symbol": ticker, "period": end.isoformat(), "kind": kind,
                        "profitability": current["profit"] / current["assets"],
                        "capital": current["equity"] / current["assets"],
                        "growth": current["revenue"] / revenue_before - 1})
    if not metrics:
        return {}
    frame = pd.DataFrame(metrics).set_index("symbol")
    output = {}
    for _, cohort in frame.groupby(["period", "kind"]):
        if len(cohort) < 3:
            continue
        scores = cohort[["profitability", "capital", "growth"]].rank(pct=True).mul(100)
        for symbol in cohort.index:
            output[symbol] = {"score": round(float(scores.loc[symbol].mean()), 1),
                              "period": cohort.loc[symbol, "period"], "peers": len(cohort),
                              "components": {key: round(float(scores.loc[symbol, key]), 1) for key in scores.columns}}
    return output


def price_strength(stocks, benchmark):
    """21/63/126-session excess returns; rank the 40/35/25 weighted composite."""
    if stocks.empty or benchmark.empty:
        return pd.DataFrame(), pd.DataFrame(), {}
    stocks = stocks.copy()
    stocks["date"] = pd.to_datetime(stocks["date"]).dt.normalize()
    dates = benchmark.index[benchmark.index >= stocks["date"].min()]
    closes = stocks.pivot_table(index="date", columns="symbol", values="close").reindex(dates)
    closes = closes.replace([float("inf"), float("-inf")], float("nan")).where(closes > 0)
    market = benchmark.reindex(dates).replace([float("inf"), float("-inf")], float("nan")).where(lambda values: values > 0)
    volumes = stocks.pivot_table(index="date", columns="symbol", values="volume").reindex(index=dates, columns=closes.columns)
    volumes = volumes.replace([float("inf"), float("-inf")], float("nan")).where(volumes >= 0)
    # Stored closes are thousands of VND; volume is shares.
    turnover = (closes * volumes * 1000).rolling(20, min_periods=20).mean()
    excess = {window: ((closes / closes.shift(window) - 1).sub(market / market.shift(window) - 1, axis=0) * 100)
              for window in [21, 63, 126]}
    weighted = excess[21] * 0.40 + excess[63] * 0.35 + excess[126] * 0.25
    ranks = weighted.where(turnover >= 5_000_000_000).rank(axis=1, pct=True).mul(100).clip(1, 99).round()
    return ranks, turnover, excess


def get_health_strength(level: int, sector_id: int, db: Session):
    mapping = _sector_constituents(level, db)
    symbols = mapping.get(sector_id, [])
    if not symbols:
        return {"as_of": None, "universe": 0, "rows": []}
    benchmark = _load_benchmark_closes()
    if benchmark.empty:
        raise ValueError("VNINDEX price history is unavailable")
    as_of = benchmark.index[-1].date()
    metadata = {stock.symbol: stock for stock in db.query(StockSymbol).all()}
    universe = sorted(set(metadata) | set(symbols))
    stocks = _load_constituent_ohlcv(universe, 126, 26, "daily")
    ranks, turnover, excess = price_strength(stocks, benchmark)
    records = db.execute(select(Company.ticker, Period.period_type, Period.end_date, StatementItem.item_key, ItemValue.value)
        .select_from(Company).join(ItemValue, ItemValue.company_id == Company.company_id)
        .join(Period, Period.period_id == ItemValue.period_id)
        .join(StatementItem, StatementItem.item_id == ItemValue.item_id)
        .join(Statement, Statement.statement_id == StatementItem.statement_id)
        .where(Company.ticker.in_(symbols), Period.period_type.in_(["quarter", "year"]),
               Period.end_date <= as_of, Period.end_date >= as_of - timedelta(days=550),
               ((Statement.statement_type == "candoiketoan") & StatementItem.item_key.in_(KEYS["assets"] + KEYS["equity"])) |
               ((Statement.statement_type == "baocaothunhap") & StatementItem.item_key.in_(KEYS["profit"] + KEYS["revenue"])))).all()
    health = financial_quality(records, as_of)

    def value(frame, symbol, offset=1):
        if symbol not in frame.columns or len(frame) < offset:
            return None
        number = frame[symbol].iloc[-offset]
        return round(float(number), 2) if pd.notna(number) and isfinite(number) else None

    rows = []
    for symbol in symbols:
        quality = health.get(symbol)
        strength = value(ranks, symbol)
        reasons = []
        if quality is None:
            reasons.append("Missing comparable financial statements, non-positive equity, or fewer than 3 complete reporting peers")
        if strength is None:
            reasons.append("Insufficient price history or 20-session turnover below VND 5 billion")
        rows.append({"symbol": symbol, "name": getattr(metadata.get(symbol), "name", None),
                     "health": quality["score"] if quality else None, "financial_period": quality["period"] if quality else None,
                     "health_peers": quality["peers"] if quality else 0, "components": quality["components"] if quality else {},
                     "strength": strength, "previous_strength": value(ranks, symbol, 6),
                     "turnover_vnd": value(turnover, symbol),
                     "excess_returns": {str(window): value(frame, symbol) for window, frame in excess.items()},
                     "unavailable": "; ".join(reasons) or None})
    return {"as_of": as_of.isoformat(), "universe": int(ranks.iloc[-1].notna().sum()) if not ranks.empty else 0, "rows": rows}
