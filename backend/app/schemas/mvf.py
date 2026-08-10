"""Schemas for MVF — Mean-Variance with Forecasting (LSTM μ + shrunk historical Σ).

See app/services/mvf_lstm_service.py for the pipeline these describe.
"""
from typing import List

from pydantic import BaseModel, Field


class MvfRequest(BaseModel):
    """Configuration for one MVF run.

    Defaults mirror the notebook's production settings, so an empty-bodied
    request (beyond `tickers`) reproduces its Phase-4 allocation.
    """

    tickers: List[str] = Field(..., min_length=2, description="Asset universe")
    benchmark: str = Field("VNINDEX", description="Index supplying the market-vol feature")

    # LSTM
    seq_len: int = Field(60, ge=10, le=250, description="Look-back window (trading days)")
    horizon: int = Field(21, ge=1, le=120, description="Forecast / holding period in days")
    epochs: int = Field(40, ge=1, le=200)
    lr: float = Field(1e-3, gt=0, le=1.0)
    batch_size: int = Field(64, ge=8, le=512)
    force_retrain: bool = Field(False, description="Ignore the disk model cache")

    # Optimizer
    max_weight: float = Field(0.40, gt=0, le=1.0, description="Per-asset weight cap")
    cov_lookback: int = Field(252, ge=60, le=2000, description="Trailing bars for Σ")
    cov_shrink: bool = Field(True, description="Ledoit-Wolf shrink the covariance")
    risk_free_rate: float = Field(0.0, ge=0, le=1.0, description="Daily risk-free rate")

    # Order sheet
    # Share counts are capital / close, so capital is denominated in whatever units
    # the OHLC feed quotes `close` in — the same ones positions and the summary use.
    capital: float = Field(1_000_000_000, gt=0, description="Capital to deploy")
    years: int = Field(10, ge=2, le=30, description="Years of history to load")


class MvfHolding(BaseModel):
    ticker: str
    weight: float
    pred_ann_return: float          # LSTM-forecast annualized return
    ann_vol: float                  # annualized vol from the historical Σ diagonal
    last_price: float
    shares: int                     # floor(target_value / last_price)
    target_value: float             # weight × capital
    alloc_value: float              # shares × last_price


class MvfResult(BaseModel):
    as_of: str                      # last bar the forecast starts from, YYYY-MM-DD
    bars: int
    universe: List[str]             # tickers actually modelled
    dropped: List[str]              # requested but lacking enough clean history
    excluded: List[str]             # modelled but zero-weighted by the optimizer
    horizon: int
    max_weight: float
    predicted_return: float         # annualized, portfolio level
    predicted_volatility: float
    predicted_sharpe: float
    weight_sum: float
    capital: float
    deployed_value: float
    cash_residual: float
    holdings: List[MvfHolding]
