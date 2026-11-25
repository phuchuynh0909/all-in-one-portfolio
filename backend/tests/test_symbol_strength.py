import asyncio
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.schemas.symbol_strength import SymbolStrengthRequest
from app.services import stock_service

# These service/route tests do not need the full application's auth fixture.
pytestmark = pytest.mark.real_auth


@pytest.fixture
def prices(monkeypatch, memory_cache):
    dates = pd.bdate_range(end="2026-11-13", periods=260)
    market = pd.Series(100 * 1.001 ** np.arange(len(dates)), index=dates)
    stocks = pd.concat([pd.DataFrame({
        "date": dates, "symbol": symbol, "close": 100 * factor ** np.arange(len(dates)),
    }) for symbol, factor in [("UP", 1.003), ("DOWN", 0.998), ("FLAT", 1.001)]], ignore_index=True)
    calls = []
    monkeypatch.setattr(stock_service, "_load_benchmark_closes", lambda *_: market)

    def load(symbols, window, tail, timeframe):
        calls.append((symbols, window, tail, timeframe))
        return stocks

    monkeypatch.setattr(stock_service, "_load_constituent_ohlcv", load)
    return dates, market, stocks, calls


def test_batch_uses_one_stock_read_and_returns_aligned_rs_and_rotation(prices):
    dates, _, _, calls = prices
    result = asyncio.run(stock_service.get_symbol_strength(["UP", "DOWN", "FLAT", "MISSING"], window=20, momentum_window=5, tail=8))
    assert len(calls) == 1
    assert calls[0] == (["DOWN", "FLAT", "MISSING", "UP"], 25, 8, "daily")
    assert result.dates == dates[-8:].strftime("%Y-%m-%d").tolist()
    rows = {row.symbol: row for row in result.rows}
    k = 1.003 / 1.001
    expected_ratio = 100 * 20 * k ** 20 / sum(k ** i for i in range(20))
    assert rows["UP"].ratio[-1] == pytest.approx(expected_ratio, abs=0.0001)
    assert rows["UP"].rs[-1] == pytest.approx(expected_ratio - 100, abs=0.0001)
    assert rows["UP"].momentum[-1] == pytest.approx(100, abs=0.0001)
    assert rows["DOWN"].ratio[-1] < 100
    assert rows["FLAT"].ratio == [100.0] * 8
    assert rows["FLAT"].momentum == [100.0] * 8
    assert rows["MISSING"].ratio == [None] * 8
    assert rows["MISSING"].unavailable
    assert all(len(row.rs) == len(row.ratio) == len(row.momentum) == 8 for row in result.rows)


def test_weekly_defaults_and_shared_rotation_calculation(prices):
    dates, market, stocks, _ = prices
    result = asyncio.run(stock_service.get_symbol_strength(["UP"], timeframe="weekly", tail=4))
    assert (result.window, result.momentum_window) == (10, 4)
    assert len(result.dates) == 4
    assert all(pd.Timestamp(date).dayofweek == 4 for date in result.dates)
    wide = stocks.pivot_table(index="date", columns="symbol", values="close")
    ratio = stock_service._relative_strength(wide, market, 10, "mansfield", "weekly") + 100
    momentum = stock_service._relative_strength(ratio, pd.Series(1.0, index=ratio.index), 4, "mansfield", "daily") + 100
    assert result.rows[0].ratio == pytest.approx(ratio["UP"].iloc[-4:].tolist(), abs=0.0001)
    assert result.rows[0].momentum == pytest.approx(momentum["UP"].iloc[-4:].tolist(), abs=0.0001)


def test_missing_bars_remain_null_and_stale_symbols_are_reported(prices):
    dates, _, stocks, _ = prices
    stocks.loc[(stocks.symbol == "UP") & (stocks.date == dates[-2]), "close"] = np.nan
    stocks.loc[(stocks.symbol == "DOWN") & (stocks.date >= dates[-5]), "close"] = np.nan
    result = asyncio.run(stock_service.get_symbol_strength(["UP", "DOWN"], window=20, tail=8))
    rows = {row.symbol: row for row in result.rows}
    assert rows["UP"].ratio[-2] is None
    assert rows["UP"].unavailable is None
    assert rows["DOWN"].unavailable


@pytest.mark.parametrize("timeframe", ["daily", "weekly"])
def test_empty_stock_batch_preserves_requested_symbols(prices, monkeypatch, timeframe):
    monkeypatch.setattr(stock_service, "_load_constituent_ohlcv", lambda *_: pd.DataFrame())
    result = asyncio.run(stock_service.get_symbol_strength(["MISSING"], tail=4, timeframe=timeframe))
    assert len(result.dates) == 4
    assert result.rows[0].symbol == "MISSING"
    assert result.rows[0].ratio == [None] * 4


def test_request_normalizes_deduplicates_and_rejects_invalid_input():
    request = SymbolStrengthRequest(symbols=[" hdb ", "HDB", "fpt"], benchmark=" vnindex ")
    assert request.symbols == ["HDB", "FPT"]
    assert request.benchmark == "VNINDEX"
    for values in [{"symbols": []}, {"symbols": ["HPG"] * 2001}, {"symbols": ["x'; DROP TABLE"]}, {"symbols": ["HPG"], "window": 0}, {"symbols": ["HPG"], "tail": 53}]:
        with pytest.raises(ValidationError):
            SymbolStrengthRequest(**values)


def test_route_contract_and_validation(prices, monkeypatch):
    # Load this router alone; app.api.v1's package imports unrelated backtest dependencies.
    path = Path(__file__).parents[1] / "app/api/v1/routes/timeseries.py"
    spec = importlib.util.spec_from_file_location("symbol_strength_routes", path)
    routes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(routes)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    with TestClient(app) as client:
        response = client.post("/api/v1/timeseries/symbols/relative-strength", json={"symbols": ["up", "FLAT"], "window": 20, "tail": 4})
        assert response.status_code == 200, response.text
        assert [row["symbol"] for row in response.json()["rows"]] == ["UP", "FLAT"]
        assert client.post("/api/v1/timeseries/symbols/relative-strength", json={"symbols": []}).status_code == 422
        monkeypatch.setattr(stock_service, "_load_benchmark_closes", lambda *_: pd.Series(dtype=float))
        response = client.post("/api/v1/timeseries/symbols/relative-strength", json={"symbols": ["UP"], "benchmark": "UNKNOWN"})
        assert response.status_code == 422
        assert "No price history for benchmark UNKNOWN" in response.json()["detail"]
