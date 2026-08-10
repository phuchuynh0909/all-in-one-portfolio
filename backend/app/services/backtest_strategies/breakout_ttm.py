from backtesting import Strategy
import numpy as np
import pandas as pd
import talib
from app.services.indicators.trailing_sl import trailing_sl
from app.services.indicators.smart_money_flow import smart_money_flow
from app.services.indicators.markov_kama_regime import markov_kama_regime
from app.services.indicators.vwap import avwap as compute_avwap

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

@numba.njit(cache=True)
def kama_1d(prices, period=10, fast=2, slow=30):
    """Kaufman's Adaptive Moving Average (1-D, numba JIT)."""
    n       = len(prices)
    out     = np.full(n, np.nan)
    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    kama    = prices[period - 1]
    out[period - 1] = kama
    for i in range(period, n):
        direction  = abs(prices[i] - prices[i - period])
        volatility = 0.0
        for k in range(period):
            volatility += abs(prices[i - k + 1] - prices[i - k])
        er   = direction / volatility if volatility != 0.0 else 0.0
        sc   = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        kama = kama + sc * (prices[i] - kama)
        out[i] = kama
    return out


def slope_flat_1d(kama, slope_window, flat_threshold_pct):
    """True where |pct KAMA change over slope_window bars| < flat_threshold_pct."""
    n   = len(kama)
    out = np.zeros(n, dtype=np.bool_)
    for i in range(slope_window, n):
        prev = kama[i - slope_window]
        if prev != 0.0 and not np.isnan(prev) and not np.isnan(kama[i]):
            slope_pct = abs((kama[i] - prev) / prev * 100.0)
            out[i] = slope_pct < flat_threshold_pct
    return out


# ── Versioned strategies with optimised params ────────────────────────────────

class BreakoutTTMV1StrategyBT(Strategy):
    """
    v1 — TTM squeeze + positive momentum breakout, fully gated by KAMA flat slope.
    Best params: Total Return 417.87%, Sortino 1.053
    """
    avwap_window          = 200
    # BB
    bb_period             = 15
    bb_multiplier         = 1.0
    matype                = 3
    # Keltner
    kc_period             = 38
    kc_multiplier         = 1.3
    kc_atr_period         = 5
    # TTM oscillator
    donichan_period       = 9
    osc_smoothing_period  = 11
    # Exit
    atr_period            = 6
    atr_multiplier        = 2.0
    low_stop_lookback     = 9
    # KAMA slope filter
    kama_period           = 8
    kama_fast             = 5
    kama_slow             = 41
    kama_slope_win        = 5
    flat_threshold_pct    = 2.7

    def init(self):
        close = np.asarray(self.data.Close, dtype=np.float64)
        high  = np.asarray(self.data.High,  dtype=np.float64)
        low   = np.asarray(self.data.Low,   dtype=np.float64)
        vol   = np.asarray(self.data.Volume, dtype=np.float64)

        bb_upper, _, bb_lower = talib.BBANDS(
            close, timeperiod=self.bb_period,
            nbdevup=self.bb_multiplier, nbdevdn=self.bb_multiplier, matype=self.matype,
        )
        kc_atr = talib.ATR(high, low, close, timeperiod=self.kc_atr_period)
        kc_ema = talib.EMA(close, timeperiod=self.kc_period)
        kc_upper = kc_ema + self.kc_multiplier * kc_atr
        kc_lower = kc_ema - self.kc_multiplier * kc_atr

        hh  = talib.MAX(high, timeperiod=self.donichan_period)
        ll  = talib.MIN(low,  timeperiod=self.donichan_period)
        sma = talib.SMA(close, timeperiod=self.donichan_period)
        osc = close - ((hh + ll) / 2.0 + sma) / 2.0
        ttms = talib.LINEARREG(osc, timeperiod=self.osc_smoothing_period)

        sqz_on  = (bb_upper < kc_upper) & (bb_lower > kc_lower)
        sqz_off = (bb_upper > kc_upper) & (bb_lower < kc_lower)
        no_sqz  = (~sqz_on) & (~sqz_off)

        kama  = kama_1d(close, self.kama_period, self.kama_fast, self.kama_slow)
        flat  = slope_flat_1d(kama, self.kama_slope_win, self.flat_threshold_pct)

        self.buy_signal = no_sqz & (ttms > 0) & flat

        atr_exit       = talib.ATR(high, low, close, timeperiod=self.atr_period)
        self.atr_trail = trailing_sl(close, atr_exit, atr_multiplier=self.atr_multiplier)
        self.low_sl    = talib.MIN(low, timeperiod=self.low_stop_lookback)

        regime, low_var_prob, high_var_prob, regime_trend, _ = markov_kama_regime(close)
        avwap_line = compute_avwap(close, high, low, vol, is_highest=False, window=self.avwap_window)

        self.I(_identity, ttms,              name='TTMS',        overlay=False, color='#4fc3f7')
        self.I(_identity, kama,              name='KAMA',        overlay=True,  color='#f7c59f')
        # self.I(_identity, flat.astype(float),name='KAMA Flat',   overlay=False, color='#3ddc84')
        self.I(_identity, regime.astype(np.float64), name='MK Regime', overlay=False, color='#ff7043')
        # self.I(_identity, low_var_prob,      name='MK Low Var Prob', overlay=False, color='#22c55e')
        # self.I(_identity, high_var_prob,     name='MK High Var Prob', overlay=False, color='#ef4444')
        # self.I(_identity, regime_trend,      name='MK Trend', overlay=False, color='#9c27b0')
        self.I(_identity, self.buy_signal.astype(float), name='Buy Signal', overlay=False, color='blue')
        self.I(_identity, self.atr_trail,    name='ATR Trail',   overlay=True,  color='red')
        self.I(_identity, avwap_line,        name='AVWAP',       overlay=True,  color='#00bcd4')

    def next(self):
        idx = len(self.data.Close) - 1
        if idx < 1:
            return
        close = self.data.Close[idx]
        if not self.position and self.buy_signal[idx]:
            sl = float(self.low_sl[idx - 1])
            if np.isnan(sl) or sl >= close:
                sl = close * 0.95
            self.buy(sl=sl)
            return
        if self.position:
            if close < self.low_sl[idx - 1]:
                self.position.close()
                return
            if (self.data.Close[idx - 1] >= self.atr_trail[idx - 1]
                    and close < self.atr_trail[idx]):
                self.position.close()


