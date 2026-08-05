from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel


class LatestQuote(BaseModel):
    """Latest matched trade for a symbol, shaped for a chart bar update.

    Prices are in thousands of VND — the same scale as the OHLCV bars returned
    by `/timeseries/{symbol}/bars`, so the values can be used directly as a
    real-time update of the current daily bar.
    """

    symbol: str
    #: VN trading date the quote belongs to (the daily bar it updates).
    trading_date: date
    #: Match time as reported by the exchange (VN local time, naive).
    time: datetime
    #: Last match price.
    price: float
    #: Session open / high / low for the board, when the exchange reports them.
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    #: Cumulative session volume (shares).
    volume: Optional[float] = None
    #: Board the quote came from ("G1" = main continuous board).
    board_id: Optional[str] = None
    market_id: Optional[str] = None
    #: Last EOD close strictly before `trading_date`, from the project's own
    #: history — the reference the day's change is measured against.
    prev_close: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    #: `live` = a matched trade from the quote provider; `eod` = the project's own
    #: last end-of-day bar, used for symbols the provider does not trade (indices
    #: such as VNINDEX) or that have not traded yet today.
    source: Literal["live", "eod"] = "live"


class QuoteBatchRequest(BaseModel):
    #: Symbols to quote. Order is preserved in the response.
    symbols: list[str]


class QuoteBatchResponse(BaseModel):
    quotes: list[LatestQuote]
    #: Symbols the provider had no usable trade for (unknown ticker, no session
    #: yet, or an upstream error) — the caller can still show the row as empty.
    unavailable: list[str]
