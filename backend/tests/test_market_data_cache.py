import asyncio

import pandas as pd
import pytest
from fastapi.encoders import jsonable_encoder

from app.core.cache import market_data_key_builder
from app.services import stock_service


def test_market_keys_ignore_sessions_and_normalize_parameters():
    def query(symbol, params=None, db=None):
        pass

    first = market_data_key_builder(query, "market", args=("HPG", {"window": 20, "period": 5}, object()))
    same = market_data_key_builder(query, "market", kwargs={
        "db": object(), "params": {"period": 5, "window": 20}, "symbol": "HPG",
    })
    assert first == same
    assert first != market_data_key_builder(query, "market", args=("FPT", {"window": 20, "period": 5}))
    assert first != market_data_key_builder(query, "market", args=("HPG", {"window": 21, "period": 5}))
    assert first != market_data_key_builder(query, "other", args=("HPG", {"window": 20, "period": 5}))


def test_bars_cache_separates_symbols_pages_and_expires(monkeypatch, memory_cache):
    reads = []
    now = [1000]
    monkeypatch.setattr(type(memory_cache), "_now", property(lambda _: now[0]))

    def load(**kwargs):
        symbol = kwargs["symbols"][0]
        reads.append(symbol)
        return pd.DataFrame({
            "date": pd.to_datetime(["2026-11-05", "2026-11-06"]), "symbol": [symbol] * 2,
            "open": [20.0, 21.0], "high": [22.0, 23.0], "low": [19.0, 20.0],
            "close": [21.0, 22.0] if symbol == "HPG" else [31.0, 32.0], "volume": [1000.0, 2000.0],
        })

    monkeypatch.setattr(stock_service, "_load_delta_stocks", load)
    monkeypatch.setattr(stock_service, "compute_stock_indicators", lambda *_: {})

    async def run():
        first = await stock_service.get_stock_bars("HPG", count_back=1)
        cached = await stock_service.get_stock_bars(symbol="HPG", interval="1d", count_back=1)
        assert jsonable_encoder(first) == cached
        assert first.timeseries.close == [22.0]
        assert reads == ["HPG"]
        longer = await stock_service.get_stock_bars("HPG", count_back=2)
        other = await stock_service.get_stock_bars("FPT", count_back=1)
        assert longer.timeseries.close == [21.0, 22.0]
        assert other.timeseries.close == [32.0]
        assert reads == ["HPG", "HPG", "FPT"]
        now[0] += 299
        assert jsonable_encoder(await stock_service.get_stock_bars("HPG", count_back=1)) == jsonable_encoder(first)
        assert len(reads) == 3
        now[0] += 2
        assert jsonable_encoder(await stock_service.get_stock_bars("HPG", count_back=1)) == jsonable_encoder(first)
        assert len(reads) == 4

    asyncio.run(run())


def test_failed_market_reads_are_not_cached(monkeypatch, memory_cache):
    reads = []

    def fail(**kwargs):
        reads.append(kwargs)
        raise RuntimeError("Market data unavailable")

    monkeypatch.setattr(stock_service, "_load_delta_stocks", fail)

    async def run():
        for _ in range(2):
            with pytest.raises(RuntimeError, match="Market data unavailable"):
                await stock_service.get_stock_bars("HPG")
        assert len(reads) == 2

    asyncio.run(run())


@pytest.mark.parametrize("function,loader,args", [
    ("get_sector_timeseries", "_load_sector_closes", ("1",)),
    ("get_sector_relative_strength", "_load_sector_closes", ("1",)),
    ("get_sector_dominance", "_load_sector_closes", ("1",)),
    ("get_sector_rotation", "_load_sector_closes", ("1",)),
    ("get_sector_constituents", "_sector_constituents", ("1", 123)),
])
def test_sector_read_retries_after_failure(monkeypatch, memory_cache, function, loader, args):
    calls = []

    def load(*args):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary outage")
        return {} if loader == "_sector_constituents" else pd.DataFrame()

    monkeypatch.setattr(stock_service, loader, load)

    async def run():
        query = getattr(stock_service, function)
        with pytest.raises(RuntimeError, match="temporary outage"):
            await query(*args, db=object())
        await query(*args, db=object())
        await query(*args, db=object())
        assert len(calls) == 2

    asyncio.run(run())