class BreakoutTTMV1bStrategyBT(Strategy):
    """
    v1b — Identical to V1 but adds: only enter when close > ATR trailing stop
    (price is above the trailing line, confirming uptrend at entry).
    TTM/BB/KC/KAMA/exit params match V1 (same optimization sweep).
    """
    avwap_window          = 50
    # BB
    bb_period             = 23
    bb_multiplier         = 1.2
    matype                = 3
    # Keltner
    kc_period             = 46
    kc_multiplier         = 1.2
    kc_atr_period         = 19
    # TTM oscillator
    donichan_period       = 10
    osc_smoothing_period  = 5
    # Exit
    atr_period            = 12
    atr_multiplier        = 2.7
    low_stop_lookback     = 7
    # KAMA slope filter
    kama_period           = 14
    kama_fast             = 3
    kama_slow             = 36
    kama_slope_win        = 6
    flat_threshold_pct    = 2.6

    def init(self):
        close = np.asarray(self.data.Close, dtype=np.float64)
        high  = np.asarray(self.data.High,  dtype=np.float64)
        low   = np.asarray(self.data.Low,   dtype=np.float64)
        vol   = np.asarray(self.data.Volume, dtype=np.float64)

        bb_upper, _, bb_lower = talib.BBANDS(
            close, timeperiod=self.bb_period,
            nbdevup=self.bb_multiplier, nbdevdn=self.bb_multiplier, matype=self.matype,
        )
        kc_atr = talib.ATR(high, low, close, timeperiod=self.kc_atr_period)
        kc_ema = talib.EMA(close, timeperiod=self.kc_period)
        kc_upper = kc_ema + self.kc_multiplier * kc_atr
        kc_lower = kc_ema - self.kc_multiplier * kc_atr

        hh  = talib.MAX(high, timeperiod=self.donichan_period)
        ll  = talib.MIN(low,  timeperiod=self.donichan_period)
        sma = talib.SMA(close, timeperiod=self.donichan_period)
        osc = close - ((hh + ll) / 2.0 + sma) / 2.0
        ttms = talib.LINEARREG(osc, timeperiod=self.osc_smoothing_period)

        sqz_on  = (bb_upper < kc_upper) & (bb_lower > kc_lower)
        sqz_off = (bb_upper > kc_upper) & (bb_lower < kc_lower)
        no_sqz  = (~sqz_on) & (~sqz_off)

        kama = kama_1d(close, self.kama_period, self.kama_fast, self.kama_slow)
        flat = slope_flat_1d(kama, self.kama_slope_win, self.flat_threshold_pct)

        atr_exit       = talib.ATR(high, low, close, timeperiod=self.atr_period)
        self.atr_trail = trailing_sl(close, atr_exit, atr_multiplier=self.atr_multiplier)
        self.low_sl    = talib.MIN(low, timeperiod=self.low_stop_lookback)

        regime, low_var_prob, high_var_prob, regime_trend, _ = markov_kama_regime(close)
        avwap_line = compute_avwap(close, high, low, vol, is_highest=False, window=self.avwap_window)

        # Entry: same as V1 + price must be above ATR trailing (uptrend confirmed)
        above_trail = close > self.atr_trail
        above_kama = close > kama
        self.buy_signal = no_sqz & (ttms > 0) & flat & (above_trail | above_kama)

        self.I(_identity, ttms,               name='TTMS',       overlay=False, color='#4fc3f7')
        self.I(_identity, kama,               name='KAMA',       overlay=True,  color='#f7c59f')
        # self.I(_identity, flat.astype(float), name='KAMA Flat',  overlay=False, color='#3ddc84')
        # self.I(_identity, above_trail.astype(float), name='Above ATR Trail', overlay=False, color='#b39ddb')
        self.I(_identity, regime.astype(np.float64), name='MK Regime', overlay=False, color='#ff7043')
        # self.I(_identity, low_var_prob,       name='MK Low Var Prob', overlay=False, color='#22c55e')
        # self.I(_identity, high_var_prob,      name='MK High Var Prob', overlay=False, color='#ef4444')
        # self.I(_identity, regime_trend,       name='MK Trend', overlay=False, color='#9c27b0')
        self.I(_identity, self.buy_signal.astype(float), name='Buy Signal',  overlay=False, color='blue')
        self.I(_identity, self.atr_trail,     name='ATR Trail',  overlay=True,  color='red')
        self.I(_identity, avwap_line,         name='AVWAP',      overlay=True,  color='#00bcd4')

    def next(self):
        idx = len(self.data.Close) - 1
        if idx < 1:
            return
        close = self.data.Close[idx]
        if not self.position and self.buy_signal[idx]:
            sl = float(self.low_sl[idx - 1])
            if np.isnan(sl) or sl >= close:
                sl = close * 0.95
            self.buy(sl=sl)
            return
        if self.position:
            if close < self.low_sl[idx - 1]:
                self.position.close()
                return
            if (self.data.Close[idx - 1] >= self.atr_trail[idx - 1]
                    and close < self.atr_trail[idx]):
                self.position.close()


