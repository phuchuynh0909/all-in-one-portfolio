from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator
from enum import Enum
import math


class PositionBase(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    quantity: Decimal = Field(..., gt=0)
    purchase_price: Decimal = Field(..., gt=0)
    purchase_date: date
    notes: Optional[str] = None


class PositionCreate(PositionBase):
    quantity: Decimal = Field(..., gt=0, max_digits=15, decimal_places=6)
    purchase_price: Decimal = Field(..., gt=0, max_digits=15, decimal_places=6)


class PositionUpdate(PositionBase):
    original: PositionBase
    quantity: Decimal = Field(..., gt=0, max_digits=15, decimal_places=6)
    purchase_price: Decimal = Field(..., gt=0, max_digits=15, decimal_places=6)


class Position(PositionBase):
    id: int
    current_price: Optional[Decimal] = None  # Computed field, not stored in DB
    current_price_date: Optional[date] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TransactionBase(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    # dividend rows share this ledger; the ENUM migration is inert without them
    transaction_type: str = Field(..., pattern="^(buy|sell|dividend_cash|dividend_stock)$")
    quantity: Decimal = Field(..., gt=0)
    # ge=0 only because a stock-dividend row books shares at zero cost. Buys and
    # sells keep their old ``gt=0`` guarantee via the validator below.
    price: Decimal = Field(..., ge=0)
    close_price: Optional[Decimal] = None
    transaction_date: date
    fees: Optional[Decimal] = Field(default=0, ge=0)
    notes: Optional[str] = None

    # Types that must still have a real price. Relaxing the field to ge=0 for
    # dividend_stock's sake dropped this guarantee for every type at once; a
    # zero-price buy or sell is a data-entry error that would compute a 100%
    # gain on the whole position.
    _PRICED_TYPES = ("buy", "sell")

    @model_validator(mode="after")
    def _require_a_price_on_trades(self):
        if self.transaction_type in self._PRICED_TYPES and self.price <= 0:
            raise ValueError(
                f"price must be greater than 0 for a {self.transaction_type} "
                "transaction"
            )
        return self


class TransactionCreate(TransactionBase):
    quantity: Decimal = Field(..., gt=0, max_digits=15, decimal_places=6)
    price: Decimal = Field(..., ge=0, max_digits=15, decimal_places=6)
    close_price: Optional[Decimal] = Field(default=None, gt=0, max_digits=15, decimal_places=6)
    fees: Optional[Decimal] = Field(default=0, ge=0, max_digits=10, decimal_places=2)


class Transaction(TransactionBase):
    id: int
    is_position_close: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class InvestmentAmountBase(BaseModel):
    amount: Decimal = Field(..., gt=0)
    date: date
    notes: Optional[str] = None


class InvestmentAmountCreate(InvestmentAmountBase):
    amount: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)


class InvestmentAmount(InvestmentAmountBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class PortfolioSummary(BaseModel):
    total_value: Optional[Decimal]
    total_invested: Decimal
    total_profit_loss: Optional[Decimal]
    total_profit_loss_pct: Optional[Decimal]
    total_realized_pl: Decimal
    # Gross is the factual record; the headline figure is net of withholding.
    total_dividend_income_gross: Decimal = Decimal(0)
    total_dividend_income: Decimal = Decimal(0)
    positions: List[Position]

    class Config:
        from_attributes = True


class OptimizationMethod(str, Enum):
    """Supported optimization methods."""
    HRP = "hrp"
    EFFICIENT_FRONTIER = "ef"  # Legacy: same as max_sharpe
    MAX_SHARPE = "max_sharpe"
    MIN_VOLATILITY = "min_volatility"
    MAX_QUADRATIC_UTILITY = "max_quadratic_utility"
    EFFICIENT_RISK = "efficient_risk"
    EFFICIENT_RETURN = "efficient_return"
    BLACK_LITTERMAN = "black_litterman"
    CVAR = "cvar"
    CLA = "cla"


class RiskModel(str, Enum):
    """Supported risk models for covariance matrix calculation."""
    SAMPLE_COV = "sample_cov"
    SEMICOVARIANCE = "semicovariance"
    EXP_COV = "exp_cov"
    LEDOIT_WOLF = "ledoit_wolf"
    LEDOIT_WOLF_CONSTANT_VARIANCE = "ledoit_wolf_constant_variance"
    LEDOIT_WOLF_SINGLE_FACTOR = "ledoit_wolf_single_factor"
    LEDOIT_WOLF_CONSTANT_CORRELATION = "ledoit_wolf_constant_correlation"
    ORACLE_APPROXIMATING = "oracle_approximating"


class ReturnPredictionMethod(str, Enum):
    """Supported methods for predicting expected returns."""
    HISTORICAL_MEAN = "historical_mean"
    BVAR = "bvar"


class OptimizationConstraints(BaseModel):
    model_config = {"extra": "forbid"}

    min_weight: float = Field(0.0, ge=0, le=1, allow_inf_nan=False)
    max_weight: float = Field(1.0, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.min_weight > self.max_weight:
            raise ValueError("min_weight must not exceed max_weight")
        return self


class OptimizationRequest(BaseModel):
    """Request body for portfolio optimization."""
    tickers: List[str] = Field(..., min_length=1)
    start_date: date | None = None
    end_date: date | None = None
    method: OptimizationMethod
    risk_model: RiskModel = RiskModel.SAMPLE_COV  # Default to sample covariance
    risk_free_rate: float | None = 0.0
    constraints: OptimizationConstraints | None = None
    
    # Additional parameters for specific optimization methods
    risk_aversion: float | None = None  # For max_quadratic_utility and black_litterman
    target_risk: float | None = None    # For efficient_risk
    target_return: float | None = None  # For efficient_return
    
    # Black-Litterman specific parameters
    market_caps: dict[str, float] | None = None  # Market cap weights for equilibrium portfolio
    views: dict[str, float] | None = None        # Investor views on expected returns
    view_confidences: dict[str, float] | None = None  # Confidence in views (lower = more confident)
    
    # Return prediction method
    return_prediction_method: ReturnPredictionMethod = ReturnPredictionMethod.HISTORICAL_MEAN
    bvar_forecast_periods: int | None = Field(21, ge=1, le=252)

    @model_validator(mode="after")
    def validate_universe(self):
        self.tickers = list(dict.fromkeys(ticker.strip().upper() for ticker in self.tickers))
        if any(not ticker for ticker in self.tickers):
            raise ValueError("tickers must not contain blank symbols")
        for name in ("market_caps", "views", "view_confidences"):
            values = getattr(self, name)
            if values is None:
                continue
            normalized = {ticker.strip().upper(): value for ticker, value in values.items()}
            if len(normalized) != len(values):
                raise ValueError(f"{name} contains duplicate normalized tickers")
            if set(normalized) - set(self.tickers):
                raise ValueError(f"{name} contains tickers outside the requested universe")
            if any(not math.isfinite(value) for value in normalized.values()):
                raise ValueError(f"{name} must contain finite values")
            if name != "views" and any(value < 0 for value in normalized.values()):
                raise ValueError(f"{name} must not contain negative values")
            setattr(self, name, normalized)
        if self.market_caps and sum(self.market_caps.values()) <= 0:
            raise ValueError("market_caps must have a positive total")
        if self.view_confidences and set(self.view_confidences) - set(self.views or {}):
            raise ValueError("view_confidences must refer to supplied views")
        if self.constraints:
            if self.method == OptimizationMethod.HRP:
                raise ValueError("hrp does not support weight constraints")
            low, high = self.weight_bounds
            if len(self.tickers) * low > 1 + 1e-9 or len(self.tickers) * high < 1 - 1e-9:
                raise ValueError("weight constraints cannot produce a fully invested portfolio")
        return self

    @property
    def weight_bounds(self) -> tuple[float, float]:
        if self.constraints is None:
            return (0.0, 1.0)
        return (self.constraints.min_weight, self.constraints.max_weight)


class OptimizationResult(BaseModel):
    method: OptimizationMethod
    weights: dict[str, float]
    expected_return: float | None = None
    volatility: float | None = None
    sharpe_ratio: float | None = None


class ClosePositionRequest(BaseModel):
    """Request body for closing a position."""
    position_id: int = Field(..., gt=0)
    quantity_to_close: Decimal = Field(..., gt=0, max_digits=15, decimal_places=6)
    closing_price: Decimal = Field(..., gt=0, max_digits=15, decimal_places=6)
    closing_date: date
    fees: Optional[Decimal] = Field(default=0, ge=0, max_digits=10, decimal_places=2)
    notes: Optional[str] = None


class ClosePositionResponse(BaseModel):
    """Response for closing a position operation."""
    success: bool
    message: str
    position_updated: bool  # True if position was updated, False if deleted
    remaining_quantity: Optional[Decimal] = None
    transaction_id: int
    realized_pl: Decimal
    realized_pl_pct: Decimal

