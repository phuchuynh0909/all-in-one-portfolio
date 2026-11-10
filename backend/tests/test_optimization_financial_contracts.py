import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.schemas.portfolio import OptimizationRequest
from app.services import optimization_service
from app.services.financial_statements import get_financial_statements


@pytest.mark.parametrize('updates', [
    {'tickers': []}, {'tickers': ['  ']},
    {'constraints': {'min_weight': 0.8, 'max_weight': 0.2}},
    {'constraints': {'max_weight': 0.4}},
    {'constraints': {'max_weight': float('nan')}},
    {'constraints': {'other': 0.5}},
    {'method': 'hrp', 'constraints': {'max_weight': 0.6}},
])
def test_invalid_optimizer_inputs(updates):
    with pytest.raises(ValidationError):
        OptimizationRequest.model_validate({'tickers': ['AAA', 'BBB'], 'method': 'min_volatility', **updates})


def test_optimizer_normalizes_and_deduplicates_symbols():
    request = OptimizationRequest(tickers=[' aaa ', 'AAA', 'bbb'], method='min_volatility')
    assert request.tickers == ['AAA', 'BBB']


@pytest.mark.parametrize('method,extra', [
    ('min_volatility', {}), ('max_sharpe', {}), ('cvar', {}), ('cla', {}),
    ('max_quadratic_utility', {'risk_aversion': 1}),
    ('efficient_risk', {'target_risk': 0.5}),
    ('efficient_return', {'target_return': 0.01}),
    ('black_litterman', {'risk_aversion': 1}),
])
def test_optimizer_honors_bounds(monkeypatch, method, extra):
    rng = np.random.default_rng(42)
    dates = pd.bdate_range('2025-01-01', periods=200)
    prices = pd.DataFrame({
        'date': list(dates) * 2, 'symbol': ['AAA'] * 200 + ['BBB'] * 200,
        'close': np.concatenate([
            100 * np.exp(np.cumsum(rng.normal(0.002, 0.001, 200))),
            100 * np.exp(np.cumsum(rng.normal(0.002, 0.03, 200))),
        ]),
    })
    monkeypatch.setattr(optimization_service, '_load_delta_stocks', lambda **kwargs: prices)
    req = OptimizationRequest(tickers=['AAA', 'BBB'], method=method,
                              constraints={'min_weight': 0.4, 'max_weight': 0.6}, **extra)
    result = optimization_service.optimize_portfolio(None, req)
    assert sum(result.weights.values()) == pytest.approx(1, abs=1e-5)
    assert all(0.4 - 1e-5 <= weight <= 0.6 + 1e-5 for weight in result.weights.values())


@pytest.fixture
def financial_db():
    engine = create_engine('sqlite://')
    with Session(engine) as db:
        for ddl in [
            'CREATE TABLE company (company_id INTEGER, ticker TEXT, name TEXT)',
            'CREATE TABLE period (period_id INTEGER, label TEXT, end_date DATE, period_type TEXT)',
            'CREATE TABLE statement (statement_id INTEGER, statement_type TEXT, title TEXT)',
            'CREATE TABLE statement_item (item_id INTEGER, statement_id INTEGER, item_key TEXT, title_vi TEXT, level INTEGER, parent_item_id INTEGER, display_order INTEGER)',
            'CREATE TABLE item_value (company_id INTEGER, item_id INTEGER, period_id INTEGER, value NUMERIC)',
            "INSERT INTO company VALUES (1, 'AAA', 'Test')",
            "INSERT INTO period VALUES (1, 'Q1-2020', '2020-03-31', 'quarter'), (2, 'Q1-2026', '2026-03-31', 'quarter'), (3, 'Q2-2026', '2026-06-30', 'quarter')",
            "INSERT INTO statement VALUES (1, 'candoiketoan', 'Balance'), (2, 'baocaothunhap', 'Income')",
            "INSERT INTO statement_item VALUES (1, 1, 'cash', 'Cash', 1, NULL, 1), (2, 2, 'revenue', 'Revenue', 1, NULL, 1)",
            'INSERT INTO item_value VALUES (1,1,1,10), (1,1,2,20), (1,2,3,30)',
        ]:
            db.execute(text(ddl))
        yield db
    engine.dispose()


