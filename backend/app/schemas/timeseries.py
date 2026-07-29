from typing import Dict, List, Optional, Union
from datetime import date
from pydantic import BaseModel, Field

class IndicatorParams(BaseModel):
    name: str
    params: Dict[str, Union[int, float, str]] = Field(default_factory=dict)

class MACD(BaseModel):
    macd: List[Optional[float]]
    signal: List[Optional[float]]
    histogram: List[Optional[float]]

class BollingerBands(BaseModel):
    upper: List[Optional[float]]
    middle: List[Optional[float]]
    lower: List[Optional[float]]

class WilliamsVixFix(BaseModel):
    wvf: List[Optional[float]]
    range_high: List[Optional[float]]
    filtered: List[bool]
    cond_fe: List[bool]

class SqueezeTTM(BaseModel):
    histogram: List[Optional[float]]
    squeeze_state: List[int]  # 0=diff==0/warmup, 1=diff<0 (on), 2=diff>0 (off)

class SmartMoneyFlow(BaseModel):
    last_signal: List[Optional[int]]
    switch_up: List[bool]
    switch_down: List[bool]
    upper: List[Optional[float]]
    lower: List[Optional[float]]
    b_close: List[Optional[float]]
    b_open: List[Optional[float]]
    mf_smooth: List[Optional[float]]
    strength: List[Optional[float]]
    bull_dot: List[bool]
    bear_dot: List[bool]
    strength_signed: List[Optional[float]]

class ChandelierExit(BaseModel):
    value: List[Optional[float]]
    direction: List[Optional[int]]
    long: List[Optional[float]]
    short: List[Optional[float]]

class LinRegChannel(BaseModel):
    reg: List[Optional[float]]
    pi_upper: List[Optional[float]]
    pi_lower: List[Optional[float]]
    ci_upper: List[Optional[float]]
    ci_lower: List[Optional[float]]

class GaussianFrama(BaseModel):
    frama: List[Optional[float]]
    long_v: List[Optional[float]]
    short_v: List[Optional[float]]
    qb: List[Optional[float]]

class HullButterfly(BaseModel):
    hso: List[Optional[float]]
    os: List[Optional[float]]

class Indicators(BaseModel):
    rsi: Optional[List[Optional[float]]] = None
    rsi_5: Optional[List[Optional[float]]] = None
    macd: Optional[MACD] = None
    sma: Optional[List[Optional[float]]] = None
    ema: Optional[List[Optional[float]]] = None
    bbands: Optional[BollingerBands] = None
    atr: Optional[List[Optional[float]]] = None
    atr_trailing: Optional[List[Optional[float]]] = None
    vwap_highest: Optional[List[Optional[float]]] = None
    vwap_lowest: Optional[List[Optional[float]]] = None
    kama: Optional[List[Optional[float]]] = None
    bvc: Optional[List[Optional[float]]] = None
    stoch: Optional[Dict[str, List[Optional[float]]]] = None
    kalman_zscore: Optional[List[Optional[float]]] = None
    yz_volatility: Optional[List[Optional[float]]] = None
    gkyz_volatility: Optional[List[Optional[float]]] = None
    rs_rating_20: Optional[List[Optional[float]]] = None
    rs_rating_50: Optional[List[Optional[float]]] = None
    rs_rating_252: Optional[List[Optional[float]]] = None
    rs_rating_20_ema: Optional[List[Optional[float]]] = None
    rs_rating_50_ema: Optional[List[Optional[float]]] = None
    rs_rating_252_ema: Optional[List[Optional[float]]] = None
    matrix_series: Optional[Dict[str, List[Optional[float]]]] = None
    williams_vix_fix: Optional[WilliamsVixFix] = None
    squeeze_ttm: Optional[SqueezeTTM] = None
    smart_money_flow: Optional[SmartMoneyFlow] = None
    chandelier_exit: Optional[ChandelierExit] = None
    linreg_channel: Optional[LinRegChannel] = None
    gaussian_frama: Optional[GaussianFrama] = None
    hull_butterfly: Optional[HullButterfly] = None

