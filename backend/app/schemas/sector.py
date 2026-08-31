from datetime import datetime, date
from decimal import Decimal
from typing import Literal, Optional, Dict, List, Union
from pydantic import BaseModel, Field

class SectorBase(BaseModel):
    id: int
    level: int
    type: Optional[str] = None
    name: Optional[str] = None
    smg: Optional[Decimal] = None
    dif: Optional[Decimal] = None
    dif_w: Optional[Decimal] = None
    dif_m: Optional[Decimal] = None
    dif_3m: Optional[Decimal] = None
    vonhoa_d: Optional[Decimal] = None
    eps_d: Optional[Decimal] = None
    pe_d: Optional[Decimal] = None
    pb_d: Optional[Decimal] = None
    roa_ttm: Optional[Decimal] = None
    roe_ttm: Optional[Decimal] = None
    lnst_yoy_ttm: Optional[Decimal] = None
    doanhthuthuan_ttm: Optional[Decimal] = None
    lnst_ttm: Optional[Decimal] = None
    ocf_ttm: Optional[Decimal] = None
    lnst_yoy_q: Optional[Decimal] = None
    novay_q: Optional[Decimal] = None
    tonkho_q: Optional[Decimal] = None
    phaithu_q: Optional[Decimal] = None
    tts_q: Optional[Decimal] = None
    vcsh_q: Optional[Decimal] = None


class Sector(SectorBase):
    created_at: datetime

    class Config:
        from_attributes = True


class StockSymbolBase(BaseModel):
    symbol: str
    name: Optional[str] = None
    id_sector_level_3: Optional[int] = None
    id_sector_level_4: Optional[int] = None
    vonhoa_d: Optional[Decimal] = None


class StockSymbolCreate(StockSymbolBase):
    pass


