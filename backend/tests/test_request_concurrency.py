"""A slow market-data read must not hold up an unrelated layout save."""
import asyncio
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.api.v1.routes import chart_layouts, price_alerts, timeseries
from app.db.base import Base, get_db
from app.db.models.chart_layout import ChartLayout
from app.db.models.openscript import OpenScript
from app.db.models.user import User
from app.schemas.price_alert import PriceAlert
from app.services import stock_service


@pytest.fixture
def concurrent_app(tmp_path, memory_cache):
    engine = create_engine(f"sqlite:///{tmp_path}/concurrency.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[User.__table__, ChartLayout.__table__, OpenScript.__table__])

    def session():
        with Session(engine) as db:
            yield db

    app = FastAPI(dependencies=[Depends(require_user)])
    for router in (chart_layouts.router, timeseries.router, price_alerts.router):
        app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[require_user] = lambda: User(id=1, username="concurrency-test", is_active=True)
    try:
        yield app
    finally:
        engine.dispose()


@pytest.mark.parametrize("path,stage", [
    ("/timeseries/HPG", "load"),
    ("/timeseries/HPG/bars", "load"),
    ("/timeseries/HPG/indicators", "load"),
    ("/timeseries/HPG/bars", "compute"),
    ("/timeseries/sector/5", "sector"),
    ("/timeseries/sector/5/relative-strength", "sector"),
    ("/timeseries/sector/5/dominance", "sector"),
    ("/timeseries/sector/5/rotation", "sector"),
    ("/timeseries/sector/5/1/constituents", "members"),
    ("/timeseries/market/breadth", "load"),
    ("/price-alerts", "quote"),
])
def test_layout_save_completes_while_market_data_is_waiting(concurrent_app, monkeypatch, path, stage):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    reads = []

    def slow_read(value):
        reads.append(True)
        entered.set()
        release.wait(timeout=3.2)
        finished.set()
        return value

    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-11-06"]), "symbol": ["HPG"],
        "open": [20.0], "high": [22.0], "low": [19.0], "close": [21.0], "volume": [1000.0],
    })
    monkeypatch.setattr(stock_service, "_load_delta_stocks", lambda **_: slow_read(frame.copy()) if stage == "load" else frame.copy())
    monkeypatch.setattr(stock_service, "compute_stock_indicators", lambda *_: slow_read({}) if stage == "compute" else {})
    monkeypatch.setattr(stock_service, "_load_sector_closes", lambda *_: slow_read(pd.DataFrame()))
    monkeypatch.setattr(stock_service, "_sector_constituents", lambda *_: slow_read({}))
    client = SimpleNamespace(query=lambda _: slow_read(SimpleNamespace(result_rows=[[21.0]])), close=lambda: None)
    monkeypatch.setattr(stock_service, "_clickhouse_client", lambda: client)
    now = datetime.now()
    alert = PriceAlert(id=1, symbol="HPG", condition="gt", target_price=20, is_active=True,
                       is_triggered=False, created_at=now, updated_at=now)
    monkeypatch.setattr(price_alerts.price_alert_service, "get_alerts", lambda *_, **__: ([alert], 1))

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=concurrent_app), base_url="http://test") as client:
            start = time.perf_counter()
            body = {"indicators": [{"name": "rsi"}]} if path.endswith("/indicators") else {}
            request = client.get("/api/v1" + path) if stage == "quote" else client.post("/api/v1" + path, json=body)
            market_request = asyncio.create_task(request)
            try:
                assert await asyncio.to_thread(entered.wait, 2), "Market-data read never started"
                response = await client.put("/api/v1/chart-layouts/layout1", json={"name": "Layout 1"})
                elapsed = time.perf_counter() - start
                assert response.status_code == 200, response.text
                assert not finished.is_set(), f"Layout save waited for market data ({elapsed:.2f}s)"
                assert response.json()["id"] == "layout1"
                print(f"Layout save: {elapsed * 1000:.1f}ms while {path} ({stage}) is still waiting")
            finally:
                release.set()
                market_response = await market_request
            assert market_response.status_code == 200, market_response.text
            if stage == "quote":
                assert market_response.json()["alerts"][0]["current_price"] == "21.0"
                previous_reads = len(reads)
                cached = await client.get("/api/v1" + path)
                assert cached.json() == market_response.json()
                assert len(reads) == previous_reads
            else:
                previous_reads = len(reads)
                cached = await client.post("/api/v1" + path, json=body)
                assert cached.status_code == 200
                assert cached.json() == market_response.json()
                assert len(reads) == previous_reads, "Repeated market-data request missed the cache"

    asyncio.run(run())


def test_cached_market_data_still_requires_authentication(concurrent_app, monkeypatch):
    reads = []

    def load(**kwargs):
        reads.append(kwargs)
        return pd.DataFrame(columns=["date", "symbol"])

    def reject():
        raise HTTPException(status_code=401, detail="Not authenticated")

    monkeypatch.setattr(stock_service, "_load_delta_stocks", load)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=concurrent_app), base_url="http://test") as client:
            assert (await client.post("/api/v1/timeseries/HPG/bars", json={})).status_code == 200
            concurrent_app.dependency_overrides[require_user] = reject
            assert (await client.post("/api/v1/timeseries/HPG/bars", json={})).status_code == 401
            assert len(reads) == 1

    asyncio.run(run())