@pytest.mark.parametrize('requested, expected', [(['Q1-2020'], 'Q1-2020'), (None, 'Q1-2026')])
def test_financial_headers_and_values_share_filtered_periods(financial_db, requested, expected):
    result = get_financial_statements('AAA', ['candoiketoan'], requested, 1, 5, financial_db)
    assert [p.label for p in result.periods] == [expected]
    assert set(result.statements[0].items[0].values) == {expected}


@pytest.mark.parametrize('query', ['max_periods=0', 'max_periods=101', 'max_level=-1'])
def test_financial_limits_rejected_before_query(query):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1.routes.financial_statements import router
    from app.db.base import get_db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: None
    response = TestClient(app).get(f'/financial/companies/AAA/statements?{query}')
    assert response.status_code == 422


@pytest.mark.parametrize('missing_history', ['absent', 'null'])
def test_missing_requested_history_is_rejected(monkeypatch, missing_history):
    prices = pd.DataFrame({'date': pd.bdate_range('2025-01-01', periods=10),
                           'symbol': ['AAA'] * 10, 'close': np.arange(100, 110)})
    if missing_history == 'null':
        prices = pd.concat([prices, prices.assign(symbol='BBB', close=np.nan)])
    monkeypatch.setattr(optimization_service, '_load_delta_stocks', lambda **kwargs: prices)
    req = OptimizationRequest(tickers=['AAA', 'BBB'], method='min_volatility')
    with pytest.raises(ValueError, match='No price history.*BBB'):
        optimization_service.optimize_portfolio(None, req)


@pytest.mark.parametrize('confidences', [None, {'BBB': 0.001}, {'BBB': 0.0}])
def test_black_litterman_applies_views_with_optional_uncertainty(confidences):
    from pypfopt import EfficientFrontier, black_litterman

    cov = pd.DataFrame([[0.04, 0.005], [0.005, 0.09]],
                       index=['AAA', 'BBB'], columns=['AAA', 'BBB'])
    mu = pd.Series([0.1, 0.1], index=cov.index)
    req = OptimizationRequest(tickers=['BBB', 'AAA'], method='black_litterman',
                              risk_aversion=2, market_caps={'BBB': 70, 'AAA': 30},
                              views={'BBB': 0.4}, view_confidences=confidences)
    weights, ret, vol, sharpe = optimization_service._optimize_black_litterman(mu, cov, req)
    prior = black_litterman.market_implied_prior_returns(pd.Series({'AAA': 0.3, 'BBB': 0.7}), 2, cov)
    model = black_litterman.BlackLittermanModel(
        cov, pi=prior, absolute_views={'BBB': 0.4},
        omega=np.diag([(confidences or {}).get('BBB', 0.1)]))
    expected = EfficientFrontier(model.bl_returns(), model.bl_cov())
    expected.max_sharpe()
    assert weights == pytest.approx(expected.clean_weights())
    no_views = req.model_copy(update={'views': None, 'view_confidences': None})
    baseline, *_ = optimization_service._optimize_black_litterman(mu, cov, no_views)
    assert weights['BBB'] > baseline['BBB']


@pytest.mark.parametrize('updates', [
    {'views': {'OTHER': 0.1}}, {'views': {'AAA': float('inf')}},
    {'views': {'aaa': 0.1, 'AAA': 0.2}},
    {'market_caps': {'AAA': 0, 'BBB': 0}},
    {'market_caps': {'AAA': -1}},
    {'views': {'AAA': 0.1}, 'view_confidences': {'AAA': -0.1}},
    {'view_confidences': {'AAA': 0.1}},
])
def test_invalid_black_litterman_maps_are_rejected(updates):
    with pytest.raises(ValidationError):
        OptimizationRequest(tickers=['AAA', 'BBB'], method='black_litterman', risk_aversion=1, **updates)


def test_black_litterman_map_symbols_are_normalized():
    req = OptimizationRequest(tickers=['AAA'], method='black_litterman', views={' aaa ': 0.1})
    assert req.views == {'AAA': 0.1}