class StockSymbol(StockSymbolBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SectorSummary(BaseModel):
    sector: Sector
    stocks: List[StockSymbol]


class SectorTimeseriesData(BaseModel):
    id: int
    name: str
    data: List[float]


class SectorTimeseries(BaseModel):
    sector_level: str
    interval: str = Field(default="1d", description="Data interval (e.g., 1d, 1h)")
    meta: Dict = Field(default_factory=dict)
    timestamps: List[str]
    sector_data: Optional[List[SectorTimeseriesData]] = None


# Both are outputs of ``relative_strength_nb``. "mansfield" measures a sector
# against its own recent strength; "outperformance" against where it stood
# ``window`` sessions ago. Both are centred on zero.
SectorRsMetric = Literal["mansfield", "outperformance"]

# "weekly" rolls the daily closes up to one bar per calendar week (W-FRI, last
# traded close in the week) before the measure is computed, so `window` and
# `lookback` then count weeks rather than sessions.
SectorRsTimeframe = Literal["daily", "weekly"]


class SectorRelativeStrengthRow(BaseModel):
    id: int
    name: str
    # Mansfield RS per date, aligned to ``SectorRelativeStrength.dates``.
    # ``None`` where the sector has no bar that day or is still inside the
    # rolling warmup.
    values: List[Optional[float]]


class SectorRelativeStrengthRequest(BaseModel):
    lookback: int = Field(default=41, ge=2, le=252, description="Bars to return, ending at T-0")
    # Left unset the server picks the default for the timeframe: 50 sessions, or
    # 10 weeks, which is the same reach in calendar terms.
    window: Optional[int] = Field(default=None, ge=2, le=252, description="Rolling window, in bars of the timeframe")
    metric: SectorRsMetric = Field(default="mansfield", description="Which relative-strength measure to return")
    timeframe: SectorRsTimeframe = Field(default="daily", description="Bar size the measure is computed on")


class SectorRelativeStrength(BaseModel):
    sector_level: str
    interval: str = Field(default="1d", description="Data interval (e.g., 1d, 1h)")
    benchmark: str = Field(description="Symbol the ratio is taken against")
    window: int = Field(description="Rolling window behind the measure, in bars of the timeframe")
    metric: SectorRsMetric = Field(description="Which relative-strength measure these values are")
    timeframe: SectorRsTimeframe = Field(description="Bar size these values are computed on")
    # Oldest (T-<lookback-1>) first, newest (T-0) last.
    dates: List[str]
    rows: List[SectorRelativeStrengthRow] = Field(default_factory=list)


class SectorDominanceRow(BaseModel):
    id: int
    name: str
    # Composite 0-100, and the four components behind it. Every component is
    # reported so the table can be sorted by any of them and the score can be
    # argued with rather than trusted.
    score: Optional[float] = None
    rs: Optional[float] = Field(default=None, description="Latest relative-strength value")
    mean_rank: Optional[float] = Field(default=None, description="Mean cross-sectional rank over the window, 1 = strongest")
    top_quintile_share: Optional[float] = Field(default=None, description="Share of bars spent in the strongest fifth")
    breadth: Optional[float] = Field(default=None, description="Share of constituents with positive RS at T-0")
    momentum: Optional[float] = Field(default=None, description="Slope of the sector's RS line over the window, per bar")
    turnover_share: Optional[float] = Field(default=None, description="Share of all sector turnover at this level")
    constituents: int = Field(default=0, description="Symbols mapped to this sector")
    constituents_rated: int = Field(default=0, description="Constituents with an RS reading behind `breadth`")


class SectorDominanceRequest(BaseModel):
    lookback: int = Field(default=41, ge=5, le=252, description="Bars the persistence and momentum components look over")
    window: Optional[int] = Field(default=None, ge=2, le=252, description="Rolling window, in bars of the timeframe")
    metric: SectorRsMetric = Field(default="mansfield", description="Relative-strength measure underneath")
    timeframe: SectorRsTimeframe = Field(default="daily", description="Bar size the measure is computed on")
    min_constituents: int = Field(
        default=3, ge=1, le=100,
        description="Sectors with fewer mapped symbols are still returned, but scored None — a one-stock sector is not a dominant sector",
    )


class SectorDominance(BaseModel):
    sector_level: str
    benchmark: str
    window: int
    metric: SectorRsMetric
    timeframe: SectorRsTimeframe
    lookback: int
    min_constituents: int
    # T-0 of the window the components were measured over.
    as_of: Optional[str] = None
    rows: List[SectorDominanceRow] = Field(default_factory=list)


class SectorRotationRow(BaseModel):
    id: int
    name: str
    # Tails, oldest first. 100 is the benchmark: >100 stronger, >100 momentum
    # means strengthening.
    ratio: List[Optional[float]]
    momentum: List[Optional[float]]


class SectorRotationRequest(BaseModel):
    tail: int = Field(default=8, ge=2, le=52, description="Bars of tail to draw per sector")
    window: Optional[int] = Field(default=None, ge=2, le=252, description="Rolling window for the RS ratio")
    momentum_window: Optional[int] = Field(default=None, ge=2, le=252, description="Rolling window for the momentum of that ratio")
    timeframe: SectorRsTimeframe = Field(default="daily", description="Bar size the measure is computed on")


class SectorRotation(BaseModel):
    sector_level: str
    benchmark: str
    window: int
    momentum_window: int
    timeframe: SectorRsTimeframe
    dates: List[str]
    rows: List[SectorRotationRow] = Field(default_factory=list)


class SectorConstituentRow(BaseModel):
    symbol: str
    name: Optional[str] = None
    vonhoa_d: Optional[Decimal] = None
    # Relative strength against the benchmark, same measure and window as the
    # sector panels above it. ``None`` when the symbol has no series in
    # ``ohlc_eod`` — mapped is not the same as covered.
    rs: Optional[float] = None
    # 1-99 percentile of ``rs`` against every other constituent at this level,
    # following the repo's rs_rating convention.
    rs_rank: Optional[int] = None


class SectorConstituentsRequest(BaseModel):
    window: Optional[int] = Field(default=None, ge=2, le=252, description="Rolling window, in bars of the timeframe")
    metric: SectorRsMetric = Field(default="mansfield", description="Which relative-strength measure to return")
    timeframe: SectorRsTimeframe = Field(default="daily", description="Bar size the measure is computed on")


class SectorConstituents(BaseModel):
    sector_level: str
    sector_id: int
    benchmark: str
    window: int
    metric: SectorRsMetric
    timeframe: SectorRsTimeframe
    as_of: Optional[str] = None
    # Covered / mapped, so a thin denominator is visible rather than implied.
    covered: int = 0
    mapped: int = 0
    rows: List[SectorConstituentRow] = Field(default_factory=list)
