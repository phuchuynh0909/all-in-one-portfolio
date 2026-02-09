from backtesting import Strategy
import numpy as np
import talib
from app.services.indicators.trailing_sl import trailing_sl

import numba


@numba.njit
def shift_numba(arr: np.ndarray, num: int, fill_value=np.nan):
    result = np.empty_like(arr)
    if num > 0:
        result[:num] = fill_value
        result[num:] = arr[:-num]
    elif num < 0:
        result[num:] = fill_value
        result[:num] = arr[-num:]
    else:
        result[:] = arr
    return result


def _identity(series: np.ndarray) -> np.ndarray:
    return series


def williams_vix_fix_signal(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    period: int = 22,
    mult: float = 2.0,
    bbl: int = 20,
    lb: int = 50,
    ph: float = 0.85,
    ltLB: int = 40,
    mtLB: int = 14,
    strength_str: int = 2,
):
    highest_close = talib.MAX(close, timeperiod=period)
    wvf = ((highest_close - low) / highest_close) * 100.0

    mid_line = talib.SMA(wvf, timeperiod=bbl)
    s_dev = talib.STDDEV(wvf, timeperiod=bbl) * mult
    upper_band = mid_line + s_dev
    range_high = talib.MAX(wvf, timeperiod=lb) * ph

    # Less strict: require higher low and higher close vs prior close
    up_range = (low >= shift_numba(low, 1)) & (close >= shift_numba(close, 1))

    filtered = (
        ((shift_numba(wvf, 1) >= shift_numba(upper_band, 1)) | (shift_numba(wvf, 1) >= shift_numba(range_high, 1)))
        & (wvf < upper_band)
        & (wvf < range_high)
    )

    cond_fe = (
        up_range
        & (close > shift_numba(close, strength_str))
        & ((close < shift_numba(close, ltLB)) | (close < shift_numba(close, mtLB)))
        & filtered
    )

    return wvf, range_high, filtered, cond_fe


class WilliamsVixStrategyBT(Strategy):
    bb_period = 10
    bb_multiplier = 1.5
    william_vix_period = 20
    lb = 20
    ph = 0.85
    ltLB = 33
    mtLB = 10
    strength_str = 1
    donichan_period = 20
    atr_period = 10
    atr_multiplier = 1.9
    sl_stop = 0.1

    def init(self):
        close = np.asarray(self.data.Close.round(2), dtype=np.float64)
        high = np.asarray(self.data.High.round(2), dtype=np.float64)
        low = np.asarray(self.data.Low.round(2), dtype=np.float64)

        wvf, range_high, filtered, buy_signal = williams_vix_fix_signal(
            close,
            high,
            low,
            period=self.william_vix_period,
            mult=self.bb_multiplier,
            bbl=self.bb_period,
            lb=self.lb,
            ph=self.ph,
            ltLB=self.ltLB,
            mtLB=self.mtLB,
            strength_str=self.strength_str,
        )

        self.buy_signal = buy_signal

        atr = talib.ATR(high, low, close, timeperiod=self.atr_period)
        self.atr_trailing = trailing_sl(close, atr, atr_multiplier=self.atr_multiplier)

        self.dc_low = talib.MIN(low, timeperiod=self.donichan_period)

        self.I(
            lambda: (wvf, range_high),
            overlay=False,
            legends=['WVF', 'Range High'],
            color_by=range_high,
            color_above="#00ff88",
            color_below="#ff4d4d",
            histogramms=[True, False],
            scatter=False,
            name='WVF',
        )
        self.I(_identity, filtered, name='Filtered', overlay=False, color='orange')
        self.I(_identity, self.buy_signal, name='Buy Signal', overlay=False, color='blue')
        self.I(_identity, self.atr_trailing, name='ATR Trailing Stop', overlay=True, color='red')

    def next(self):
        current_idx = len(self.data.Close) - 1
        if current_idx < 0:
            return

        if not self.position and self.buy_signal[current_idx]:
            entry_price = float(self.data.Close[current_idx])
            self.buy(sl=entry_price * (1 - self.sl_stop))
            return

        if self.position and current_idx >= 1:
            if (
                self.data.Close[current_idx - 1] >= self.atr_trailing[current_idx - 1]
                and self.data.Close[current_idx] < self.atr_trailing[current_idx]
            ):
                self.position.close()

            if self.data.Close[current_idx] < self.dc_low[current_idx-1]:
                self.position.close()