class Timeseries(BaseModel):
    open: List[float]
    high: List[float]
    low: List[float]
    close: List[float]
    volume: List[float]

class TimeseriesResponse(BaseModel):
    symbol: str
    interval: str = Field(default="1d", description="Data interval (e.g., 1d, 1h)")
    meta: Dict = Field(default_factory=dict)
    timestamps: List[str]
    timeseries: Timeseries
    indicators: Optional[Indicators] = None

class TimeseriesRequest(BaseModel):
    interval: str = Field(default="1d", description="Data interval (e.g., 1d, 1h)")
    indicators: List[IndicatorParams] = Field(default_factory=list)
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class BarsRequest(BaseModel):
    """
    Paged bars request, shaped after the TradingView datafeed ``getBars`` call.

    The window is defined by ``to`` (exclusive) plus ``count_back`` — the number
    of bars to return ending at ``to``. ``from`` is only used when
    ``count_back`` is omitted.
    """
    interval: str = Field(default="1d", description="Data interval (e.g., 1d, 1h)")
    indicators: List[IndicatorParams] = Field(default_factory=list)
    to: Optional[int] = Field(
        default=None,
        description="Exclusive upper bound of the window, unix seconds (UTC). Defaults to the latest bar.",
    )
    count_back: Optional[int] = Field(
        default=None,
        ge=1,
        le=20000,
        description="Number of bars to return, ending at `to`. Takes priority over `from`.",
    )
    from_ts: Optional[int] = Field(
        default=None,
        alias="from",
        description="Inclusive lower bound, unix seconds (UTC). Used only when `count_back` is omitted.",
    )

    model_config = {"populate_by_name": True}


class BarsResponse(BaseModel):
    """One page of bars (plus indicators aligned to that page)."""
    symbol: str
    interval: str = Field(default="1d", description="Data interval (e.g., 1d, 1h)")
    meta: Dict = Field(default_factory=dict)
    timestamps: List[str]
    timeseries: Timeseries
    indicators: Optional[Indicators] = None
    no_data: bool = Field(default=False, description="True when the requested window holds no bars")
    next_time: Optional[int] = Field(
        default=None,
        description="Unix seconds of the closest available bar when `no_data` is true (lets the chart skip gaps)",
    )
    has_more_history: bool = Field(
        default=False, description="True when older bars exist before this page"
    )


class IndicatorsOnlyResponse(BaseModel):
    """Response schema for indicators-only endpoint (no OHLCV data)."""
    symbol: str
    interval: str = Field(default="1d", description="Data interval (e.g., 1d, 1h)")
    timestamps: List[str]
    indicators: Indicators


class IndicatorsRequest(BaseModel):
    """Request schema for indicators-only endpoint."""
    indicators: List[IndicatorParams] = Field(..., min_length=1, description="List of indicators to calculate")
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class MarketBreadthResponse(BaseModel):
    """Response schema for market breadth indicators (A/D Line, McClellan)."""
    timestamps: List[str]
    ad_line: List[Optional[float]] = Field(description="Advance-Decline Line (cumulative)")
    mcclellan_oscillator: List[Optional[float]] = Field(description="McClellan Oscillator (19 EMA - 39 EMA)")
    mcclellan_summation: List[Optional[float]] = Field(description="McClellan Summation Index")
    advances: List[int] = Field(description="Daily advancing stocks count")
    declines: List[int] = Field(description="Daily declining stocks count")
    unchanged: List[int] = Field(description="Daily unchanged stocks count")


class MarketBreadthRequest(BaseModel):
    """Request schema for market breadth indicators."""
    start_date: Optional[str] = Field(None, description="Start date (YYYY-MM-DD)")
    end_date: Optional[str] = Field(None, description="End date (YYYY-MM-DD)")
