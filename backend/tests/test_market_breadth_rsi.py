import asyncio

import numpy as np
import pandas as pd
from app.schemas.timeseries import MarketBreadthRequest

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


def test_market_breadth_includes_historical_rsi_distribution(monkeypatch) -> None:
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
        lambda **_: pd.DataFrame(rows),
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
