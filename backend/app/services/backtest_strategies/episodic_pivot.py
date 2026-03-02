from backtesting import Strategy
import numpy as np
import talib
from app.services.indicators.trailing_sl import trailing_sl


def _identity(series: np.ndarray) -> np.ndarray:
    return series


def episodic_pivot_signal(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    gap_threshold: float = 0.10,
    vol_mult: float = 3.0,
    vol_period: int = 20,
    wait_days: int = 5,
    breakout_lookahead: int = 10,
    hold_days: int = 3,
):
    """
    Compute Episodic Pivot delayed entry signals.

    - EP Day: bullish Fair Value Gap (low > high[2 bars ago]) >= gap_threshold AND volume >= avg_vol * vol_mult
    - Consolidation: wait_days after EP, lows must stay above EP day low
    - Entry: first close above post-gap high within breakout_lookahead bars
    - Hold confirmation: price must NOT go below breakout candle low within hold_days
    - Stop loss: EP day low
    """
    n = len(close)
    prev_close = np.roll(close, 1)
    prev_close[0] = np.nan

    # Gap 
    # gap_pct = np.where(prev_close > 0, (open_ - prev_close) / prev_close, 0.0)

    # Fair Value Gap (bullish FVG): low[i] > high[i-2], gap = low[i] - high[i-2]
    high_2 = np.roll(high, 2)
    high_2[:2] = np.nan
    fvg_exists = (low > high_2) & (high_2 > 0)
    gap_pct = np.where(fvg_exists, (low - high_2) / high_2, 0.0)
    avg_vol = talib.SMA(volume, timeperiod=vol_period)
    avg_vol = np.where(np.isnan(avg_vol), 0.0, avg_vol)

    is_ep_day = (
        (gap_pct >= gap_threshold)
        # & (close > prev_close)
        & (volume >= avg_vol * vol_mult)
        & (avg_vol > 0)
    )

    buy_signal = np.zeros(n, dtype=bool)
    stop_loss = np.full(n, np.nan, dtype=np.float64)

    for i in range(n):
        if not is_ep_day[i]:
            continue

        ep_low = low[i - 1]

        # Consolidation zone: bars i+1 to i+wait_days
        cons_start = i + 1
        cons_end = cons_start + wait_days
        if cons_end > n:
            continue

        consolidation_lows = low[cons_start:cons_end]
        if np.min(consolidation_lows) < ep_low:
            continue

        entry_bar = cons_start + wait_days

        # # condition if close[entry_bar] > low[i] and close[entry_bar] < high[i]
        # if close[entry_bar] > low[i] and close[entry_bar] < high[i]:    
        #     buy_signal[entry_bar] = True
        # else:
        #     continue

        buy_signal[entry_bar] = True
        stop_loss[entry_bar] = ep_low

    return is_ep_day, buy_signal, stop_loss


class EpisodicPivotStrategyBT(Strategy):
    gap_threshold = 0.01
    vol_mult = 1.5
    vol_period = 20
    wait_days = 2
    breakout_lookahead = 3
    hold_days = 3
    atr_period = 10
    atr_multiplier = 1.8

    def init(self):
        open_ = np.asarray(self.data.Open.round(2), dtype=np.float64)
        high = np.asarray(self.data.High.round(2), dtype=np.float64)
        low = np.asarray(self.data.Low.round(2), dtype=np.float64)
        close = np.asarray(self.data.Close.round(2), dtype=np.float64)
        volume = np.asarray(self.data.Volume, dtype=np.float64)
        volume = np.where(np.isnan(volume), 0.0, volume)

        is_ep_day, buy_signal, stop_loss = episodic_pivot_signal(
            open_,
            high,
            low,
            close,
            volume,
            gap_threshold=self.gap_threshold,
            vol_mult=self.vol_mult,
            vol_period=self.vol_period,
            wait_days=self.wait_days,
            breakout_lookahead=self.breakout_lookahead,
            hold_days=self.hold_days,
        )

        self.buy_signal = buy_signal
        self.stop_loss = stop_loss

        atr = talib.ATR(high, low, close, timeperiod=self.atr_period)
        self.atr_trailing = trailing_sl(close, atr, atr_multiplier=self.atr_multiplier)

        self.I(_identity, is_ep_day.astype(np.float64), name='EP Day', overlay=False, color='orange')
        self.I(_identity, buy_signal.astype(np.float64), name='Buy Signal', overlay=False, color='green')
        self.I(_identity, self.atr_trailing, name='ATR Trailing Stop', overlay=True, color='red')

    def next(self):
        current_idx = len(self.data.Close) - 1
        if current_idx < 0:
            return

        if not self.position and self.buy_signal[current_idx]:
            entry_price = float(self.data.Close[current_idx])
            sl = float(self.stop_loss[current_idx])
            if not np.isnan(sl) and sl > 0 and entry_price > sl:
                self.buy(sl=sl)
            else:
                self.buy()
            return

        if self.position and current_idx >= 1:
            if (
                self.data.Close[current_idx - 1] >= self.atr_trailing[current_idx - 1]
                and self.data.Close[current_idx] < self.atr_trailing[current_idx]
            ):
                self.position.close()