class BreakoutTTMV1cStrategyBT(Strategy):
    """
    V1c — TTM Breakout V1 gated by SMF Cloud regime filter.

    Entry: same as V1 (no_squeeze & TTM > 0 & KAMA flat)
           AND SMF last_signal == +1 (bull regime)
    Exit:  ATR trailing cross OR lowest-low SL
           OR SMF switch_down (regime turns bearish → force exit)

    TTM/BB/KC/KAMA/exit params match V1; SMF params from same optimization sweep.
    """
    avwap_window          = 200
    # BB
    bb_period             = 23
    bb_multiplier         = 1.2
    matype                = 3
    # Keltner
    kc_period             = 46
    kc_multiplier         = 1.2
    kc_atr_period         = 19
    # TTM oscillator
    donichan_period       = 10
    osc_smoothing_period  = 5
    # Exit
    atr_period            = 12
    atr_multiplier        = 2.7
    low_stop_lookback     = 7
    # KAMA slope filter
    kama_period           = 14
    kama_fast             = 3
    kama_slow             = 36
    kama_slope_win        = 6
    flat_threshold_pct    = 2.6
    # SMF regime params (sweep)
    smf_trend_len         = 13
    smf_mf_len            = 39
    smf_mf_smooth         = 8
    smf_mf_power          = 2.3
    smf_atr_len           = 7
    smf_min_mult          = 0.5
    smf_max_mult          = 2.9
    smf_basis_type        = 'ALMA'

    def init(self):
        close = np.asarray(self.data.Close, dtype=np.float64)
        high  = np.asarray(self.data.High,  dtype=np.float64)
        low   = np.asarray(self.data.Low,   dtype=np.float64)
        open_ = np.asarray(self.data.Open,  dtype=np.float64)
        vol   = np.asarray(self.data.Volume, dtype=np.float64)

        # ── TTM Breakout V1 signals ───────────────────────────────────────────
        bb_upper, _, bb_lower = talib.BBANDS(
            close, timeperiod=self.bb_period,
            nbdevup=self.bb_multiplier, nbdevdn=self.bb_multiplier, matype=self.matype,
        )
        kc_atr   = talib.ATR(high, low, close, timeperiod=self.kc_atr_period)
        kc_ema   = talib.EMA(close, timeperiod=self.kc_period)
        kc_upper = kc_ema + self.kc_multiplier * kc_atr
        kc_lower = kc_ema - self.kc_multiplier * kc_atr

        hh  = talib.MAX(high, timeperiod=self.donichan_period)
        ll  = talib.MIN(low,  timeperiod=self.donichan_period)
        sma = talib.SMA(close, timeperiod=self.donichan_period)
        osc = close - ((hh + ll) / 2.0 + sma) / 2.0
        ttms = talib.LINEARREG(osc, timeperiod=self.osc_smoothing_period)

        sqz_on  = (bb_upper < kc_upper) & (bb_lower > kc_lower)
        sqz_off = (bb_upper > kc_upper) & (bb_lower < kc_lower)
        no_sqz  = (~sqz_on) & (~sqz_off)

        kama = kama_1d(close, self.kama_period, self.kama_fast, self.kama_slow)
        flat = slope_flat_1d(kama, self.kama_slope_win, self.flat_threshold_pct)

        ttm_signal = no_sqz & (ttms > 0) & flat

        # ── SMF Cloud regime ──────────────────────────────────────────────────
        smf = smart_money_flow(
            open_, high, low, close, vol,
            trend_len    = self.smf_trend_len,
            mf_len       = self.smf_mf_len,
            mf_smooth    = self.smf_mf_smooth,
            mf_power     = self.smf_mf_power,
            atr_len      = self.smf_atr_len,
            min_mult     = self.smf_min_mult,
            max_mult     = self.smf_max_mult,
            basis_type   = self.smf_basis_type,
        )
        bull_regime       = smf["last_signal"] == 1
        self.switch_down  = smf["switch_down"]
        smf_basis         = smf["b_close"]
        smf_upper         = smf["upper"]
        smf_lower         = smf["lower"]

        # ── Combined entry: TTM V1 AND bull regime ────────────────────────────
        self.buy_signal = ttm_signal & bull_regime

        # ── Exits ─────────────────────────────────────────────────────────────
        atr_exit       = talib.ATR(high, low, close, timeperiod=self.atr_period)
        self.atr_trail = trailing_sl(close, atr_exit, atr_multiplier=self.atr_multiplier)
        self.low_sl    = talib.MIN(low, timeperiod=self.low_stop_lookback)

        regime, low_var_prob, high_var_prob, regime_trend, _ = markov_kama_regime(close)

        # ── Chart indicators ──────────────────────────────────────────────────
        self.I(_identity, ttms,                     name='TTMS',        overlay=False, color='#4fc3f7')
        self.I(_identity, kama,                     name='KAMA',        overlay=True,  color='#f7c59f')
        self.I(_identity, flat.astype(float),       name='KAMA Flat',   overlay=False, color='#3ddc84')
        self.I(_identity, bull_regime.astype(float),name='SMF Regime',  overlay=False, color='#a855f7')
        self.I(_identity, smf_basis,                name='SMF Basis',   overlay=True,  color='#f39c12')
        self.I(_identity, smf_upper,                name='SMF Upper',   overlay=True,  color='#3498db')
        self.I(_identity, smf_lower,                name='SMF Lower',   overlay=True,  color='#e74c3c')
        self.I(_identity, regime.astype(np.float64), name='MK Regime', overlay=False, color='#ff7043')
        # self.I(_identity, low_var_prob,             name='MK Low Var Prob', overlay=False, color='#22c55e')
        # self.I(_identity, high_var_prob,            name='MK High Var Prob', overlay=False, color='#ef4444')
        # self.I(_identity, regime_trend,             name='MK Trend', overlay=False, color='#9c27b0')
        avwap_line = compute_avwap(close, high, low, vol, is_highest=False, window=self.avwap_window)

        self.I(_identity, self.buy_signal.astype(float), name='Buy Signal', overlay=False, color='blue')
        self.I(_identity, self.atr_trail,           name='ATR Trail',   overlay=True,  color='red')
        self.I(_identity, avwap_line,               name='AVWAP',       overlay=True,  color='#00bcd4')

    def next(self):
        idx = len(self.data.Close) - 1
        if idx < 1:
            return
        close = self.data.Close[idx]
        if not self.position and self.buy_signal[idx]:
            sl = float(self.low_sl[idx - 1])
            if np.isnan(sl) or sl >= close:
                sl = close * 0.95
            self.buy(sl=sl)
            return
        if self.position:
            # Force exit when SMF regime flips to bearish
            if self.switch_down[idx]:
                self.position.close()
                return
            if close < self.low_sl[idx - 1]:
                self.position.close()
                return
            if (self.data.Close[idx - 1] >= self.atr_trail[idx - 1]
                    and close < self.atr_trail[idx]):
                self.position.close()


