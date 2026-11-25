import asyncio
import pandas as pd
from app.schemas.timeseries import MarketBreadthResponse
from app.services import stock_service


def test_directional_volume_excludes_unchanged_and_unrated_stocks(monkeypatch, memory_cache):
    dates = pd.bdate_range('2026-11-02', periods=5)
    rows = []
    for symbol, closes, volumes in (
        ('A', [10, 11, 11, 9, 10], [100, 200, 300, 400, None]),
        ('B', [20, 19, 19, 20, 21], [100, 500, 600, 700, 800]),
    ):
        rows.extend({'symbol': symbol, 'date': date, 'close': close,
                     'high': close + 1, 'low': close - 1, 'volume': volume}
                    for date, close, volume in zip(dates, closes, volumes))
    rows.append({'symbol': 'NEW', 'date': dates[1], 'close': 50,
                 'high': 51, 'low': 49, 'volume': 10000})
    monkeypatch.setattr(stock_service, '_load_delta_stocks', lambda **_: pd.DataFrame(rows))
    result = asyncio.run(stock_service.get_market_indicators())
    assert result['advancing_volume'] == [0, 200, 0, 700, None]
    assert result['declining_volume'] == [0, 500, 0, 400, None]
    assert result['advances'][-1] == 2
    MarketBreadthResponse(**result)
