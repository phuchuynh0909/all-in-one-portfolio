"""Max-Sharpe has no solution when every asset underperforms cash.

pypfopt raises a bare ``ValueError`` in that case, which used to escape the
route as a 500. It is a legitimate input condition — a portfolio of holdings
that all fell over the window hits it — so it belongs in the 4xx range, with a
message saying which rate and which asset made it infeasible.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import optimization_service


@pytest.fixture
def losing_assets():
    """Four assets whose annualised expected returns are all negative."""
    tickers = ["NKG", "PAN", "VCG", "YEG"]
    mu = pd.Series([-0.131, -0.016, -0.076, -0.005], index=tickers)
    S = pd.DataFrame(np.diag([0.09, 0.04, 0.06, 0.16]), index=tickers, columns=tickers)
    return mu, S


def test_max_sharpe_rejects_a_universe_that_all_underperforms_cash(losing_assets):
    mu, S = losing_assets
    with pytest.raises(ValueError) as excinfo:
        optimization_service._optimize_max_sharpe(mu, S, risk_free_rate=0.0)

    message = str(excinfo.value)
    assert "YEG" in message, "names the best asset so the caller can judge the gap"
    assert "max_sharpe" in message or "Sharpe" in message
    assert "min_volatility" in message, "points at a method that does have a solution"


def test_max_sharpe_still_solves_when_one_asset_beats_the_rate(losing_assets):
    mu, S = losing_assets
    mu = mu.copy()
    mu["PAN"] = 0.12

    weights, ret, vol, sharpe = optimization_service._optimize_max_sharpe(mu, S, 0.0)

    assert pytest.approx(sum(weights.values()), abs=1e-6) == 1.0
    assert ret > 0


def test_route_translates_an_infeasible_universe_into_400(monkeypatch):
    """The route must not let a domain ValueError become a 500."""
    def boom(db, req):
        raise ValueError("No asset has an expected return above the risk-free rate")

    monkeypatch.setattr(optimization_service, "optimize_portfolio", boom)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/portfolio/optimize",
            json={"tickers": ["NKG", "YEG"], "method": "max_sharpe", "risk_free_rate": 0.0},
        )

    assert response.status_code == 400
    assert "risk-free rate" in response.json()["detail"]