class BreakoutTTMV2StrategyBT(Strategy):
    """
    v2 — Momentum zero-cross breakout, fully gated by KAMA flat slope.
    Best params: Total Return 441.00%, Sortino 0.997
    """
    avwap_window          = 200
    # BB
    bb_period             = 15
    bb_multiplier         = 1.3
    matype                = 0
    # Keltner
    kc_period             = 52
    kc_multiplier         = 1.2
    kc_atr_period         = 14
    # TTM oscillator
    donichan_period       = 9
    osc_smoothing_period  = 13
    # Exit
    atr_period            = 7
    atr_multiplier        = 2.6
    low_stop_lookback     = 3
    # WVF (not used for v2 entry, kept for chart)
    william_vix_period    = 18
    # KAMA slope filter
    kama_period           = 11
    kama_fast             = 2
    kama_slow             = 36
    kama_slope_win        = 5
    flat_threshold_pct    = 2.5

    def init(self):
        close = np.asarray(self.data.Close, dtype=np.float64)
        high  = np.asarray(self.data.High,  dtype=np.float64)
        low   = np.asarray(self.data.Low,   dtype=np.float64)
        vol   = np.asarray(self.data.Volume, dtype=np.float64)

        bb_upper, _, bb_lower = talib.BBANDS(
            close, timeperiod=self.bb_period,
            nbdevup=self.bb_multiplier, nbdevdn=self.bb_multiplier, matype=self.matype,
        )
        kc_atr   = talib.ATR(high, low, close, timeperiod=self.kc_atr_period)
        kc_ema   = talib.EMA(close, timeperiod=self.kc_period)
        kc_upper = kc_ema + self.kc_multiplier * kc_atr
        kc_lower = kc_ema - self.kc_multiplier * kc_atr

        hh  = talib.MAX(high, timeperiod=self.donichan_period)
        ll  = talib.MIN(low,  timeperiod=self.donichan_period)
        sma = talib.SMA(close, timeperiod=self.donichan_period)
        osc = close - ((hh + ll) / 2.0 + sma) / 2.0
        ttms = talib.LINEARREG(osc, timeperiod=self.osc_smoothing_period)

        sqz_on  = (bb_upper < kc_upper) & (bb_lower > kc_lower)
        sqz_off = (bb_upper > kc_upper) & (bb_lower < kc_lower)
        no_sqz  = (~sqz_on) & (~sqz_off)

        # momentum zero-cross: crossed now, 1 bar ago, or 2 bars ago
        ttms_1 = shift_numba(ttms, 1)
        ttms_2 = shift_numba(ttms, 2)
        ttms_3 = shift_numba(ttms, 3)
        crossed_now  = (ttms_1 < 0) & (ttms > 0)
        crossed_1ago = (ttms_2 < 0) & (ttms_1 > 0)
        crossed_2ago = (ttms_3 < 0) & (ttms_2 > 0)
        cond_2 = (ttms > ttms_1) & crossed_1ago
        cond_3 = (ttms > ttms_2) & crossed_2ago

        kama = kama_1d(close, self.kama_period, self.kama_fast, self.kama_slow)
        flat = slope_flat_1d(kama, self.kama_slope_win, self.flat_threshold_pct)

        self.buy_signal = no_sqz & (crossed_now | cond_2 | cond_3) & flat

        atr_exit       = talib.ATR(high, low, close, timeperiod=self.atr_period)
        self.atr_trail = trailing_sl(close, atr_exit, atr_multiplier=self.atr_multiplier)
        self.low_sl    = talib.MIN(low, timeperiod=self.low_stop_lookback)
        avwap_line     = compute_avwap(close, high, low, vol, is_highest=False, window=self.avwap_window)

        self.I(_identity, ttms,              name='TTMS',       overlay=False, color='#4fc3f7')
        self.I(_identity, kama,              name='KAMA',       overlay=True,  color='#f7c59f')
        self.I(_identity, flat.astype(float),name='KAMA Flat',  overlay=False, color='#3ddc84')
        self.I(_identity, self.buy_signal.astype(float), name='Buy Signal', overlay=False, color='blue')
        self.I(_identity, self.atr_trail,    name='ATR Trail',  overlay=True,  color='red')
        self.I(_identity, avwap_line,        name='AVWAP',      overlay=True,  color='#00bcd4')

    def next(self):
        idx = len(self.data.Close) - 1
        if idx < 1:
            return
        close = self.data.Close[idx]
        if not self.position and self.buy_signal[idx]:
            sl = float(self.low_sl[idx - 1])
            if np.isnan(sl) or sl >= close:
                sl = close * 0.95
            self.buy(sl=sl)
            return
        if self.position:
            if close < self.low_sl[idx - 1]:
                self.position.close()
                return
            if (self.data.Close[idx - 1] >= self.atr_trail[idx - 1]
                    and close < self.atr_trail[idx]):
                self.position.close()


