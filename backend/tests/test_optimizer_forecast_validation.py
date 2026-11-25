import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sklearn.preprocessing import StandardScaler

from app.api.v1.routes.portfolio import router
from app.db.base import get_db
from app.services import optimization_service


def test_bvar_uses_same_multivariate_lag_order_as_training():
    data = pd.DataFrame({'AAA': [1., 2., 3.], 'BBB': [10., 20., 30.]},
                        index=pd.date_range('2025-01-01', periods=3))
    model = optimization_service.BayesianVAR(lags=2)
    model.fitted_ = True
    model.variables_ = list(data.columns)
    model.scaler_ = StandardScaler(with_mean=False, with_std=False).fit(data)
    # AAA continues its latest value; BBB continues its second lag.
    model.coefficients_ = np.array([[1., 0., 0., 0., 0.], [0., 0., 0., 1., 0.]])
    features, _ = model._create_lag_matrix(data)
    np.testing.assert_array_equal(features[0], [2., 20., 1., 10., 1.])
    prediction = model.predict(data, periods=3)
    np.testing.assert_array_equal(prediction.to_numpy(), [[3., 20.], [3., 30.], [3., 20.]])


@pytest.fixture
def optimizer_client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize('periods', [-1, 0, 253, 1000000000])
def test_invalid_forecast_length_rejected_before_optimization(optimizer_client, monkeypatch, periods):
    def unexpected(*args):
        pytest.fail('invalid horizon reached optimization')
    monkeypatch.setattr(optimization_service, 'optimize_portfolio', unexpected)
    response = optimizer_client.post('/portfolio/optimize', json={
        'tickers': ['AAA'], 'method': 'min_volatility',
        'return_prediction_method': 'bvar', 'bvar_forecast_periods': periods,
    })
    assert response.status_code == 422


@pytest.mark.parametrize('periods', [1, 252, None])
def test_valid_forecast_lengths_remain_accepted(periods):
    from app.schemas.portfolio import OptimizationRequest
    request = OptimizationRequest(tickers=['AAA'], method='min_volatility', bvar_forecast_periods=periods)
    assert request.bvar_forecast_periods == periods


def test_infeasible_risk_with_weight_limits_is_400(optimizer_client, monkeypatch):
    mu = pd.Series([0.1, 0.2], index=['AAA', 'BBB'])
    cov = pd.DataFrame(np.diag([0.0001, 0.04]), index=mu.index, columns=mu.index)
    def optimize(db, req):
        return optimization_service._optimize_efficient_risk(mu, cov, req)
    monkeypatch.setattr(optimization_service, 'optimize_portfolio', optimize)
    response = optimizer_client.post('/portfolio/optimize', json={
        'tickers': ['AAA', 'BBB'], 'method': 'efficient_risk',
        'target_risk': 0.05, 'constraints': {'max_weight': 0.6},
    })
    assert response.status_code == 400
    assert 'relaxing' in response.json()['detail']
    assert 'Solver status' not in response.json()['detail']
