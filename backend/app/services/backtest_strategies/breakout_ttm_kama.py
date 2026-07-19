"""
Backtest 011 — TTM Squeeze with KAMA-centred bands + Pearson-r adaptive ATR exit.

Same machinery as ``breakout_ttm`` (BB-vs-KC squeeze, TTM momentum histogram,
entry v1/v2/v3, swing-low stop) with two changes carried over from the
``notebooks/backtest_011.ipynb`` research:

  1. The basis (middle line) of **both** the Bollinger Band and the Keltner
     Channel is KAMA(close) instead of SMA / EMA.
  2. The ATR trailing-stop multiplier is **adaptive**: a rolling Pearson
     correlation of price-vs-time (``corr_window``) measures trend linearity and
     scales the multiplier between ``atr_mult_min`` and ``atr_mult_max`` — wide
     stop in clean trends (let winners run), tight stop in chop.

     ``mult_mode`` selects how trend strength maps to the multiplier:
       'linear'          : scale = clip(r, 0, 1)                       (original)
       'threshold'       : scale ramps only once r > r_threshold
       'slope'           : clip(r,0,1) * tanh(slope% / slope_k)        (linearity
                           gates, normalized slope supplies magnitude)
       'slope_threshold' : threshold(r) * slope strength
     ``r_smooth`` (EMA span, 1 = off) damps bar-to-bar jumpiness in r.

Default params are the OOS-selected values from ``ttm_best_params_011.json``.
"""
from backtesting import Strategy
import numpy as np
import pandas as pd
import talib

from app.services.indicators.trailing_sl import (
    pearson_r_2d,
    atr_trailing_adaptive_nb,
)
from app.services.indicators.swing_points import swing_high_low
from app.services.backtest_strategies.breakout_ttm import (
    _identity,
    shift_numba,
    consecutive_bar_ttm_np,
    williams_vix_fix_signal,
    kama_1d,
)


def adaptive_atr_trail(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    atr_period: int,
    corr_window: int,
    atr_mult_min: float,
    atr_mult_max: float,
    mult_mode: str = "linear",
    r_threshold: float = 0.6,
    slope_k: float = 0.01,
    r_smooth: int = 1,
):
    """
    Pearson-r adaptive ATR trailing stop (1-D). Mirrors ``compute_exits`` in the
    backtest_011 notebook. Returns (trail, r, scale, mult) — all length-n arrays.
    """
    cN  = close.reshape(-1, 1).astype(np.float64)
    atr = talib.ATR(high, low, close, timeperiod=atr_period)

    r = pearson_r_2d(cN, corr_window).ravel()
    if r_smooth and r_smooth > 1:
        r = pd.Series(r).ewm(span=r_smooth, adjust=False).mean().to_numpy()
    lin = np.clip(np.nan_to_num(r, nan=0.0), 0.0, 1.0)   # trend linearity (long-only)

    if mult_mode == "linear":
        scale = lin
    elif mult_mode == "threshold":
        scale = np.clip((lin - r_threshold) / max(1e-9, 1.0 - r_threshold), 0.0, 1.0)
    elif mult_mode in ("slope", "slope_threshold"):
        slope     = talib.LINEARREG_SLOPE(close, timeperiod=corr_window)
        slope_pct = np.nan_to_num(slope / close, nan=0.0)          # trend % per bar
        slope_scl = np.clip(np.tanh(slope_pct / slope_k), 0.0, 1.0)
        if mult_mode == "slope":
            scale = lin * slope_scl
        else:  # slope_threshold
            thr   = np.clip((lin - r_threshold) / max(1e-9, 1.0 - r_threshold), 0.0, 1.0)
            scale = thr * slope_scl
    else:
        raise ValueError(f"unknown mult_mode {mult_mode!r}")

    mult  = atr_mult_min + (atr_mult_max - atr_mult_min) * scale
    trail = atr_trailing_adaptive_nb(cN, atr.reshape(-1, 1), mult.reshape(-1, 1)).ravel()
    return trail, r, scale, mult


