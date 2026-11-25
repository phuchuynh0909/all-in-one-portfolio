import asyncio

import numpy as np
import pandas as pd
from app.schemas.timeseries import MarketBreadthRequest, MarketBreadthResponse

from app.services import stock_service


def test_rsi_distribution_uses_inclusive_middle_bucket() -> None:
    buckets = stock_service.classify_rsi_values(
        pd.Series([29.99, 30.0, 50.0, 70.0, 70.01, np.nan])
    )

    assert buckets["rsi_below_30"].tolist() == [1, 0, 0, 0, 0, 0]
    assert buckets["rsi_between_30_70"].tolist() == [0, 1, 1, 1, 0, 0]
    assert buckets["rsi_above_70"].tolist() == [0, 0, 0, 0, 1, 0]


def test_market_breadth_rsi_period_defaults_to_21() -> None:
    assert MarketBreadthRequest().rsi_period == 21


def test_market_breadth_includes_historical_rsi_distribution(monkeypatch, memory_cache) -> None:
    dates = pd.date_range("2026-08-17", periods=20, freq="B")
    rows = [
        *[
            {"date": day, "symbol": "DOWN", "close": 100.0 - index}
            for index, day in enumerate(dates)
        ],
        *[
            {"date": day, "symbol": "UP", "close": 100.0 + index}
            for index, day in enumerate(dates)
        ],
        *[
            {"date": day, "symbol": "MID", "close": 50.0 + (index % 2)}
            for index, day in enumerate(dates)
        ],
    ]
    monkeypatch.setattr(
        stock_service,
        "_load_delta_stocks",
        lambda **_: pd.DataFrame(rows).assign(
            volume=1000.0,
            high=lambda frame: frame["close"] + 0.5,
            low=lambda frame: frame["close"] - 0.5,
        ),
    )

    result = asyncio.run(
        stock_service.get_market_indicators(
            start_date=dates[0].strftime("%Y-%m-%d"),
            end_date=dates[-1].strftime("%Y-%m-%d"),
            rsi_period=14,
        )
    )

    assert result["rsi_period"] == 14
    assert result["timestamps"] == dates.strftime("%Y-%m-%d").tolist()
    assert result["rsi_below_30"][-1] == 1
    assert result["rsi_between_30_70"][-1] == 1
    assert result["rsi_above_70"][-1] == 1
    assert result["rsi_below_30"][13] == 0
    assert result["rsi_above_70"][13] == 0
    assert result["net_new_highs"] == [None] * len(dates)
    MarketBreadthResponse(**result)


def test_net_new_highs_uses_full_per_symbol_history_before_date_filter(monkeypatch, memory_cache):
    dates = pd.bdate_range("2025-01-01", periods=260)
    rows = []
    for symbol in ("UP1", "UP2", "DOWN", "FLAT"):
        for i, day in enumerate(dates):
            close = 400.0 - i if symbol == "DOWN" else 100.0 + i
            if symbol == "FLAT":
                close = 200.0
            if symbol == "UP2" and i == len(dates) - 1:
                close = 1.0
            rows.append({"date": day, "symbol": symbol, "close": close,
                         "high": close + 0.5, "low": close - 0.5})
    rows.append({"date": dates[-1], "symbol": "NEW_LISTING", "close": 9999.0,
                 "high": 10000.0, "low": 9998.0})
    calls = []

    def load(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame(rows).assign(volume=1000.0).sample(frac=1, random_state=42)

    monkeypatch.setattr(stock_service, "_load_delta_stocks", load)
    all_dates = asyncio.run(stock_service.get_market_indicators())
    assert all_dates["net_new_highs"][:251] == [None] * 251
    # Equal-price touches count; a flat constituent appears on both sides.
    assert all_dates["new_highs"][251] == 3
    assert all_dates["new_lows"][251] == 2
    assert all_dates["net_new_highs"][251:-1] == [1] * 8
    assert all_dates["net_new_highs"][-1] == -1

    filtered = asyncio.run(stock_service.get_market_indicators(
        start_date=dates[-2].strftime("%Y-%m-%d"),
        end_date=dates[-1].strftime("%Y-%m-%d"),
    ))
    assert filtered["net_new_highs"] == [1, -1]
    assert calls[-1]["start"] <= dates[-2] - pd.Timedelta(days=365)
    for key in ("new_highs", "new_lows", "net_new_highs"):
        assert filtered[key] == all_dates[key][-2:]
    MarketBreadthResponse(**filtered)


def test_empty_breadth_keeps_high_low_arrays_aligned(monkeypatch, memory_cache):
    monkeypatch.setattr(stock_service, "_load_delta_stocks", lambda **_: pd.DataFrame())
    result = asyncio.run(stock_service.get_market_indicators())
    assert result["new_highs"] == result["new_lows"] == result["net_new_highs"] == []
    MarketBreadthResponse(**result)
