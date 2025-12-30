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
    bvc: Optional[List[Optional[float]]] = None
    stoch: Optional[Dict[str, List[Optional[float]]]] = None
    kalman_zscore: Optional[List[Optional[float]]] = None
    yz_volatility: Optional[List[Optional[float]]] = None
    rs_rating_20: Optional[List[Optional[float]]] = None
    rs_rating_50: Optional[List[Optional[float]]] = None
    rs_rating_252: Optional[List[Optional[float]]] = None
    rs_rating_20_ema: Optional[List[Optional[float]]] = None
    rs_rating_50_ema: Optional[List[Optional[float]]] = None
    rs_rating_252_ema: Optional[List[Optional[float]]] = None
    matrix_series: Optional[Dict[str, List[Optional[float]]]] = None

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
