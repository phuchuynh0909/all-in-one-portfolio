from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.financial import Company, ItemValue, Period, Statement, StatementItem
from app.db.models.market import StockSymbol
from app.services import health_strength as service
from app.schemas.health_strength import HealthStrength

pytestmark = pytest.mark.real_auth


def reports():
    rows = []
    for i, symbol in enumerate(["AAA", "BBB", "CCC"], 1):
        for end, revenue in [(date(2025, 9, 30), 100), (date(2026, 9, 30), 100 + 10 * i)]:
            values = {"tongcongtaisan": 100, "vonchusohuu_tong": 20 * i,
                      "loinhuansauthuethunhapdoanhnghiep": i, "doanhthuthuanvebanhangvacungcapdichvu": revenue}
            rows.extend((symbol, "quarter", end, key, value) for key, value in values.items())
    return rows


def test_quality_uses_three_complete_same_period_peers_without_fabricating_missing_scores():
    result = service.financial_quality(reports(), date(2026, 11, 19))
    assert result["AAA"]["score"] == 33.3
    assert result["BBB"]["score"] == 66.7
    assert result["CCC"]["score"] == 100
    assert result["CCC"]["peers"] == 3
    assert service.financial_quality(reports()[:16], date(2026, 11, 19)) == {}
    assert service.financial_quality(reports(), date(2028, 11, 19)) == {}


def test_quality_excludes_non_positive_equity_and_does_not_use_future_reports():
    rows = [(s, kind, end, key, (-1 if s == "AAA" and key == "vonchusohuu_tong" else value)) for s, kind, end, key, value in reports()]
    assert service.financial_quality(rows, date(2026, 11, 19)) == {}
    assert service.financial_quality(reports(), date(2026, 6, 30)) == {}


def prices():
    dates = pd.bdate_range(end="2026-11-19", periods=180)
    market = pd.Series(100 * 1.001 ** np.arange(180), index=dates)
    stocks = pd.concat([pd.DataFrame({"date": dates, "symbol": symbol, "close": 20 * factor ** np.arange(180), "volume": volume})
                        for symbol, factor, volume in [("AAA", 1.002, 1_000_000), ("BBB", 1.003, 1_000_000), ("CCC", 1.004, 1_000_000), ("LOW", 1.005, 1)]])
    return stocks, market


def test_price_rank_uses_weighted_returns_and_vnd_liquidity_units():
    stocks, market = prices()
    ranks, turnover, excess = service.price_strength(stocks, market)
    assert ranks.iloc[-1]["AAA"] == 33
    assert ranks.iloc[-1]["BBB"] == 67
    assert ranks.iloc[-1]["CCC"] == 99
    assert pd.isna(ranks.iloc[-1]["LOW"])
    assert turnover.iloc[-1]["AAA"] > 5_000_000_000
    expected = ((1.002 ** 63 - 1) - (1.001 ** 63 - 1)) * 100
    assert excess[63].iloc[-1]["AAA"] == pytest.approx(expected)
    assert len(ranks) == 180
    assert ranks.iloc[-6]["AAA"] == 33
    assert ranks.iloc[:126].isna().all().all()


def test_short_term_weight_can_outrank_a_larger_six_month_return():
    dates = pd.bdate_range(end="2026-11-19", periods=180)
    market = pd.Series(100.0, index=dates)
    series = {"RECENT": np.r_[np.full(159, 100.0), np.full(21, 150.0)],
              "OLD": np.r_[np.full(70, 100.0), np.full(110, 200.0)], "FLAT": np.full(180, 100.0)}
    stocks = pd.concat([pd.DataFrame({"date": dates, "symbol": symbol, "close": values, "volume": 1_000_000}) for symbol, values in series.items()])
    ranks, _, excess = service.price_strength(stocks, market)
    assert excess[126].iloc[-1]["OLD"] == 100
    assert excess[126].iloc[-1]["RECENT"] == 50
    assert ranks.iloc[-1]["RECENT"] == 99
    assert ranks.iloc[-1]["OLD"] == 67


def test_sector_endpoint_data_joins_real_statements_in_a_batch(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    tables = [model.__table__ for model in [Company, Period, Statement, StatementItem, ItemValue, StockSymbol]]
    Base.metadata.create_all(engine, tables=tables)
    stocks, market = prices()
    outside = stocks[stocks.symbol == "CCC"].copy()
    outside["symbol"] = "OUTSIDE"
    outside["close"] = 20 * 1.005 ** np.arange(len(outside))
    stocks = pd.concat([stocks, outside])
    monkeypatch.setattr(service, "_load_benchmark_closes", lambda: market)
    monkeypatch.setattr(service, "_load_constituent_ohlcv", lambda *_: stocks)
    monkeypatch.setattr(service, "_sector_constituents", lambda *_: {1: ["AAA", "BBB", "CCC", "LOW"]})
    with Session(engine) as db:
        statements = {kind: Statement(statement_type=kind) for kind in ["baocaothunhap", "candoiketoan"]}
        db.add_all(statements.values())
        db.flush()
        items = {key: StatementItem(statement_id=statements["candoiketoan" if key in service.KEYS["assets"] + service.KEYS["equity"] else "baocaothunhap"].statement_id, item_key=key, title_vi=key, level=1) for key in {record[3] for record in reports()}}
        periods = {end: Period(label=str(end), period_type="quarter", end_date=end) for end in {record[2] for record in reports()}}
        companies = {symbol: Company(ticker=symbol, name=symbol) for symbol in ["AAA", "BBB", "CCC"]}
        db.add_all([*items.values(), *periods.values(), *companies.values()])
        db.add_all([StockSymbol(symbol=symbol, name=symbol) for symbol in ["AAA", "BBB", "CCC", "LOW", "OUTSIDE"]])
        db.flush()
        for symbol, _, end, key, value in reports():
            db.add(ItemValue(company_id=companies[symbol].company_id, period_id=periods[end].period_id, item_id=items[key].item_id, value=value))
        db.commit()
        result = service.get_health_strength(3, 1, db)
        assert len(HealthStrength.model_validate(result).rows) == 4
        assert result["universe"] == 4
        assert result["as_of"] == "2026-11-19"
        rows = {row["symbol"]: row for row in result["rows"]}
        assert rows["BBB"]["health"] == 66.7
        assert rows["BBB"]["strength"] == 50
        assert rows["BBB"]["previous_strength"] == 50
        assert rows["LOW"]["health"] is None
        assert rows["LOW"]["unavailable"]
    engine.dispose()
