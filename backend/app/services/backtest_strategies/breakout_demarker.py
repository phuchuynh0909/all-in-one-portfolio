from backtesting import Strategy
import numpy as np
import pandas as pd
import talib
from ta.volatility import BollingerBands, KeltnerChannel
from app.services.indicators.trailing_sl import trailing_sl


def demarker(high: pd.Series, low: pd.Series, period: int) -> pd.Series:
    """
    DeMarker per MT5 iDeMarker:
      DeMax = max(High - PrevHigh, 0)
      DeMin = max(PrevLow - Low, 0)
      DeM   = SMA(DeMax, p) / (SMA(DeMax, p) + SMA(DeMin, p))
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    demax = (high - prev_high).where(high > prev_high, 0.0)
    demin = (prev_low - low).where(low < prev_low, 0.0)

    demax_sma = demax.rolling(window=period, min_periods=period).mean()
    demin_sma = demin.rolling(window=period, min_periods=period).mean()

    denom = demax_sma + demin_sma
    dem = demax_sma / denom.replace(0.0, np.nan)
    return dem

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = (high - low).abs()
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

def atr_sma(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """
    MT5 iATR uses Wilder's smoothing (RMA) internally, not SMA.
    BUT the MT5 script just calls iATR; to match it closely, we should use Wilder's ATR (RMA).
    This implements Wilder ATR:
      ATR_t = (ATR_{t-1}*(p-1) + TR_t) / p
    """
    tr = true_range(high, low, close)
    atr = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    return atr

def stddev_sma(close: pd.Series, period: int) -> pd.Series:
    """
    MT5 iStdDev(... MODE_SMA, PRICE_CLOSE) = stdev around SMA(close, period).
    Pandas rolling std uses sample ddof=1 by default; MT5 is closer to population.
    Use ddof=0 for closer match.
    """
    return close.rolling(window=period, min_periods=period).std(ddof=0)
    
def _identity(series: np.ndarray) -> np.ndarray:
    return series

class BreakoutDeMarkerStrategyBT(Strategy):
    demarker_period = 14
    keltner_period = 20
    bb_period = 20
    bb_deviation = 1.8
    keltner_factor = 1.8
    keltner_atr_period = 20
    atr_multiplier = 1.8
    sl_stop = 0.1
    entry_version = 'v2'

    def init(self):
        close = np.asarray(self.data.Close, dtype=np.float64)
        high = np.asarray(self.data.High, dtype=np.float64)
        low = np.asarray(self.data.Low, dtype=np.float64)

        # Convert to pandas Series for rolling calculations and ta indicators
        close_series = pd.Series(close, index=self.data.index)
        high_series = pd.Series(high, index=self.data.index)
        low_series = pd.Series(low, index=self.data.index)

        # ---- DeMarker trigger line (Buffer[index] - 0.5) ----
        dem = demarker(high_series, low_series, self.demarker_period)
        d = (dem - 0.5).fillna(0.0)  # MT5 buffers often show 0 until ready

        # DeMarker centered around 0 (range -0.5 to +0.5)
        histo_line = (dem - 0.5).fillna(0.0)
        histo_line_np = histo_line.to_numpy(dtype=np.float64)

        # ---- Width proxies exactly like MT5 ----
        atr = atr_sma(high_series, low_series, close_series, self.keltner_atr_period)
        std = stddev_sma(close_series, self.bb_period)
        
        # ---- Squeeze ratio: if < 1, BB inside Keltner (squeeze ON) ----
        diff = atr * self.keltner_factor                  # Keltner half-width proxy
        bb_radius = self.bb_deviation * std               # Bollinger half-width proxy
        bbs = bb_radius / diff
        bbs = bbs.replace([np.inf, -np.inf], np.nan)
        bbs = bbs.where(diff != 0, np.inf)
        
        squeeze_on = (bbs < 1.0).fillna(False)

        # ---- Histogram components (match MT5 routing) ----
        histo_trending = np.where(~squeeze_on.to_numpy(), histo_line_np, 0.0)
        histo_sideways = np.where(squeeze_on.to_numpy(),  histo_line_np, 0.0)
        direction = np.where(histo_line_np > 0, 1, np.where(histo_line_np < 0, -1, 0))

        # Buy signal: squeeze just ended + bullish direction
        squeeze_just_ended = squeeze_on.shift(1).fillna(False) & ~squeeze_on
        buy_signal = squeeze_just_ended.to_numpy(dtype=bool) & (direction == 1)
        self.buy_signal = buy_signal

        # # Register indicators for plotting using self.I()
        # self.I(_identity, histo_line_np, name='DeMarker Histogram', overlay=False, color='purple')
        # self.I(_identity, np.asarray(histo_sideways, dtype=np.float64), name='Sideways', overlay=False, color='orange')
        self.I(_identity, np.asarray(squeeze_on, dtype=np.float64), name='Squeeze', overlay=False, color='blue')
        self.I(_identity, np.asarray(direction, dtype=np.float64), name='Trending', overlay=False, color='green')
        self.I(_identity, np.asarray(buy_signal, dtype=np.float64), name='Signal', overlay=False, color='green')

        # ========== ATR Trailing Stop ==========
        atr = talib.ATR(high, low, close, timeperiod=self.keltner_atr_period)
        atr_trailing = trailing_sl(close, atr, self.atr_multiplier)
        self.atr_trailing = atr_trailing

        # Plot ATR trailing stop on price chart
        self.I(_identity, atr_trailing, name='ATR Trailing Stop', overlay=True, color='red')

        # Sanity check for matching lengths
        if len(self.buy_signal) != len(self.atr_trailing):
            raise ValueError(
                f"Signal length mismatch: buy_signal={len(self.buy_signal)}, atr_trailing={len(self.atr_trailing)}"
            )

    def next(self):
        current_idx = len(self.data.Close) - 1
        if current_idx < 0:
            return

        if not self.position and self.buy_signal[current_idx]:
            self.buy()
            return

        if self.position and current_idx >= 1:
            if (self.data.Close[current_idx - 1] >= self.atr_trailing[current_idx - 1]
                and self.data.Close[current_idx] < self.atr_trailing[current_idx]
            ):
                self.position.close()