class BreakoutTTMV3StrategyBT(Strategy):
    """
    v3 — Composite entries:
      entry_1 (breakout on squeeze release) — gated by KAMA flat slope
      entry_2 (bottom fishing: extended neg momentum) — no KAMA filter
      WVF (volatility spike / contrarian) — no KAMA filter
    Best params: Total Return 422.02%, Sortino 1.028
    """
    avwap_window          = 200
    # BB
    bb_period             = 10
    bb_multiplier         = 1.2
    matype                = 3
    # Keltner
    kc_period             = 39
    kc_multiplier         = 1.6
    kc_atr_period         = 6
    # TTM oscillator
    donichan_period       = 14
    osc_smoothing_period  = 15
    # Exit
    atr_period            = 18
    atr_multiplier        = 1.9
    low_stop_lookback     = 5
    # entry_2 threshold
    consecutive_neg_threshold = 4
    # WVF
    william_vix_period    = 20

    def init(self):
        close = np.asarray(self.data.Close, dtype=np.float64)
        high  = np.asarray(self.data.High,  dtype=np.float64)
        low   = np.asarray(self.data.Low,   dtype=np.float64)
        vol   = np.asarray(self.data.Volume, dtype=np.float64)

        bb_upper, _, bb_lower = talib.BBANDS(
            close, timeperiod=self.bb_period,
            nbdevup=self.bb_multiplier, nbdevdn=self.bb_multiplier, matype=self.matype,
        )
        kc_atr   = talib.ATR(high, low, close, timeperiod=self.kc_atr_period)
        kc_ema   = talib.EMA(close, timeperiod=self.kc_period)
        kc_upper = kc_ema + self.kc_multiplier * kc_atr

        hh  = talib.MAX(high, timeperiod=self.donichan_period)
        ll  = talib.MIN(low,  timeperiod=self.donichan_period)
        sma = talib.SMA(close, timeperiod=self.donichan_period)
        osc = close - ((hh + ll) / 2.0 + sma) / 2.0
        ttms = talib.LINEARREG(osc, timeperiod=self.osc_smoothing_period)

        squeeze_diff = bb_upper - kc_upper
        consec_neg   = consecutive_bar_ttm_np(ttms)

        # entry_1: bottom fishing — no KAMA filter
        entry_1 = (
            (shift_numba(squeeze_diff, 1) < 0)
            & (squeeze_diff > 0)
            & (consec_neg > self.consecutive_neg_threshold)
        )
        # WVF: volatility spike — no KAMA filter
        wvf, rangeHigh, _, wvf_entry = williams_vix_fix_signal(
            close, high, low,
            period=self.william_vix_period, mult=self.bb_multiplier,
            bbl=self.bb_period, lb=20, ph=0.85, ltLB=33, mtLB=10, strength_str=1,
        )

        self.buy_signal = entry_1 | wvf_entry

        atr_exit       = talib.ATR(high, low, close, timeperiod=self.atr_period)
        self.atr_trail = trailing_sl(close, atr_exit, atr_multiplier=self.atr_multiplier)
        self.low_sl    = talib.MIN(low, timeperiod=self.low_stop_lookback)
        avwap_line     = compute_avwap(close, high, low, vol, is_highest=False, window=self.avwap_window)

        self.I(_identity, ttms,               name='TTMS',       overlay=False, color='#4fc3f7')
        self.I(_identity, wvf,                name='WVF',        overlay=False, color='#f7c59f')
        self.I(_identity, entry_1.astype(float), name='Entry 1 (Bottom)',   overlay=False, color='purple')
        self.I(_identity, wvf_entry.astype(float), name='Entry 2 (WVF)',    overlay=False, color='orange')
        self.I(_identity, self.atr_trail,     name='ATR Trail',  overlay=True,  color='red')
        self.I(_identity, avwap_line,         name='AVWAP',      overlay=True,  color='#00bcd4')

    def next(self):
        idx = len(self.data.Close) - 1
        if idx < 1:
            return
        close = self.data.Close[idx]
        if not self.position and self.buy_signal[idx]:
            sl = float(self.low_sl[idx - 1])
            if np.isnan(sl) or sl >= close:
                sl = close * 0.95
            self.buy(sl=sl)
            return
        if self.position:
            if close < self.low_sl[idx - 1]:
                self.position.close()
                return
            if (self.data.Close[idx - 1] >= self.atr_trail[idx - 1]
                    and close < self.atr_trail[idx]):
                self.position.close()

