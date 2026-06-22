from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class CoveredWarrantDetail(BaseModel):
    symbol: str
    stock_name: Optional[str] = None
    base_stock_code: Optional[str] = None
    base_stock_name: Optional[str] = None
    cw_stock_type: Optional[str] = None
    exercise_price: Optional[float] = None
    conversion_rate: Optional[float] = None
    trading_date: Optional[datetime] = None
    listing_date: Optional[datetime] = None
    first_trading_date: Optional[datetime] = None
    last_trading_date: Optional[datetime] = None
    period: Optional[str] = None
    issuer_name: Optional[str] = None
    last_price: Optional[float] = None
    close_price: Optional[float] = None
    basic_price: Optional[float] = None
    offering_price: Optional[float] = None
    total_vol: Optional[float] = None
    total_val: Optional[float] = None
    raw_base_stock_price: Optional[float] = None
    source_url: Optional[str] = None


class CoveredWarrantAssumptions(BaseModel):
    stock_price: Optional[float] = None
    warrant_price: Optional[float] = None
    annual_volatility: Optional[float] = None
    hist_vol: Optional[float] = None
    risk_free_rate: float
    days_to_expiry: int
    time_to_expiry_years: float
    underlying_price_source: str
    warrant_price_source: str
    volatility_source: str


class CoveredWarrantGreeks(BaseModel):
    option_style: str
    theoretical_price: Optional[float] = None
    intrinsic_value: Optional[float] = None
    time_value: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta_per_day: Optional[float] = None
    vega_per_1pct_vol: Optional[float] = None
    rho_per_1pct_rate: Optional[float] = None
    d1: Optional[float] = None
    d2: Optional[float] = None


class CoveredWarrantAnalysis(BaseModel):
    moneyness_pct: Optional[float] = None
    break_even_stock_price: Optional[float] = None
    premium_to_break_even_pct: Optional[float] = None
    leverage: Optional[float] = None
    effective_gearing: Optional[float] = None
    theoretical_edge_pct: Optional[float] = None
    parity_price_ratio: Optional[float] = None
    in_the_money: Optional[bool] = None
    summary: str


class CoveredWarrantResponse(BaseModel):
    detail: CoveredWarrantDetail
    assumptions: CoveredWarrantAssumptions
    greeks: CoveredWarrantGreeks
    analysis: CoveredWarrantAnalysis