class BreakoutTTMKamaStrategyBT(Strategy):
    """
    Final backtest_011 strategy — KAMA-centred TTM squeeze breakout with a
    Pearson-r adaptive ATR trailing exit and a swing-low stop capped at ``max_sl``.

    Entry logic is selected by ``entry_version``:
      v1 : no-squeeze release & TTM momentum > 0
      v2 : no-squeeze release & TTM momentum zero-cross (now / 1 / 2 bars ago)
      v3 : squeeze release + extended negative momentum (bottom-fishing)
           OR Williams-VIX-Fix contrarian spike

    Defaults below are the v2 OOS-selected params from ttm_best_params_011.json.
    """
    entry_version         = "v2"
    # ── KAMA basis (shared BB + KC middle line) ──
    kama_fast             = 2
    kama_slow             = 30
    # ── Bollinger (KAMA ± σ) ──
    bb_window             = 21
    bb_multiplier         = 2.0
    # ── Keltner (KAMA ± ATR) ──
    kc_window             = 21
    kc_multiplier         = 0.8
    kc_atr_period         = 5
    # ── TTM oscillator ──
    donichan_period       = 10
    osc_smoothing_period  = 10
    # ── Adaptive ATR trailing exit ──
    atr_period            = 17
    atr_mult_min          = 1.9
    atr_mult_max          = 3.7
    corr_window           = 15
    low_stop_lookback     = 4
    max_sl                = 0.06
    # ── Multiplier mapping refinements ──
    mult_mode             = "linear"   # 'linear'|'threshold'|'slope'|'slope_threshold'
    r_threshold           = 0.6
    slope_k               = 0.01
    r_smooth              = 5
    # ── Position sizing from the last swing-high / swing-low distance ──
    swing_length          = 10     # candles each side of a pivot (= confirmation lag)
    range_hi_threshold    = 40.0   # signed swing range % above which we cut size
    reduced_size          = 0.20   # fraction of equity when the swing is extended
    # ── v3-only ──
    consecutive_neg_threshold = 6
    william_vix_period        = 30

    # Fill entries/exits at the signal bar's close (mirrors vbt from_signals,
    # which fills same-bar at close). Read by the plot runner -> Backtest(...).
    _trade_on_close = True

    def init(self):
        close = np.asarray(self.data.Close, dtype=np.float64)
        high  = np.asarray(self.data.High,  dtype=np.float64)
        low   = np.asarray(self.data.Low,   dtype=np.float64)
        volume = np.asarray(self.data.Volume, dtype=np.float64)

        # ── KAMA-centred Bollinger + Keltner ──────────────────────────────────
        bb_basis = kama_1d(close, self.bb_window, self.kama_fast, self.kama_slow)
        bb_std   = talib.STDDEV(close, timeperiod=self.bb_window, nbdev=1.0)
        bb_upper = bb_basis + self.bb_multiplier * bb_std
        bb_lower = bb_basis - self.bb_multiplier * bb_std

        kc_atr   = talib.ATR(high, low, close, timeperiod=self.kc_atr_period)
        kc_basis = kama_1d(close, self.kc_window, self.kama_fast, self.kama_slow)
        kc_upper = kc_basis + self.kc_multiplier * kc_atr
        kc_lower = kc_basis - self.kc_multiplier * kc_atr

        # ── TTM momentum histogram ────────────────────────────────────────────
        hh   = talib.MAX(high, timeperiod=self.donichan_period)
        ll   = talib.MIN(low,  timeperiod=self.donichan_period)
        sma  = talib.SMA(close, timeperiod=self.donichan_period)
        osc  = close - ((hh + ll) / 2.0 + sma) / 2.0
        ttms = talib.LINEARREG(osc, timeperiod=self.osc_smoothing_period)

        sqz_on  = (bb_upper < kc_upper) & (bb_lower > kc_lower)   # BB inside KC
        sqz_off = (bb_upper > kc_upper) & (bb_lower < kc_lower)

        # ── Volume filter ──────────────────────────────────────────────────────
        self.sma_volume = talib.SMA(volume, timeperiod=20)

        # ── Entry signal by version ───────────────────────────────────────────
        wvf = np.full_like(close, np.nan)
        if self.entry_version == "v1":
            self.buy_signal = sqz_off & (ttms > 0)
        elif self.entry_version == "v2":
            ttms_1 = shift_numba(ttms, 1)
            ttms_2 = shift_numba(ttms, 2)
            ttms_3 = shift_numba(ttms, 3)
            crossed_now  = (ttms_1 < 0) & (ttms   > 0)
            crossed_1ago = (ttms_2 < 0) & (ttms_1 > 0)
            crossed_2ago = (ttms_3 < 0) & (ttms_2 > 0)
            cond_2 = (ttms > ttms_1) & crossed_1ago
            cond_3 = (ttms > ttms_2) & crossed_2ago
            self.buy_signal = sqz_off & (crossed_now | cond_2 | cond_3)
        elif self.entry_version == "v3":
            squeeze_diff = bb_upper - kc_upper
            consec_neg   = consecutive_bar_ttm_np(ttms)
            entry_2 = (
                (shift_numba(squeeze_diff, 1) < 0)
                & (squeeze_diff > 0)
                & (consec_neg > self.consecutive_neg_threshold)
            )
            wvf, _rangeHigh, _filtered, wvf_entry = williams_vix_fix_signal(
                close, high, low,
                period=self.william_vix_period, mult=self.bb_multiplier,
                bbl=self.bb_window, lb=20, ph=0.85, ltLB=33, mtLB=10, strength_str=1,
            )
            self.buy_signal = entry_2 | wvf_entry
        else:
            raise ValueError(f"unknown entry_version {self.entry_version!r}")

        # ── Adaptive ATR trailing exit + swing-low stop ───────────────────────
        self.atr_trail, r, _scale, mult = adaptive_atr_trail(
            close, high, low,
            atr_period=self.atr_period, corr_window=self.corr_window,
            atr_mult_min=self.atr_mult_min, atr_mult_max=self.atr_mult_max,
            mult_mode=self.mult_mode, r_threshold=self.r_threshold,
            slope_k=self.slope_k, r_smooth=self.r_smooth,
        )
        self.low_sl = talib.MIN(low, timeperiod=self.low_stop_lookback)
        self.kama   = kc_basis        # KAMA basis line, for the profit-protect exit
        self._stop_price   = np.nan   # fixed per-trade stop level, set at entry
        self._took_partial = False    # whether the 50% profit-take already fired

        # ── Last swing-high / swing-low distance → position sizing ────────────
        # Detect fractal pivots (look-ahead-free) and measure the latest swing
        # leg: magnitude = (last_swing_high - last_swing_low)/last_swing_low, %.
        # Sign from which pivot formed most recently — a fresh swing high means
        # the latest leg was UP (bullish, +, extended); a fresh swing low means
        # the latest leg was DOWN (bearish, -). Unlike a fixed HH–LL window this
        # tracks structure and can't latch onto a run-up from months ago.
        sw = swing_high_low(high, low, swing_length=self.swing_length)
        sh_p, sl_p = sw["last_sh_price"], sw["last_sl_price"]
        self.swing_high, self.swing_low = sh_p, sl_p
        sign = np.where(sw["last_sh_idx"] >= sw["last_sl_idx"], 1.0, -1.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            range_dist = sign * (sh_p - sl_p) / sl_p * 100.0
        # Cut size only when the latest swing is extended to the upside.
        # dist > threshold -> reduced_size; dist < -threshold or in-between -> full.
        self._reduce_size = np.nan_to_num(range_dist, nan=0.0) > self.range_hi_threshold

        # ── Chart indicators ──────────────────────────────────────────────────
        self.I(_identity, ttms,     name="TTMS",       overlay=False, color="#4fc3f7", histogram=True)
        self.I(_identity, r,        name="Pearson r",  overlay=False, color="#3ddc84")
        self.I(_identity, mult,     name="ATR mult",   overlay=False, color="#9c27b0")
        self.I(_identity, sqz_on.astype(float), name="Squeeze ON", overlay=False, color="#ff005d")
        self.I(_identity, range_dist, name="Swing range %", overlay=False, color="#ffb300")
        self.I(_identity, sh_p, name="Last swing high", overlay=True, color="#26a69a")
        self.I(_identity, sl_p, name="Last swing low",  overlay=True, color="#ef5350")
        if self.entry_version == "v3":
            self.I(_identity, wvf,  name="WVF",        overlay=False, color="#f39c12")
        self.I(_identity, kc_basis, name="KAMA basis", overlay=True,  color="#f7c59f")
        # self.I(_identity, bb_upper, name="BB Upper",   overlay=True,  color="#8888aa")
        # self.I(_identity, bb_lower, name="BB Lower",   overlay=True,  color="#8888aa")
        # self.I(_identity, kc_upper, name="KC Upper",   overlay=True,  color="#3498db")
        # self.I(_identity, kc_lower, name="KC Lower",   overlay=True,  color="#e74c3c")
        self.I(_identity, self.buy_signal.astype(float), name="Buy Signal", overlay=False, color="blue")
        self.I(_identity, self.atr_trail, name="ATR Trail (adaptive)", overlay=True, color="red")
        # self.I(_identity, self.sma_volume, name="Volume Filter", overlay=True, color="green")

    def next(self):
        idx = len(self.data.Close) - 1
        if idx < 1:
            return
        close = self.data.Close[idx]
        
        if self.sma_volume[idx] < 100000:
            return

        if not self.position and self.buy_signal[idx]:
            # Fixed swing-low stop set at entry, exactly mirroring the notebook
            # sl_stop = clip((close - lowest_low)/close, 0, max_sl) applied as a
            # fraction of the entry price. Checked on close (vbt got no OHLC, so
            # its stop is close-based too), NOT trailed — so no broker sl here.
            if close < self.kama[idx]: # and close < self.atr_trail[idx]:
                return

            lowest_low = float(self.low_sl[idx])
            if np.isnan(lowest_low):
                pct = self.max_sl
            else:
                pct = min(max((close - lowest_low) / close, 0.0), self.max_sl)
            self._stop_price = close * (1.0 - pct)
            self._took_partial = False
            # position size from the signed HH–LL range: cut to reduced_size when
            # the range is extended to the upside, else full equity.
            if self._reduce_size[idx]:
                self.buy(size=self.reduced_size)
            else:
                self.buy()
            return

        if self.position:
            kama_val   = self.kama[idx]
            stop_price = self._stop_price
            atr_trail  = self.atr_trail[idx]
            # full exit when up >15%, below KAMA, and close crosses below ATR trail
            if (self.position.pl_pct > 15.0
                    and close < kama_val
                    and self.data.Close[idx - 1] >= self.atr_trail[idx - 1]
                    and close < atr_trail):
                self.position.close()
                return
            # profit-take: once up >15%, scale out 50% on a close below the KAMA
            # line (once per position); the rest rides the stop / ATR trail.
            if (not self._took_partial
                    and self.position.pl_pct > 15.0
                    and close < kama_val):
                self.position.close(portion=0.5)
                self._took_partial = True
                return
            # fixed stop-loss (close-based) — matches notebook sl_stop
            if close < stop_price:
                self.position.close()
                return
            # adaptive ATR trailing-stop cross (close-based) — matches crossed_below
            if (self.data.Close[idx - 1] >= self.atr_trail[idx - 1]
                    and close < self.atr_trail[idx]):
                self.position.close()


class BreakoutTTMKamaV1StrategyBT(BreakoutTTMKamaStrategyBT):
    """v1 entry (no-squeeze release + positive TTM), OOS params from 011."""
    entry_version     = "v1"
    bb_window         = 21
    bb_multiplier     = 2.0
    kc_window         = 21
    kc_multiplier     = 0.9
    kc_atr_period     = 14
    atr_period        = 6
    atr_mult_min      = 1.0
    atr_mult_max      = 2.2
    corr_window       = 40
    low_stop_lookback = 2
    max_sl            = 0.11


class BreakoutTTMKamaV3StrategyBT(BreakoutTTMKamaStrategyBT):
    """v3 entry (bottom-fishing + Williams VIX Fix), OOS params from 011."""
    entry_version             = "v3"
    bb_window                 = 14
    bb_multiplier             = 1.1
    kc_window                 = 14
    kc_multiplier             = 1.8
    kc_atr_period             = 13
    atr_period                = 7
    atr_mult_min              = 2.4
    atr_mult_max              = 3.9
    corr_window               = 20
    low_stop_lookback         = 2
    max_sl                    = 0.09
    consecutive_neg_threshold = 6
    william_vix_period        = 30
