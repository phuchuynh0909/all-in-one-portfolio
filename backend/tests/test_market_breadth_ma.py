import asyncio

import pandas as pd
import pytest

from app.schemas.timeseries import MarketBreadthResponse
from app.services import stock_service


def test_ma_breadth_uses_eligible_stocks_strict_comparison_and_full_warmup(monkeypatch, memory_cache):
    dates = pd.bdate_range('2025-01-01', periods=210)
    rows = []
    for symbol in ('UP', 'DOWN', 'FLAT', 'NEW'):
        start = 170 if symbol == 'NEW' else 0
        for i in range(start, len(dates)):
            close = 500.0 - i if symbol == 'DOWN' else 100.0 + i
            if symbol == 'FLAT':
                close = 200.0
            if symbol == 'DOWN' and i == len(dates) - 1:
                close = 0.0
            rows.append({'symbol': symbol, 'date': dates[i], 'close': close,
                         'high': close + 0.5, 'low': close - 0.5})
    monkeypatch.setattr(stock_service, '_load_delta_stocks',
                        lambda **_: pd.DataFrame(rows).assign(volume=1000.0).sample(frac=1, random_state=1))
    result = asyncio.run(stock_service.get_market_indicators())
    for period in (20, 50, 200):
        values = result[f'pct_above_ma_{period}']
        assert values[:period - 1] == [None] * (period - 1)
        assert values[period - 1] == pytest.approx(100 / 3)
        assert all(value is None or 0 <= value <= 100 for value in values)
    # Flat closes equal their averages, so are eligible but never "above".
    # NEW has 40 bars: eligible for SMA20, excluded from SMA50 and SMA200.
    assert result['pct_above_ma_20'][-2] == 50
    assert result['pct_above_ma_50'][-2] == pytest.approx(100 / 3)
    assert result['pct_above_ma_200'][-2] == pytest.approx(100 / 3)
    # An invalid current close is excluded from both numerator and denominator.
    assert result['pct_above_ma_20'][-1] == pytest.approx(200 / 3)
    assert result['pct_above_ma_50'][-1] == 50
    assert result['pct_above_ma_200'][-1] == 50
    filtered = asyncio.run(stock_service.get_market_indicators(
        start_date=dates[-3].strftime('%Y-%m-%d'), end_date=dates[-1].strftime('%Y-%m-%d'),
    ))
    for period in (20, 50, 200):
        assert filtered[f'pct_above_ma_{period}'] == result[f'pct_above_ma_{period}'][-3:]
    MarketBreadthResponse(**filtered)


def test_empty_ma_breadth_is_aligned(monkeypatch, memory_cache):
    monkeypatch.setattr(stock_service, '_load_delta_stocks', lambda **_: pd.DataFrame())
    result = asyncio.run(stock_service.get_market_indicators())
    for period in (20, 50, 200):
        assert result[f'pct_above_ma_{period}'] == []
    MarketBreadthResponse(**result)
