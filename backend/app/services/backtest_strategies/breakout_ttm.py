from backtesting import Strategy
import numpy as np
import pandas as pd
import talib
from app.services.indicators.trailing_sl import trailing_sl

import numba
@numba.njit
def shift_numba(arr, num, fill_value=np.nan):
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

def _identity(x):
    return x


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

def consecutive_bar_ttm_np(ttms: np.ndarray) -> np.ndarray:
    out = np.zeros(len(ttms), dtype=np.int64)
    count = 0
    for i in range(len(ttms)):
        if ttms[i] < 0:
            count += 1
            out[i] = count
        else:
            count = 0
    return out

## Williams Vix Fix Formula
def williams_vix_fix_signal(close, high, low, period=22, mult=2.0,
    bbl: int = 20,
    lb: int = 50,
    ph: float = 0.85, # percentile high
    ltLB: int = 40,
    mtLB: int = 14,
    strength_str: int = 2):

    # 1) WVF
    highest_close = talib.MAX(close, timeperiod=period)
    wvf = ((highest_close - low) / highest_close) * 100.0

    # 2) Bands
    midLine = talib.SMA(wvf, timeperiod=bbl)
    sDev = talib.STDDEV(wvf, timeperiod=bbl) * mult
    upperBand = midLine + sDev
    rangeHigh = talib.MAX(wvf, timeperiod=lb) * ph

    # 3) Conditions
    upRange = (low > shift_numba(low, 1)) & (close > shift_numba(high, 1))

    filtered = (
        ((shift_numba(wvf, 1) >= shift_numba(upperBand, 1)) | (shift_numba(wvf, 1) >= shift_numba(rangeHigh, 1)))
        & (wvf < upperBand)
        & (wvf < rangeHigh)
    )

    cond_FE = (
        upRange
        & (close > shift_numba(close, strength_str))
        & ((close < shift_numba(close, ltLB)) | (close < shift_numba(close, mtLB)))
        & filtered
    )

    return wvf, rangeHigh, filtered, cond_FE

class BreakoutTTMStrategyBT(Strategy):
    bb_period = 10
    bb_multiplier = 1.8
    kc_period = 14
    kc_multiplier = 1.1
    kc_atr_period = 10
    donichan_period = 10
    osc_smoothing_period = 5
    matype = 3
    william_vix_period = 25
    entry_version = 'v2'

    def init(self):
        close = np.asarray(self.data.Close.round(2), dtype=np.float64)
        high = np.asarray(self.data.High.round(2), dtype=np.float64)
        low = np.asarray(self.data.Low.round(2), dtype=np.float64)
        print("Length of close, high, low: ", len(close), len(high), len(low))

        ## Squeeze
        bb_indicator = talib.BBANDS(close, timeperiod=self.bb_period, nbdevup=self.bb_multiplier, nbdevdn=self.bb_multiplier, matype=self.matype)
        bb_upper = bb_indicator[0]
        kc_atr = talib.ATR(high, low, close, timeperiod=self.kc_atr_period)
        kc_ema = talib.EMA(close, timeperiod=self.kc_period)
        kc = kc_ema + self.kc_multiplier * kc_atr
        squeeze_diff = bb_upper - kc

        ## Oscillator
        sma_donichan = talib.SMA(close, timeperiod=self.donichan_period)
        highest_high = talib.MAX(high, timeperiod=self.donichan_period)
        lowest_low = talib.MIN(low, timeperiod=self.donichan_period)
        mp = (highest_high + lowest_low) / 2.0
        osc = close - ((mp + sma_donichan) / 2.0)
        ttms = talib.LINEARREG(osc, timeperiod=self.osc_smoothing_period)

        # Store signals for strategy usage (match notebook logic)
        squeeze_diff_np = np.asarray(squeeze_diff, dtype=np.float64)
        ttms_np = np.asarray(ttms, dtype=np.float64)

        entry_1 = (shift_numba(squeeze_diff_np, 1) < 0) & (squeeze_diff_np > 0) & (ttms_np > 0)
        entry_2 = (shift_numba(squeeze_diff_np, 1) < 0) & (squeeze_diff_np > 0) & (consecutive_bar_ttm_np(ttms_np) > 7)
        wvf, rangeHigh, filtered, entry_3 = williams_vix_fix_signal(close, high, low, period=self.william_vix_period, mult=self.bb_multiplier, bbl=self.bb_period, lb=50, ph=0.85, ltLB=40, mtLB=14, strength_str=2)
        buy_signal = entry_1 | entry_2 | entry_3
        self.buy_signal = buy_signal

        # ATR trailing stop
        atr_trailing_real = talib.ATR(high, low, close, timeperiod=10)
        self.atr_trailing = trailing_sl(close, atr_trailing_real, atr_multiplier=1.9)

        self.I(
            lambda: (ttms,),
            overlay=False,
            legends=['TTMS'],
            histogramms=[True],
            scatter=False,
            name='TTMS',
        )
        self.I(
            lambda: (wvf, rangeHigh),
            overlay=False,
            legends=['WVF', 'Range High'],
            color_by=rangeHigh,
            color_above="#00ff88",
            color_below="#ff4d4d",
            histogramms=[True, False],
            scatter=False,
            name='WVF',
        )
        self.I(_identity, entry_3, name='Entry 3', overlay=False, color='orange')
        self.I(_identity, squeeze_diff_np > 0, name='Squeeze Diff', overlay=False, color='green')
        self.I(_identity, self.buy_signal, name='Buy Signal', overlay=False, color='blue')
        self.I(_identity, self.atr_trailing, name='ATR Trailing Stop', overlay=True, color='red')

        ## Max low stoploss
        self.lowest_low = talib.MIN(low, timeperiod=self.donichan_period)


    def next(self):
        current_idx = len(self.data.Close) - 1
        if current_idx < 0:
            return

        # # print logs at the last 20 bars
        # if len(self.atr_trailing) - current_idx <= 20:
        #     print(f"Current index: {current_idx}, Close: {self.data.Close[current_idx]}, Buy Signal: {self.buy_signal[current_idx]}, ATR Trailing: {self.atr_trailing[current_idx]}")

        if not self.position and self.buy_signal[current_idx]:
            # 10% stoploss from entry
            entry_price = float(self.data.Close[current_idx])
            self.buy(sl=entry_price * 0.9)
            return

        if self.position and self.data.Close[current_idx] < self.lowest_low[current_idx-1]:
            self.position.close()
            return

        if self.position and current_idx >= 1:
            if (self.data.Close[current_idx - 1] >= self.atr_trailing[current_idx - 1]
                and self.data.Close[current_idx] < self.atr_trailing[current_idx]
            ):
                self.position.close()