# ── Legacy class (kept for backward compatibility) ────────────────────────────
class BreakoutTTMStrategyBT(Strategy):
    avwap_window = 200
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
        vol = np.asarray(self.data.Volume, dtype=np.float64)

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
        wvf, rangeHigh, filtered, entry_3 = williams_vix_fix_signal(close, high, low, 
            period=self.william_vix_period, mult=self.bb_multiplier, bbl=self.bb_period, 
            lb=20, ph=0.85, ltLB=33, mtLB=14, strength_str=1)
        buy_signal = entry_1 | entry_2 | entry_3
        self.buy_signal = buy_signal

        # ATR trailing stop
        atr_trailing_real = talib.ATR(high, low, close, timeperiod=10)
        self.atr_trailing = trailing_sl(close, atr_trailing_real, atr_multiplier=1.9)

        regime, low_var_prob, high_var_prob, regime_trend, _ = markov_kama_regime(close)

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
        self.I(_identity, regime.astype(np.float64), name='MK Regime', overlay=False, color='#ff7043')
        # self.I(_identity, low_var_prob, name='MK Low Var Prob', overlay=False, color='#22c55e')
        # self.I(_identity, high_var_prob, name='MK High Var Prob', overlay=False, color='#ef4444')
        # self.I(_identity, regime_trend, name='MK Trend', overlay=False, color='#9c27b0')
        avwap_line = compute_avwap(close, high, low, vol, is_highest=False, window=self.avwap_window)

        self.I(_identity, self.buy_signal, name='Buy Signal', overlay=False, color='blue')
        self.I(_identity, self.atr_trailing, name='ATR Trailing Stop', overlay=True, color='red')
        self.I(_identity, avwap_line,        name='AVWAP',             overlay=True, color='#00bcd4')

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
