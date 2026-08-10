"""
Standalone single-file version of train_meta_label_models.py.
All app.services.indicators / app.services.strategies dependencies are inlined.
No local imports — safe to run in any cloud environment.
"""
from __future__ import annotations

import gc
import logging
import os
import sys
import time
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("meta_label")


PROJECT_ROOT = Path(__file__).resolve()
PIPELINE_OUTPUT_DIR = "notebooks"
MODELS_DIR =  "models"
USE_GPU: bool = False  # set to True via --gpu flag or USE_GPU=1 env var
DEFAULT_WATCHLIST_SYMBOLS = (
    "AAH", "AAA", "ABB", "ACB", "ACV", "ADS", "AGG", "AGR", "ANV", "APH",
    "ASM", "BAF", "BCM", "BFC", "BID", "BMP", "BSI", "BSR", "BVB",
    "BVH", "BVS", "BWE", "C4G", "CEO", "CII", "CMG", "CNG", "CSV", "CTD",
    "MIG", "CTG", "CTI", "CTR", "CTS", "DBC", "DCM", "DDV", "DGC", "DGW",
    "DHC", "DIG", "DPG", "DPM", "DPR", "DRC", "DRI", "DTD", "DVM", "DXG",
    "DXS", "EIB", "ELC", "EVF", "EVG", "FCN", "FOX", "FPT", "FRT", "FTS",
    "GAS", "GEG", "GEX", "GIL", "GKM", "GMD", "GVR", "HAG", "HAH", "HAX",
    "HBC", "HCM", "HDB", "HDC", "HDG", "HHS", "HHV", "HNG", "HPG", "HPX",
    "HQC", "HSG", "HTN", "HUT", "HVN", "IDC", "IDI", "IDJ", "IJC", "ITA",
    "KBC", "KDC", "KDH", "KHG", "KOS", "KSB", "LAS", "LCG", "LPB", "LSS",
    "MBB", "MBS", "MCH", "MSB", "MSH", "MSN", "MSR", "MWG", "NAB", "NHA",
    "NHH", "NKG", "NLG", "NT2", "NTL", "NVL", "OCB", "OIL", "ORS", "PAN",
    "PC1", "PDR", "PET", "PHR", "PLC", "PLX", "PNJ", "POW", "PSH", "PTB",
    "PVB", "PVC", "PVI", "PVD", "PVP", "PVS", "PVT", "QCG", "QNS", "REE",
    "SAB", "SBT", "SCR", "SCS", "SHB", "SHS", "SIP", "SJS", "SKG", "SMC",
    "SSB", "SSI", "STB", "SZC", "TCB", "TCH", "TCM", "TIG", "TNG", "TPB",
    "TV2", "VC3", "VC7", "VCB", "VCG", "VCI", "VCS", "VDS", "VFS", "VGC",
    "VGI", "VGS", "VGT", "VHC", "VHM", "VIB", "VIC", "VIX", "VJC", "VND",
    "VNM", "VOS", "VPB", "VPG", "VPI", "VRE", "VSC", "VTP", "YEG", "IMP",
    "L18", "QTP", "KSV", "VNINDEX", "VN30", "PAT", "STK", "PAC", "TLG",
)


# ── Inlined: app.services.indicators.zcore ──────────────────────────────────

import numpy as np
import numba as nb
from numba import njit, prange


@njit
def zscore_nb(data, window):
    zscore_result = np.full(data.shape, np.nan)
    for col in range(data.shape[1]):
        for i in range(window, data.shape[0]):
            window_data = data[i - window:i, col]
            std_dev = np.std(window_data)
            if std_dev == 0:
                zscore_result[i, col] = 0.0
            else:
                mean_val = np.mean(window_data)
                zscore_result[i, col] = (data[i, col] - mean_val) / std_dev
    return zscore_result


# ── Inlined: app.services.indicators.common ──────────────────────────────────

_EPS_COMMON = 1e-10


@njit
def relative_strength_nb(close, benmark_close, window):
    rs = np.full(close.shape, np.nan, dtype=np.float64)
    mrs = np.full(close.shape, np.nan, dtype=np.float64)
    rs_ratio = close / (benmark_close + _EPS_COMMON)
    mean_rs_ratio = np.full(close.shape, np.nan, dtype=np.float64)
    for col in range(close.shape[1]):
        for i in range(window, close.shape[0]):
            mean_rs_ratio[i, col] = np.mean(rs_ratio[i - window:i, col])
    for col in range(close.shape[1]):
        for i in range(window, close.shape[0]):
            rs[i, col] = (rs_ratio[i, col] / (rs_ratio[i - window, col] + _EPS_COMMON)) * 100 - 100
            mrs[i, col] = ((rs_ratio[i, col] / (mean_rs_ratio[i, col] + _EPS_COMMON)) - 1) * 100
    return rs, mrs


# ── Inlined: app.services.indicators.vwap ────────────────────────────────────

@njit(parallel=True)
def avwap_func_nb(close_arr, high_arr, low_arr, volume_arr, is_highest: bool = True, window: int = 200):
    n_rows, n_cols = close_arr.shape
    avwap_arr = np.full((n_rows, n_cols), np.nan)
    for col in nb.prange(n_cols):
        close = close_arr[:, col]
        high = high_arr[:, col]
        low = low_arr[:, col]
        volume = volume_arr[:, col]
        for i in range(window - 1, n_rows):
            window_start = i - window + 1
            if is_highest:
                extreme_val = close[window_start]
                extreme_idx = 0
                for j in range(1, window):
                    if close[window_start + j] > extreme_val:
                        extreme_val = close[window_start + j]
                        extreme_idx = j
            else:
                extreme_val = close[window_start]
                extreme_idx = 0
                for j in range(1, window):
                    if close[window_start + j] < extreme_val:
                        extreme_val = close[window_start + j]
                        extreme_idx = j
            anchor_index = window_start + extreme_idx
            cum_tp_vol = 0.0
            cum_vol = 0.0
            for k in range(anchor_index, i + 1):
                vol = volume[k]
                if vol > 0:
                    tp = (close[k] + high[k] + low[k]) / 3.0
                    cum_tp_vol += tp * vol
                    cum_vol += vol
            if cum_vol > 0:
                avwap_arr[i, col] = cum_tp_vol / cum_vol
    return avwap_arr


# ── Inlined: app.services.indicators.yang_zhang_volatility ───────────────────

@njit(parallel=True)
def yang_zhang_volatility_nb(close, open, high, low, window=30, periods=252):
    n_rows, n_cols = close.shape
    yz_volatility = np.full(close.shape, np.nan, dtype=np.float32)
    k = 0.34 / (1.34 + (window + 1) / (window - 1))
    for col in nb.prange(n_cols):
        for i in range(window, n_rows):
            start_idx = i - window + 1
            window_open = open[start_idx:i + 1, col]
            window_high = high[start_idx:i + 1, col]
            window_low = low[start_idx:i + 1, col]
            window_close = close[start_idx:i + 1, col]
            prev_close_indices = np.arange(max(0, start_idx - 1), i)
            previous_close_for_overnight = close[prev_close_indices, col]
            log_oc_window = np.log(window_open / previous_close_for_overnight)
            log_co_window = np.log(window_close / window_open)
            log_hl_window = np.log(window_high / window_low)
            sigma_oc_sq_window = np.mean(log_oc_window ** 2)
            sigma_co_sq_window = np.mean(log_co_window ** 2)
            sigma_rs_sq_window = np.mean(
                0.5 * log_hl_window ** 2
                - (2 * np.log(2) - 1) * (log_co_window ** 2 + log_oc_window ** 2)
            )
            sigma_rs_sq_window = max(0, sigma_rs_sq_window)
            sigma_yz_sq_window = sigma_oc_sq_window + k * sigma_co_sq_window + (1 - k) * sigma_rs_sq_window
            sigma_yz_sq_window = max(0, sigma_yz_sq_window)
            yz_volatility[i, col] = np.sqrt(sigma_yz_sq_window) * np.sqrt(periods)
    return yz_volatility


# ── Inlined: app.services.indicators.gkyz_volatility ────────────────────────

import pandas as pd


def calculate_gkyz_volatility(
    open_prices: np.ndarray,
    high_prices: np.ndarray,
    low_prices: np.ndarray,
    close_prices: np.ndarray,
    window: int = 21,
    normalize: bool = True,
) -> np.ndarray:
    opens = pd.Series(open_prices, dtype=float).round(2)
    highs = pd.Series(high_prices, dtype=float).round(2)
    lows = pd.Series(low_prices, dtype=float).round(2)
    closes = pd.Series(close_prices, dtype=float).round(2)
    prev_close = closes.shift(1).fillna(closes)
    log_oc = np.log(opens / prev_close)
    log_hl = np.log(highs / lows)
    log_co = np.log(closes / opens)
    oc_comp = (log_oc ** 2).rolling(window).mean()
    hl_comp = 0.5 * (log_hl ** 2).rolling(window).mean()
    co_comp = -(2 * np.log(2) - 1) * (log_co ** 2).rolling(window).mean()
    raw = np.sqrt((oc_comp + hl_comp + co_comp).clip(lower=0))
    result = raw * np.sqrt(252) * 100
    if normalize:
        lo = result.rolling(window).min()
        hi = result.rolling(window).max()
        result = (result - lo) / (hi - lo + 1e-10)
    return result.to_numpy()


# ── Inlined: app.services.indicators.squeeze_ttm ────────────────────────────

import vectorbt as vbt


def squeeze_ttm(
    close: np.ndarray, high: np.ndarray, low: np.ndarray,
    bb_period: int = 20, bb_mult: float = 1.2, bb_matype: int = 3,
    kc_period: int = 10, kc_mult: float = 1.2,
    donichan_period: int = 10, osc_smoothing_period: int = 10,
):
    bb = vbt.IndicatorFactory.from_talib('BBANDS').run(
        close, timeperiod=bb_period, nbdevup=bb_mult, nbdevdn=bb_mult, matype=bb_matype
    )
    atr = vbt.IndicatorFactory.from_talib('ATR').run(high, low, close, timeperiod=kc_period)
    ema = vbt.IndicatorFactory.from_talib('EMA').run(close, timeperiod=kc_period)
    kc = ema.real + kc_mult * atr.real
    diff = bb.upperband.vbt - kc
    sma = vbt.IndicatorFactory.from_talib('SMA').run(close, timeperiod=donichan_period).real
    hh = vbt.IndicatorFactory.from_talib('MAX').run(high, timeperiod=donichan_period).real
    ll = vbt.IndicatorFactory.from_talib('MAX').run(low, timeperiod=donichan_period).real
    mid = (hh + ll) / 2
    histogram = close - ((mid + sma) / 2)
    ttms = vbt.IndicatorFactory.from_talib('LINEARREG').run(histogram, timeperiod=osc_smoothing_period).real
    return diff, ttms


# ── Inlined: app.services.indicators.trailing_sl ────────────────────────────

@njit(parallel=True)
def atr_trailing_nb(close, atr_val, atr_multiplier: float = 1.8):
    sl = atr_val * atr_multiplier
    trail = np.full(close.shape, np.nan, dtype=np.float64)
    for col in nb.prange(trail.shape[1]):
        for i in range(1, trail.shape[0]):
            if np.isnan(close[i, col]):
                trail[i, col] = np.nan
                continue
            src = close[i, col]
            src_prev = close[i - 1, col]
            trail_prev = trail[i - 1, col]
            iff_1 = src - sl[i, col] if src > trail_prev else src + sl[i, col]
            iff_2 = min(trail_prev, src + sl[i, col]) if src < trail_prev and src_prev < trail_prev else iff_1
            trail[i, col] = max(trail_prev, src - sl[i, col]) if src > trail_prev and src_prev > trail_prev else iff_2
    return trail


# ── Inlined: app.services.indicators.wiliams_vix_fix ────────────────────────

import traceback


@nb.njit
def _shift_numba(arr: np.ndarray, num: int, fill_value=np.nan):
    result = np.empty_like(arr)
    n_rows, n_cols = arr.shape
    if num > 0:
        result[:num, :] = fill_value
        result[num:, :] = arr[:n_rows - num, :]
    elif num < 0:
        result[num:, :] = fill_value
        result[:n_rows + num, :] = arr[-num:, :]
    else:
        result[:, :] = arr
    return result


def williams_vix_fix_indicator(
    close, high, low,
    period=22, mult=2.0, bbl: int = 20, lb: int = 50,
    ph: float = 0.85, ltLB: int = 40, mtLB: int = 14, strength_str: int = 3,
):
    try:
        MAX = vbt.IndicatorFactory.from_talib("MAX")
        SMA = vbt.IndicatorFactory.from_talib("SMA")
        STDDEV = vbt.IndicatorFactory.from_talib("STDDEV")
        highest_close = MAX.run(close, timeperiod=period).real
        wvf = ((highest_close - low) / highest_close) * 100.0
        midLine = SMA.run(wvf, timeperiod=bbl).real
        sDev = STDDEV.run(wvf, timeperiod=bbl).real * mult
        upperBand = midLine + sDev
        rangeHigh = MAX.run(wvf, timeperiod=lb).real * ph
        if isinstance(low, pd.DataFrame):
            low_np = low.to_numpy()
            close_np = close.to_numpy()
            high_np = high.to_numpy()
        else:
            low_np = low
            close_np = close
            high_np = high
        wvf_np = wvf.to_numpy()
        upperBand_np = upperBand.to_numpy()
        rangeHigh_np = rangeHigh.to_numpy()
        upRange = (low_np > _shift_numba(low_np, 1)) & (close_np > _shift_numba(close_np, 1))
        filtered = (
            ((_shift_numba(wvf_np, 1) >= _shift_numba(upperBand_np, 1)) | (_shift_numba(wvf_np, 1) >= _shift_numba(rangeHigh_np, 1)))
            & (wvf_np < upperBand_np)
            & (wvf_np < rangeHigh_np)
        )
        cond_FE = (
            upRange
            & (close_np > _shift_numba(close_np, strength_str))
            & ((close_np < _shift_numba(close_np, ltLB)) | (close_np < _shift_numba(close_np, mtLB)))
            & filtered
        )
    except Exception as e:
        print(f"Error calculating Williams Vix Fix: {e}")
        print(traceback.format_exc())
        return np.nan, np.nan, np.nan, np.nan
    return wvf, rangeHigh, filtered, cond_FE


# ── Inlined: app.services.strategies.breakout_ttm_v1 ─────────────────────────

FIXED_TTM_PARAMS = {
    'v1': {
        'bb_window': 15, 'bb_multiplier': 1.0, 'bb_matype': 3,
        'kc_window': 38, 'kc_multiplier': 1.3, 'kc_atr_period': 5,
        'donichan_window': 9, 'osc_smoothing_period': 11,
        'atr_period': 6, 'atr_multiplier': 2.0,
        'low_stop_lookback': 9, 'consecutive_neg_threshold': 13,
        'william_vix_period': 17,
        'kama_period': 8, 'kama_fast': 5, 'kama_slow': 41,
        'kama_slope_win': 5, 'flat_threshold_pct': 2.7,
    },
    'v2': {
        'bb_window': 15, 'bb_multiplier': 1.3, 'bb_matype': 0,
        'kc_window': 52, 'kc_multiplier': 1.2, 'kc_atr_period': 14,
        'donichan_window': 9, 'osc_smoothing_period': 13,
        'atr_period': 7, 'atr_multiplier': 2.6,
        'low_stop_lookback': 3, 'consecutive_neg_threshold': 8,
        'william_vix_period': 18,
        'kama_period': 11, 'kama_fast': 2, 'kama_slow': 36,
        'kama_slope_win': 5, 'flat_threshold_pct': 2.5,
    },
    'v3': {
        'bb_window': 10, 'bb_multiplier': 1.2, 'bb_matype': 3,
        'kc_window': 39, 'kc_multiplier': 1.6, 'kc_atr_period': 6,
        'donichan_window': 14, 'osc_smoothing_period': 15,
        'atr_period': 11, 'atr_multiplier': 2.6,
        'low_stop_lookback': 5, 'consecutive_neg_threshold': 4,
        'william_vix_period': 17,
        'kama_period': 16, 'kama_fast': 5, 'kama_slow': 39,
        'kama_slope_win': 5, 'flat_threshold_pct': 1.8,
    },
}


@nb.njit
def _shift_2d(arr, num, fill_value=np.nan):
    result = np.empty_like(arr)
    n = arr.shape[0]
    if num > 0:
        result[:num, :] = fill_value
        result[num:, :] = arr[:n - num, :]
    elif num < 0:
        result[n + num:, :] = fill_value
        result[:n + num, :] = arr[-num:, :]
    else:
        result[:, :] = arr
    return result


@nb.njit
def _count_consecutive_neg_2d(arr):
    n, m = arr.shape
    out = np.zeros((n, m), dtype=np.int64)
    for j in range(m):
        count = 0
        for i in range(n):
            if arr[i, j] < 0:
                count += 1
                out[i, j] = count
            else:
                count = 0
    return out


@nb.njit(cache=True)
def _kama_2d(prices_np, period=10, fast=2, slow=30):
    n, m = prices_np.shape
    out = np.full((n, m), np.nan)
    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    for j in range(m):
        kama = prices_np[period - 1, j]
        out[period - 1, j] = kama
        for i in range(period, n):
            direction = abs(prices_np[i, j] - prices_np[i - period, j])
            volatility = 0.0
            for k in range(1, period + 1):
                volatility += abs(prices_np[i - k + 1, j] - prices_np[i - k, j])
            er = direction / volatility if volatility != 0.0 else 0.0
            sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
            kama = kama + sc * (prices_np[i, j] - kama)
            out[i, j] = kama
    return out


@nb.njit(cache=True)
def _slope_flat_2d(kama_np, slope_window, flat_threshold_pct):
    n, m = kama_np.shape
    out = np.zeros((n, m), dtype=nb.boolean)
    for j in range(m):
        for i in range(slope_window, n):
            prev = kama_np[i - slope_window, j]
            if prev != 0.0 and not np.isnan(prev) and not np.isnan(kama_np[i, j]):
                slope_pct = abs((kama_np[i, j] - prev) / prev * 100.0)
                out[i, j] = slope_pct < flat_threshold_pct
    return out


def _compute_signals(
    close, high, low,
    bb_window=16, bb_multiplier=1.0, bb_matype=0,
    kc_window=40, kc_multiplier=1.2, kc_atr_period=10,
    donichan_window=12, osc_smoothing_period=10,
    entry_version='v3', consecutive_neg_threshold=7,
    william_vix_period=20,
    use_kama_slope=True, kama_period=10, kama_fast=2, kama_slow=30,
    kama_slope_win=5, flat_threshold_pct=1.0,
):
    BB = vbt.IndicatorFactory.from_talib('BBANDS')
    ATR = vbt.IndicatorFactory.from_talib('ATR')
    EMA = vbt.IndicatorFactory.from_talib('EMA')
    SMA = vbt.IndicatorFactory.from_talib('SMA')
    MAX = vbt.IndicatorFactory.from_talib('MAX')
    LREG = vbt.IndicatorFactory.from_talib('LINEARREG')

    bb = BB.run(close, timeperiod=bb_window, nbdevup=bb_multiplier, nbdevdn=bb_multiplier, matype=bb_matype)
    atr = ATR.run(high, low, close, timeperiod=kc_atr_period)
    ema = EMA.run(close, timeperiod=kc_window)

    bb_upper_np = bb.upperband.to_numpy()
    bb_lower_np = bb.lowerband.to_numpy()
    ema_np = ema.real.to_numpy()
    atr_np = atr.real.to_numpy()
    kc_upper_np = ema_np + kc_multiplier * atr_np
    kc_lower_np = ema_np - kc_multiplier * atr_np

    hh = MAX.run(high, timeperiod=donichan_window).real.to_numpy()
    ll = MAX.run(low, timeperiod=donichan_window).real.to_numpy()
    sma = SMA.run(close, timeperiod=donichan_window).real.to_numpy()
    close_np = close.to_numpy()
    histogram = close_np - ((hh + ll) / 2 + sma) / 2
    ttms_np = LREG.run(
        pd.DataFrame(histogram, index=close.index, columns=close.columns),
        timeperiod=osc_smoothing_period,
    ).real.to_numpy()

    _, _, _, wvf_signal = williams_vix_fix_indicator(
        close, high, low,
        period=william_vix_period, mult=bb_multiplier, bbl=bb_window,
        lb=20, ph=0.85, ltLB=33, mtLB=10, strength_str=1,
    )
    wvf_np = wvf_signal if isinstance(wvf_signal, np.ndarray) else wvf_signal.to_numpy()

    if use_kama_slope:
        kama_np = _kama_2d(close_np.astype(np.float64), kama_period, kama_fast, kama_slow)
        flat_np = _slope_flat_2d(kama_np, kama_slope_win, flat_threshold_pct)
    else:
        flat_np = np.ones_like(close_np, dtype=np.bool_)

    if entry_version == 'v1':
        sqz_on_np = (bb_upper_np < kc_upper_np) & (bb_lower_np > kc_lower_np)
        sqz_off_np = (bb_upper_np > kc_upper_np) & (bb_lower_np < kc_lower_np)
        no_sqz_np = (~sqz_on_np) & (~sqz_off_np)
        entries_np = no_sqz_np & (ttms_np > 0) & flat_np
    elif entry_version == 'v2':
        sqz_on_np = (bb_upper_np < kc_upper_np) & (bb_lower_np > kc_lower_np)
        sqz_off_np = (bb_upper_np > kc_upper_np) & (bb_lower_np < kc_lower_np)
        no_sqz_np = (~sqz_on_np) & (~sqz_off_np)
        ttms_1 = _shift_2d(ttms_np, 1)
        ttms_2 = _shift_2d(ttms_np, 2)
        ttms_3 = _shift_2d(ttms_np, 3)
        crossed_now = (ttms_1 < 0) & (ttms_np > 0)
        crossed_1ago = (ttms_2 < 0) & (ttms_1 > 0)
        crossed_2ago = (ttms_3 < 0) & (ttms_2 > 0)
        cond_2 = (ttms_np > ttms_1) & crossed_1ago
        cond_3 = (ttms_np > ttms_2) & crossed_2ago
        entries_np = no_sqz_np & (crossed_now | cond_2 | cond_3) & flat_np
    else:
        squeeze_diff_np = bb_upper_np - kc_upper_np
        consec_neg = _count_consecutive_neg_2d(ttms_np)
        entry_1 = (
            (_shift_2d(squeeze_diff_np, 1) < 0)
            & (squeeze_diff_np > 0)
            & (ttms_np > 0)
            & flat_np
        )
        entry_2 = (
            (_shift_2d(squeeze_diff_np, 1) < 0)
            & (squeeze_diff_np > 0)
            & (consec_neg > consecutive_neg_threshold)
        )
        entries_np = entry_1 | entry_2 | wvf_np

    return pd.DataFrame(entries_np, index=close.index, columns=close.columns)


def _compute_exits(close, high, low, atr_multiplier=1.9, atr_period=10, low_stop_lookback=5):
    atr_raw = vbt.IndicatorFactory.from_talib('ATR').run(high, low, close, timeperiod=atr_period)
    ATRTrailing = vbt.IndicatorFactory(
        input_names=['close', 'atr'],
        param_names=['atr_multiplier'],
        output_names=['atr_trailing'],
    ).from_apply_func(atr_trailing_nb)
    atr_sl = ATRTrailing.run(close, atr_raw.real, atr_multiplier=atr_multiplier)
    exits_df = close.vbt.crossed_below(atr_sl.atr_trailing)
    MIN = vbt.IndicatorFactory.from_talib('MIN')
    lowest_low = MIN.run(low, timeperiod=low_stop_lookback).real
    sl_stop_df = ((close - lowest_low) / close).clip(lower=0)
    return exits_df, sl_stop_df


_EXIT_PARAM_KEYS = frozenset({'atr_period', 'atr_multiplier', 'low_stop_lookback'})


class BreakoutTTMV1:
    def __init__(self, data, entry_version, *, use_kama_slope=True, init_cash=100.0, **param_overrides):
        if entry_version not in FIXED_TTM_PARAMS:
            raise ValueError(f'entry_version must be one of {sorted(FIXED_TTM_PARAMS)}, got {entry_version!r}')
        self.data = data
        self.entry_version = entry_version
        self.use_kama_slope = use_kama_slope
        self.init_cash = init_cash
        self._params: dict = {**FIXED_TTM_PARAMS[entry_version], **param_overrides}

    @property
    def param_dict(self) -> dict:
        return {'entry_version': self.entry_version, **self._params}

    def get_entries(self) -> pd.DataFrame:
        sig_kw = {k: v for k, v in self._params.items() if k not in _EXIT_PARAM_KEYS}
        return _compute_signals(
            self.data.close, self.data.high, self.data.low,
            entry_version=self.entry_version,
            use_kama_slope=self.use_kama_slope,
            **sig_kw,
        )

    def get_exits_and_stop(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        p = self._params
        return _compute_exits(
            self.data.close, self.data.high, self.data.low,
            atr_multiplier=p['atr_multiplier'],
            atr_period=p['atr_period'],
            low_stop_lookback=p['low_stop_lookback'],
        )

    def get_portfolio(self, **portfolio_kwargs) -> vbt.Portfolio:
        entries = self.get_entries()
        exits, sl_stop_df = self.get_exits_and_stop()
        kw = {
            'close': self.data.close,
            'entries': entries,
            'exits': exits,
            'sl_stop': sl_stop_df,
            'freq': '1d',
            'group_by': ['symbol'],
            'cash_sharing': False,
            'init_cash': self.init_cash,
        }
        kw.update(portfolio_kwargs)
        return vbt.Portfolio.from_signals(**kw)


# ── Original train_meta_label_models.py (local imports removed) ──────────────

@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _get_env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _gpu_kwargs(library: str) -> dict:
    """Return GPU-related constructor kwargs for the given library when USE_GPU is set."""
    if not USE_GPU:
        return {}
    if library == "xgb":
        return {"tree_method": "hist", "device": "cuda"}
    if library == "lgbm":
        return {"device": "gpu"}
    if library == "cat":
        return {"task_type": "GPU"}
    return {}


def _resolve_optuna_n_jobs(explicit: int | None = None) -> int:
    """Parallel trials for Optuna (-1 = all CPUs). Override with OPTUNA_N_JOBS or explicit."""
    if explicit is not None:
        return explicit
    raw = os.getenv("OPTUNA_N_JOBS", "-1").strip().lower()
    if raw in ("", "auto", "all"):
        return -1
    try:
        v = int(raw)
    except ValueError:
        return -1
    return -1 if v == 0 else v


class PurgedKFold:
    """Purged K-fold with embargo (module-level for picklable Optuna objectives)."""

    def __init__(self, n_splits=5, entry_dates=None, exit_dates=None, embargo_pct=0.01):
        self.n_splits = n_splits
        self.entry_dates = entry_dates.reset_index(drop=True)
        self.exit_dates = exit_dates.reset_index(drop=True)
        self.embargo_pct = embargo_pct

    def split(self, X, y=None, groups=None):
        n = len(X)
        indices = np.arange(n)
        embargo_n = int(n * self.embargo_pct)
        test_ranges = np.array_split(indices, self.n_splits)
        for test_idx in test_ranges:
            test_start_t = self.entry_dates.iloc[test_idx[0]]
            test_end_t = self.exit_dates.iloc[test_idx[-1]]
            train_mask = np.ones(n, dtype=bool)
            train_mask[test_idx] = False
            for i in indices[train_mask]:
                if (self.entry_dates.iloc[i] <= test_end_t) and (
                    self.exit_dates.iloc[i] >= test_start_t
                ):
                    train_mask[i] = False
            embargo_end = min(test_idx[-1] + 1 + embargo_n, n)
            train_mask[test_idx[-1] + 1 : embargo_end] = False
            train_idx = indices[train_mask]
            yield train_idx, test_idx

    def get_n_splits(self, X=None, y=None, groups=None):
        return self.n_splits


def _optuna_rows(arr: Any, idx: Any) -> Any:
    return arr.iloc[idx] if hasattr(arr, "iloc") else np.asarray(arr)[idx]


def _optuna_objective_xgb(
    trial: Any,
    *,
    X_train_scaled: Any,
    y_train: Any,
    train_weights: Any,
    purged_cv: PurgedKFold,
    scale_pos_weight: float,
    es_rounds: int,
    xgb_gpu_kwargs: dict[str, Any],
) -> float:
    from sklearn.metrics import roc_auc_score
    from xgboost import XGBClassifier

    params = {
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "n_estimators": 1000,
        "early_stopping_rounds": es_rounds,
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "max_depth": trial.suggest_int("max_depth", 3, 7),
        "min_child_weight": trial.suggest_int("min_child_weight", 1, 10),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
        "gamma": trial.suggest_float("gamma", 0, 5),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 1.0, log=True),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 1.0, log=True),
        "scale_pos_weight": scale_pos_weight,
        "random_state": 42,
        "verbosity": 0,
        **xgb_gpu_kwargs,
    }
    scores = []
    for train_idx, val_idx in purged_cv.split(X_train_scaled):
        m = XGBClassifier(**params)
        m.fit(
            _optuna_rows(X_train_scaled, train_idx),
            _optuna_rows(y_train, train_idx),
            sample_weight=_optuna_rows(train_weights, train_idx),
            eval_set=[
                (_optuna_rows(X_train_scaled, val_idx), _optuna_rows(y_train, val_idx))
            ],
            verbose=False,
        )
        proba = m.predict_proba(_optuna_rows(X_train_scaled, val_idx))[:, 1]
        scores.append(roc_auc_score(_optuna_rows(y_train, val_idx), proba))
    return float(np.mean(scores))


def _optuna_objective_lgbm(
    trial: Any,
    *,
    X_train_scaled: Any,
    y_train: Any,
    train_weights: Any,
    purged_cv: PurgedKFold,
    es_rounds: int,
    lgbm_gpu_kwargs: dict[str, Any],
) -> float:
    from lightgbm import LGBMClassifier
    from lightgbm import early_stopping as lgb_es
    from lightgbm import log_evaluation as lgb_log
    from sklearn.metrics import roc_auc_score

    params = {
        "objective": "binary",
        "metric": "auc",
        "n_estimators": 1000,
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "num_leaves": trial.suggest_int("num_leaves", 15, 63),
        "max_depth": trial.suggest_int("max_depth", 3, 7),
        "min_child_samples": trial.suggest_int("min_child_samples", 10, 80),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 1.0, log=True),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 1.0, log=True),
        "class_weight": "balanced",
        "random_state": 42,
        "verbosity": -1,
        **lgbm_gpu_kwargs,
    }
    scores = []
    for train_idx, val_idx in purged_cv.split(X_train_scaled):
        m = LGBMClassifier(**params)
        m.fit(
            _optuna_rows(X_train_scaled, train_idx),
            _optuna_rows(y_train, train_idx),
            sample_weight=_optuna_rows(train_weights, train_idx),
            eval_set=[
                (_optuna_rows(X_train_scaled, val_idx), _optuna_rows(y_train, val_idx))
            ],
            callbacks=[
                lgb_es(es_rounds, verbose=False),
                lgb_log(period=-1),
            ],
        )
        proba = m.predict_proba(_optuna_rows(X_train_scaled, val_idx))[:, 1]
        scores.append(roc_auc_score(_optuna_rows(y_train, val_idx), proba))
    return float(np.mean(scores))


def _optuna_objective_cat(
    trial: Any,
    *,
    X_train_scaled: Any,
    y_train: Any,
    train_weights: Any,
    purged_cv: PurgedKFold,
    es_rounds: int,
    cat_gpu_kwargs: dict[str, Any],
) -> float:
    from catboost import CatBoostClassifier
    from sklearn.metrics import roc_auc_score

    params = {
        "objective": "Logloss",
        "eval_metric": "AUC",
        "iterations": 1000,
        "early_stopping_rounds": es_rounds,
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "depth": trial.suggest_int("depth", 3, 7),
        "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1e-3, 10.0, log=True),
        "random_strength": trial.suggest_float("random_strength", 1e-3, 10.0, log=True),
        "bagging_temperature": trial.suggest_float("bagging_temperature", 0, 5),
        "auto_class_weights": "Balanced",
        "random_seed": 42,
        "verbose": False,
        **cat_gpu_kwargs,
    }
    scores = []
    for train_idx, val_idx in purged_cv.split(X_train_scaled):
        m = CatBoostClassifier(**params)
        m.fit(
            _optuna_rows(X_train_scaled, train_idx),
            _optuna_rows(y_train, train_idx),
            sample_weight=_optuna_rows(train_weights, train_idx),
            eval_set=(
                _optuna_rows(X_train_scaled, val_idx),
                _optuna_rows(y_train, val_idx),
            ),
        )
        proba = m.predict_proba(_optuna_rows(X_train_scaled, val_idx))[:, 1]
        scores.append(roc_auc_score(_optuna_rows(y_train, val_idx), proba))
    return float(np.mean(scores))


def _configure_runtime() -> None:
    cache_root = Path(os.getenv("META_LABEL_TASK_CACHE_DIR", "/tmp/meta-label-prefect-cache"))
    numba_cache_dir = cache_root / "numba"
    numba_cache_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("XDG_CACHE_HOME", str(cache_root))
    os.environ.setdefault("NUMBA_CACHE_DIR", str(numba_cache_dir))

    backend_path = "backend"
    for path in (str(PROJECT_ROOT), str(backend_path)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _artifact_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Expected artifact was not created: {path}")
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "modified_at": path.stat().st_mtime,
    }


def _serialize_model_metrics(model_metrics: dict[str, dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    serialized: dict[str, dict[str, float | int]] = {}
    for model_name, metrics in model_metrics.items():
        serialized[model_name] = {}
        for metric_name, value in metrics.items():
            if isinstance(value, (int, float)):
                serialized[model_name][metric_name] = value
            else:
                serialized[model_name][metric_name] = float(value)
    return serialized


def _format_metric_comparison(
    model_metrics: dict[str, dict[str, Any]],
    best_ensemble_name: str | None,
) -> str:
    rows = sorted(
        model_metrics.items(),
        key=lambda item: item[1].get("auc", float("-inf")),
        reverse=True,
    )
    lines = [
        "Final model comparison",
        "model                          auc     f1   precision  recall  brier  filt_sharpe  lift   thresh",
        "----------------------------  -----  -----  ---------  ------  -----  -----------  -----  ------",
    ]
    for model_name, metrics in rows:
        marker = "*" if model_name == best_ensemble_name else " "
        lines.append(
            f"{marker}{model_name[:27]:<27}  "
            f"{metrics.get('auc', 0):>5.3f}  "
            f"{metrics.get('f1', 0):>5.3f}  "
            f"{metrics.get('precision', 0):>9.3f}  "
            f"{metrics.get('recall', 0):>6.3f}  "
            f"{metrics.get('brier_score', 0):>5.3f}  "
            f"{metrics.get('filt_sharpe', 0):>11.3f}  "
            f"{metrics.get('sharpe_lift', 0):>5.3f}  "
            f"{metrics.get('opt_threshold', 0.50):>6.2f}"
        )
    if best_ensemble_name:
        lines.append(f"Best ensemble: {best_ensemble_name}")
    return "\n".join(lines)


def _collect_garbage() -> None:
    gc.collect()


# ── Cloudflare R2 helpers ────────────────────────────────────────────────────

R2_ENDPOINT = os.getenv(
    "R2_ENDPOINT_URL",
    "https://bf992ebd6b2d460f07db4868252c33a6.r2.cloudflarestorage.com",
)
R2_BUCKET = os.getenv("R2_BUCKET", "all-in-one-porffolio")
R2_OHLC_KEY = os.getenv("R2_OHLC_KEY", "data/stocks_data_latest.h5")
R2_MODELS_PREFIX = os.getenv("R2_MODELS_PREFIX", "models/")


def _r2_client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def _r2_download(r2_key: str, local_path: Path) -> None:
    client = _r2_client()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("R2 download  r2://%s/%s → %s", R2_BUCKET, r2_key, local_path)
    t0 = time.time()
    client.download_file(R2_BUCKET, r2_key, str(local_path))
    mb = local_path.stat().st_size / 1024 ** 2
    log.info("R2 download  done  %.1f MB  in %.1fs", mb, time.time() - t0)


def _r2_upload(local_path: Path, r2_key: str) -> None:
    client = _r2_client()
    mb = local_path.stat().st_size / 1024 ** 2
    log.info("R2 upload  %s (%.1f MB) → r2://%s/%s", local_path.name, mb, R2_BUCKET, r2_key)
    t0 = time.time()
    client.upload_file(str(local_path), R2_BUCKET, r2_key)
    log.info("R2 upload  done  in %.1fs", time.time() - t0)


def _load_watchlist_symbols(watchlist_path: str | Path | None = None) -> list[str]:
    import pandas as pd

    path = (
        Path(watchlist_path)
        if watchlist_path is not None
        else PROJECT_ROOT / "backend/models/watchlist.csv"
    )
    if not path.exists():
        if watchlist_path is not None:
            raise FileNotFoundError(f"watchlist file not found: {path}")
        return list(DEFAULT_WATCHLIST_SYMBOLS)
    watchlist_df = pd.read_csv(path, header=None)
    return (
        watchlist_df.iloc[:, 0]
        .dropna()
        .astype(str)
        .str.strip()
        .loc[lambda symbols: symbols != ""]
        .tolist()
    )


def load_ohlc_panel(
    output_dir: str | Path = PIPELINE_OUTPUT_DIR,
    refresh_cache: bool = False,
    cache_filename: str = "stocks_data_latest.h5",
    r2_key: str | None = None,
) -> Any:
    """Load the OHLC panel from local HDF cache, downloading from R2 on a miss.

    Returns a wide DataFrame with OHLC fields on level 0 and symbols on level 1.
    """
    import pandas as pd
    from dotenv import load_dotenv

    load_dotenv()

    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    cache_path = output_path / cache_filename
    store_key = "stocks"

    if cache_path.exists() and not refresh_cache:
        mb = cache_path.stat().st_size / 1024 ** 2
        log.info("OHLC cache hit  %s  (%.1f MB) — skipping R2 download", cache_path, mb)
        t0 = time.time()
        with pd.HDFStore(cache_path, mode="r") as store:
            panel = store[store_key]
        log.info("OHLC panel loaded  shape=%s  in %.1fs", panel.shape, time.time() - t0)
        return panel

    key = r2_key or R2_OHLC_KEY
    log.info("OHLC cache miss — fetching from R2  key=%s", key)
    _r2_download(key, cache_path)

    t0 = time.time()
    with pd.HDFStore(cache_path, mode="r") as store:
        panel = store[store_key]
    log.info("OHLC panel loaded  shape=%s  in %.1fs", panel.shape, time.time() - t0)
    return panel


def _build_feature_context_from_ohlc_panel(
    df: Any,
    watchlist_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Build the full feature frame and return internals needed by training-label generation."""
    import numpy as np
    import pandas as pd
    import vectorbt as vbt

    _t_feat_start = time.time()
    if watchlist_symbols is None:
        watchlist_symbols = _load_watchlist_symbols()
    stocks = df.loc[:, df.columns.get_level_values('symbol').isin(watchlist_symbols)]
    symbols_in_columns = stocks.columns.get_level_values(1)
    columns_to_keep_mask = symbols_in_columns != 'VNINDEX'
    stocks_exclude_vnindex = stocks.loc[:, columns_to_keep_mask]

    _panel_syms = df.columns.get_level_values('symbol').unique()
    _BENCH_PX_SYM = next((s for s in ('VNINDEX', 'VN30') if s in _panel_syms), None)

    # ── Raw arrays ──────────────────────────────────────────────────────────────
    close_df  = stocks_exclude_vnindex.close
    open_df   = stocks_exclude_vnindex.open
    high_df   = stocks_exclude_vnindex.high
    low_df    = stocks_exclude_vnindex.low
    volume_df = stocks_exclude_vnindex.volume

    index   = close_df.index
    symbols = close_df.columns

    log.info(
        "Feature pipeline  symbols=%d  dates=%d  bench=%s",
        len(symbols), len(index), _BENCH_PX_SYM,
    )

    close_2d  = close_df.to_numpy().astype(np.float64)
    open_2d   = open_df.to_numpy().astype(np.float64)
    high_2d   = high_df.to_numpy().astype(np.float64)
    low_2d    = low_df.to_numpy().astype(np.float64)
    volume_2d = volume_df.to_numpy().astype(np.float64)

    EPS = 1e-10

    def _df(arr):
        return pd.DataFrame(arr, index=index, columns=symbols)

    def _safe_log_ratio_df(num_df, denom_df):
        safe = denom_df.where(denom_df != 0, np.nan)
        return np.log1p(((num_df - safe) / safe).clip(lower=-1 + EPS))

    def _safe_log_ratio_2d(num, denom):
        safe = np.where(denom == 0, np.nan, denom)
        ratio = (num - safe) / safe
        return np.log1p(np.clip(ratio, -1 + EPS, None))

    # ── 1. RSI (5, 14) ──────────────────────────────────────────────────────────
    log.info("  [1/14] RSI 5,14")
    RSI = vbt.IndicatorFactory.from_talib('RSI')
    rsi_5_df  = RSI.run(close_df, timeperiod=5).real
    rsi_14_df = RSI.run(close_df, timeperiod=14).real

    # ── 2. MFI (21) ─────────────────────────────────────────────────────────────
    log.info("  [2/14] MFI 21")
    mfi_21_df = vbt.IndicatorFactory.from_talib('MFI').run(
        high_df, low_df, close_df, volume_df, timeperiod=21).real

    # ── 3. OBV ──────────────────────────────────────────────────────────────────
    log.info("  [3/14] OBV")
    obv_df = vbt.IndicatorFactory.from_talib('OBV').run(close_df, volume_df).real

    # ── 4. Log return ────────────────────────────────────────────────────────────
    log.info("  [4/14] Log return")
    log_return_df = np.log(close_df / close_df.shift(1))

    # ── 5. EMA distances (10, 20, 50, 200) ──────────────────────────────────────
    log.info("  [5/14] EMA distances 10,20,50,200")
    EMA = vbt.IndicatorFactory.from_talib('EMA')
    ema_10_df  = EMA.run(close_df, timeperiod=10).real
    ema_20_df  = EMA.run(close_df, timeperiod=20).real
    ema_50_df  = EMA.run(close_df, timeperiod=50).real
    ema_200_df = EMA.run(close_df, timeperiod=200).real

    ema_10_dist  = _safe_log_ratio_df(close_df, ema_10_df)
    ema_20_dist  = _safe_log_ratio_df(close_df, ema_20_df)
    ema_50_dist  = _safe_log_ratio_df(close_df, ema_50_df)
    ema_200_dist = _safe_log_ratio_df(close_df, ema_200_df)

    # ── 6. Volume MA ratio (10, 20) ──────────────────────────────────────────────
    log.info("  [6/14] Volume MA ratio 10,20")
    SMA = vbt.IndicatorFactory.from_talib('SMA')
    vol_ma_10_dist = _safe_log_ratio_df(volume_df, SMA.run(volume_df, timeperiod=10).real)
    vol_ma_20_dist = _safe_log_ratio_df(volume_df, SMA.run(volume_df, timeperiod=20).real)

    # ── 7. EFI z-score (Elder Force Index = close.diff * volume) ────────────────
    log.info("  [7/14] EFI z-score")
    efi_2d = np.nan_to_num((close_df.diff() * volume_df).to_numpy().astype(np.float64))
    efi_zscore_10_2d = zscore_nb(efi_2d, window=10)
    efi_zscore_20_2d = zscore_nb(efi_2d, window=20)

    # ── 8. AVWAP distances (anchored to rolling highest / lowest) ────────────────
    log.info("  [8/14] AVWAP distances (Numba, window=200)")
    avwap_hi_2d = avwap_func_nb(close_2d, high_2d, low_2d, volume_2d, is_highest=True,  window=200)
    avwap_lo_2d = avwap_func_nb(close_2d, high_2d, low_2d, volume_2d, is_highest=False, window=200)
    vwap_dist_hi_2d = _safe_log_ratio_2d(close_2d, avwap_hi_2d)
    vwap_dist_lo_2d = _safe_log_ratio_2d(close_2d, avwap_lo_2d)

    # ── 9. Relative strength vs benchmark (VNINDEX / VN30 / EW mean close) ─────
    log.info("  [9/14] Relative strength vs benchmark")
    if _BENCH_PX_SYM is not None:
        _bench_close_1d = stocks.close[_BENCH_PX_SYM].values.astype(np.float64)
    else:
        _bench_close_1d = np.nanmean(close_2d, axis=1)
    vnindex_2d = np.tile(_bench_close_1d.reshape(-1, 1), (1, close_2d.shape[1]))
    rs_10_2d, mrs_10_2d = relative_strength_nb(close_2d, vnindex_2d, window=10)
    rs_20_2d, mrs_20_2d = relative_strength_nb(close_2d, vnindex_2d, window=20)

    # ── 10. Cross-sectional MSR rank ─────────────────────────────────────────────
    log.info("  [10/14] MSR cross-sectional rank")
    msr_rank_10_2d = _df(mrs_10_2d).rank(axis=1, pct=True).to_numpy()
    msr_rank_20_2d = _df(mrs_20_2d).rank(axis=1, pct=True).to_numpy()

    # ── 11. Z-score of log return (10, 20) ───────────────────────────────────────
    log.info("  [11/14] Z-score log return")
    lr_2d = np.nan_to_num(log_return_df.to_numpy().astype(np.float64))
    zscore_lr_10_2d = zscore_nb(lr_2d, window=10)
    zscore_lr_20_2d = zscore_nb(lr_2d, window=20)

    # ── 12. Yang-Zhang volatility (10, 20) ───────────────────────────────────────
    log.info("  [12/14] Yang-Zhang volatility (Numba)")
    yz_vol_10_2d = yang_zhang_volatility_nb(close_2d, open_2d, high_2d, low_2d, window=10, periods=252).astype(np.float64)
    yz_vol_20_2d = yang_zhang_volatility_nb(close_2d, open_2d, high_2d, low_2d, window=20, periods=252).astype(np.float64)

    # ── 13. DC TMV — TTM Squeeze momentum value ────────────────────────────────
    log.info("  [13/14] TTM Squeeze momentum (DC TMV)")
    _diff, dc_tmv_raw = squeeze_ttm(
        close_df.to_numpy(), high_df.to_numpy(), low_df.to_numpy(),
        bb_period=10, bb_mult=1.2, bb_matype=3,
        kc_period=10, kc_mult=1.2,
        donichan_period=10, osc_smoothing_period=10,
    )
    if hasattr(dc_tmv_raw, 'to_numpy'):
        dc_tmv_2d = dc_tmv_raw.to_numpy()
    else:
        dc_tmv_2d = np.asarray(dc_tmv_raw)
    if dc_tmv_2d.ndim == 3:
        dc_tmv_2d = dc_tmv_2d[:, :, 0]

    # ── 14. Kalman-filter smoothed price → distance + z-scores ───────────────────
    log.info("  [14/14] Kalman filter + z-scores (Numba)")
    @nb.njit(parallel=True)
    def _kalman_2d(prices, obs_cov=1.0, trans_cov=0.01):
        T, S = prices.shape
        out = np.empty_like(prices)
        for j in nb.prange(S):
            x = prices[0, j]
            p = 1.0
            for i in range(T):
                p_pred = p + trans_cov
                gain   = p_pred / (p_pred + obs_cov)
                x      = x + gain * (prices[i, j] - x)
                p      = (1.0 - gain) * p_pred
                out[i, j] = x
        return out

    kf_2d          = _kalman_2d(close_2d)
    kf_dist_2d     = _safe_log_ratio_2d(close_2d, kf_2d)
    zscore_kf_10_2d = zscore_nb(kf_2d, window=10)
    zscore_kf_20_2d = zscore_nb(kf_2d, window=20)

    # ── Assemble features_df (date × symbol long form) ──────────────────────────
    _feat_arrays = {
        'rsi_window_5':           rsi_5_df.to_numpy(),
        'rsi_window_14':          rsi_14_df.to_numpy(),
        'mfi_21':                 mfi_21_df.to_numpy(),
        'obv':                    obv_df.to_numpy(),
        'log_return':             log_return_df.to_numpy(),
        'volume_threshold_ma_10': vol_ma_10_dist.to_numpy(),
        'volume_threshold_ma_20': vol_ma_20_dist.to_numpy(),
        'ema_10_distance':        ema_10_dist.to_numpy(),
        'ema_20_distance':        ema_20_dist.to_numpy(),
        'ema_50_distance':        ema_50_dist.to_numpy(),
        'ema_200_distance':       ema_200_dist.to_numpy(),
        'vwap_distance_highest':  vwap_dist_hi_2d,
        'vwap_distance_lowest':   vwap_dist_lo_2d,
        'efi_zscore_10':          efi_zscore_10_2d,
        'efi_zscore_20':          efi_zscore_20_2d,
        'rs_10':                  rs_10_2d,
        'rs_20':                  rs_20_2d,
        'mrs_10':                 mrs_10_2d,
        'mrs_20':                 mrs_20_2d,
        'msr_rank_10':            msr_rank_10_2d,
        'msr_rank_20':            msr_rank_20_2d,
        'zscore_10_log_return':   zscore_lr_10_2d,
        'zscore_20_log_return':   zscore_lr_20_2d,
        'yz_vol_10':              yz_vol_10_2d,
        'yz_vol_20':              yz_vol_20_2d,
        'dc_tmv':                 dc_tmv_2d,
        'kf_distance':            kf_dist_2d,
        'zscore_kf_10':           zscore_kf_10_2d,
        'zscore_kf_20':           zscore_kf_20_2d,
        'close':                  close_2d,
    }

    features_df = pd.concat(
        [_df(arr).stack().rename(feat) for feat, arr in _feat_arrays.items()],
        axis=1,
    )
    features_df.index.names = ['date', 'symbol']
    log.info("  Base features assembled  shape=%s", features_df.shape)

    # ── Autopsy-Driven Feature Additions ────────────────────────────────────────
    log.info("  Autopsy-driven features (vol regime, overextension, breakout quality)")
    yz_vol_zscore_10_2d = zscore_nb(yz_vol_10_2d, window=63)
    yz_vol_zscore_20_2d = zscore_nb(yz_vol_20_2d, window=63)

    yz_vol_accel_2d = np.where(yz_vol_20_2d > 1e-9,
                                yz_vol_10_2d / (yz_vol_20_2d + 1e-9), np.nan)

    mrs_overext_ratio_2d = np.where(np.abs(mrs_20_2d) > 1e-9,
                                     mrs_10_2d / (np.abs(mrs_20_2d) + 1e-9), np.nan)

    _ema10_arr = ema_10_dist.to_numpy()
    _ema50_arr = ema_50_dist.to_numpy()
    ema_near_far_ratio_2d = np.where(np.abs(_ema50_arr) > 1e-9,
                                      _ema10_arr / (np.abs(_ema50_arr) + 1e-9), np.nan)

    rsi_rank_14_2d = _df(rsi_14_df.to_numpy()).rank(axis=1, pct=True).to_numpy()

    _vol_sma5 = SMA.run(volume_df, timeperiod=5).real.to_numpy()
    vol_spike_2d = _safe_log_ratio_2d(volume_2d, _vol_sma5)

    _day_range = high_2d - low_2d
    close_position_2d = np.where(_day_range > 1e-9, (close_2d - low_2d) / _day_range, 0.5)

    dist_20d_high_2d = _safe_log_ratio_2d(close_2d, close_df.rolling(20).max().to_numpy())
    dist_60d_high_2d = _safe_log_ratio_2d(close_2d, close_df.rolling(60).max().to_numpy())

    # ── VNINDEX Market Regime Features ───────────────────────────────────────────
    if _BENCH_PX_SYM is not None:
        _vn_px = stocks['close'][_BENCH_PX_SYM]
    else:
        _vn_px = pd.Series(np.nanmean(close_2d, axis=1), index=close_df.index)
    _vn_px = _vn_px.reindex(close_df.index, method='ffill')

    _vn_ema50  = _vn_px.ewm(span=50,  adjust=False).mean()
    _vn_ema200 = _vn_px.ewm(span=200, adjust=False).mean()
    _vn_vol20  = _vn_px.pct_change().rolling(20).std()

    _vn_df = pd.DataFrame({
        'vnindex_above_ema50':  (_vn_px > _vn_ema50).astype(float),
        'vnindex_above_ema200': (_vn_px > _vn_ema200).astype(float),
        'vnindex_ret_5d':       _vn_px.pct_change(5),
        'vnindex_ret_20d':      _vn_px.pct_change(20),
        'vnindex_vol_20d':      _vn_vol20,
        'vnindex_vol_zscore':   ((_vn_vol20 - _vn_vol20.rolling(252).mean())
                                 / (_vn_vol20.rolling(252).std() + 1e-9)),
        'vnindex_drawdown':     _vn_px / _vn_px.rolling(252).max() - 1,
    }, index=_vn_px.index)

    _extra_feat_arrays = {
        'yz_vol_zscore_10':   yz_vol_zscore_10_2d,
        'yz_vol_zscore_20':   yz_vol_zscore_20_2d,
        'yz_vol_accel':       yz_vol_accel_2d,
        'mrs_overext_ratio':  mrs_overext_ratio_2d,
        'ema_near_far_ratio': ema_near_far_ratio_2d,
        'rsi_rank_14':        rsi_rank_14_2d,
        'vol_spike':          vol_spike_2d,
        'close_position':     close_position_2d,
        'dist_20d_high':      dist_20d_high_2d,
        'dist_60d_high':      dist_60d_high_2d,
    }
    _extra_features = pd.concat(
        [_df(arr).stack().rename(feat) for feat, arr in _extra_feat_arrays.items()],
        axis=1,
    )
    _extra_features.index.names = ['date', 'symbol']

    _vn_long = _vn_df.reindex(features_df.index.get_level_values('date'))
    _vn_long.index = features_df.index

    features_df = pd.concat([features_df, _extra_features, _vn_long], axis=1)

    _ratio_fills = {
        'yz_vol_accel':       1.0,
        'mrs_overext_ratio':  1.0,
        'ema_near_far_ratio': 1.0,
        'yz_vol_zscore_10':   0.0,
        'yz_vol_zscore_20':   0.0,
    }
    for col, fill in _ratio_fills.items():
        if col in features_df.columns:
            features_df[col] = features_df[col].fillna(fill)

    # ── VN Priority Features ─────────────────────────────────────────────────────
    log.info("  VN priority features (vol pctile, ATR, range pos, trend, beta)")
    rv_20_2d = (log_return_df.rolling(20).std() * np.sqrt(252)).to_numpy()

    _yz10 = yz_vol_10_2d
    vol_pctile_2d = np.full_like(_yz10, np.nan)
    _W_vol = 252
    for _t in range(_W_vol, _yz10.shape[0]):
        vol_pctile_2d[_t] = (_yz10[_t - _W_vol:_t] < _yz10[_t]).mean(axis=0)

    ATR = vbt.IndicatorFactory.from_talib('ATR')
    atr_pct_2d = (ATR.run(high_df, low_df, close_df, timeperiod=14).real
                  / close_df.replace(0, np.nan)).to_numpy()

    _h60 = high_df.rolling(60).max().to_numpy()
    _l60 = low_df.rolling(60).min().to_numpy()
    _r60 = _h60 - _l60
    range_pos_60_2d = np.where(_r60 > 1e-9, (close_2d - _l60) / _r60, 0.5)

    dist_from_high_60_2d = _safe_log_ratio_2d(close_2d, _h60)

    trend_20_2d = np.log(close_df / close_df.shift(20)).to_numpy()

    _sma10  = SMA.run(close_df, timeperiod=10).real.to_numpy()
    _sma20  = SMA.run(close_df, timeperiod=20).real.to_numpy()
    _sma50  = SMA.run(close_df, timeperiod=50).real.to_numpy()
    _sma200 = SMA.run(close_df, timeperiod=200).real.to_numpy()
    sma_alignment_2d = (
        (close_2d > _sma10).astype(float) + (close_2d > _sma20).astype(float) +
        (close_2d > _sma50).astype(float) + (close_2d > _sma200).astype(float)
    ) / 4.0

    _vol_std20 = volume_df.rolling(20).std().to_numpy()
    vol_zscore_2d = np.where(
        _vol_std20 > 1e-9,
        (volume_2d - volume_df.rolling(20).mean().to_numpy()) / _vol_std20,
        0.0,
    )

    dollar_vol_pctile_2d = (
        (close_df * volume_df).rolling(20).mean()
        .rank(axis=1, pct=True)
        .to_numpy()
    )

    _vn_r1 = _vn_px.pct_change().reindex(close_df.index, method='ffill').values
    _stk_r1 = close_df.pct_change().to_numpy()
    _stk_r20 = close_df.pct_change(20).to_numpy()
    _vn_r20  = _vn_px.pct_change(20).reindex(close_df.index, method='ffill').values

    rs_vnindex_2d    = _stk_r20 - _vn_r20.reshape(-1, 1)
    corr_vnindex_2d  = np.full_like(close_2d, np.nan)
    beta_60d_2d      = np.full_like(close_2d, np.nan)

    _Wc = 60
    for _t in range(_Wc, close_2d.shape[0]):
        _vn_w  = _vn_r1[_t - _Wc:_t]
        _stk_w = _stk_r1[_t - _Wc:_t, :]
        _vn_m  = _vn_w.mean()
        _stk_m = _stk_w.mean(axis=0)
        _vn_d  = _vn_w - _vn_m
        _cov   = (_vn_d.reshape(-1, 1) * (_stk_w - _stk_m)).mean(axis=0)
        _var   = float((_vn_d ** 2).mean()) + 1e-9
        corr_vnindex_2d[_t] = _cov / (np.sqrt(_var) * (_stk_w.std(axis=0) + 1e-9))
        beta_60d_2d[_t]     = _cov / _var

    _vn_priority_arrays = {
        'rv_20':             rv_20_2d,
        'vol_pctile':        vol_pctile_2d,
        'atr_pct':           atr_pct_2d,
        'range_pos_60':      range_pos_60_2d,
        'dist_from_high_60': dist_from_high_60_2d,
        'trend_20':          trend_20_2d,
        'sma_alignment':     sma_alignment_2d,
        'vol_zscore':        vol_zscore_2d,
        'dollar_vol_pctile': dollar_vol_pctile_2d,
        'rs_vnindex':        rs_vnindex_2d,
        'corr_vnindex':      corr_vnindex_2d,
        'beta_60d':          beta_60d_2d,
    }
    _vn_prio_df = pd.concat(
        [_df(arr).stack().rename(feat) for feat, arr in _vn_priority_arrays.items()],
        axis=1,
    )
    _vn_prio_df.index.names = ['date', 'symbol']
    features_df = pd.concat([features_df, _vn_prio_df], axis=1)

    _fills = {'range_pos_60': 0.5, 'sma_alignment': 0.5,
               'vol_zscore': 0.0, 'corr_vnindex': 0.0, 'beta_60d': 1.0}
    for _c, _v in _fills.items():
        features_df[_c] = features_df[_c].fillna(_v)

    # ── GKYZ for symbols (multiple windows) ─────────────────────────────────────
    log.info("  GKYZ volatility windows=%s (symbols + benchmark)", [10, 20])
    _gkyz_windows = [10, 20]
    _gkyz_sym_arrays = {}

    for _w in _gkyz_windows:
        _gkyz_sym_2d = np.full_like(close_2d, np.nan)
        for _col in range(close_2d.shape[1]):
            try:
                _gkyz_sym_2d[:, _col] = calculate_gkyz_volatility(
                    open_2d[:, _col],
                    high_2d[:, _col],
                    low_2d[:, _col],
                    close_2d[:, _col],
                    window=_w,
                    normalize=True,
                )
            except Exception:
                pass
        _gkyz_sym_arrays[f'gkyz_vol_{_w}'] = _gkyz_sym_2d

    # ── GKYZ for benchmark index ─────────────────────────────────────────────────
    _gkyz_vn_dict = {}
    _vn_ohlc = None
    if _BENCH_PX_SYM is not None:
        for _panel in (stocks, df):
            for _lev in ('symbol', -1):
                try:
                    _vn_ohlc = _panel.xs(_BENCH_PX_SYM, axis=1, level=_lev)[['open', 'high', 'low', 'close']]
                    break
                except (KeyError, ValueError, TypeError, IndexError):
                    continue
            if _vn_ohlc is not None:
                break

    if _vn_ohlc is not None:
        _vn_open_1d  = _vn_ohlc['open'].values.astype(np.float64)
        _vn_high_1d  = _vn_ohlc['high'].values.astype(np.float64)
        _vn_low_1d   = _vn_ohlc['low'].values.astype(np.float64)
        _vn_close_1d = _vn_ohlc['close'].values.astype(np.float64)
        for _w in _gkyz_windows:
            try:
                _gkyz_vn_1d = calculate_gkyz_volatility(
                    _vn_open_1d, _vn_high_1d, _vn_low_1d, _vn_close_1d,
                    window=_w, normalize=True,
                )
                _gkyz_vn_dict[f'vnindex_gkyz_vol_{_w}'] = _gkyz_vn_1d
            except Exception:
                pass
    else:
        _vn_ohlc = pd.DataFrame(index=df.index)
        for _w in _gkyz_windows:
            _gkyz_vn_dict[f'vnindex_gkyz_vol_{_w}'] = np.full(len(df.index), 0.5, dtype=np.float64)

    _gkyz_sym_df = pd.concat(
        [_df(arr).stack().rename(feat) for feat, arr in _gkyz_sym_arrays.items()],
        axis=1,
    )
    _gkyz_sym_df.index.names = ['date', 'symbol']
    features_df = pd.concat([features_df, _gkyz_sym_df], axis=1)

    _vn_gkyz_df = pd.DataFrame(_gkyz_vn_dict, index=_vn_ohlc.index)
    _vn_gkyz_long = _vn_gkyz_df.reindex(features_df.index.get_level_values('date'))
    _vn_gkyz_long.index = features_df.index
    features_df = pd.concat([features_df, _vn_gkyz_long], axis=1)

    for _sym in symbols:
        for _col in _gkyz_sym_arrays.keys():
            sym_mask = features_df.index.get_level_values('symbol') == _sym
            features_df.loc[sym_mask, _col] = (
                features_df.loc[sym_mask, _col]
                .bfill()
                .fillna(0.5)
            )

    for _col in _gkyz_vn_dict.keys():
        features_df[_col] = features_df[_col].bfill().fillna(0.5)

    feature_dates = pd.to_datetime(features_df.index.get_level_values('date'))
    features_df['dow_sin'] = np.sin(2 * np.pi * feature_dates.dayofweek / 5)
    features_df['dow_cos'] = np.cos(2 * np.pi * feature_dates.dayofweek / 5)
    features_df['month_sin'] = np.sin(2 * np.pi * feature_dates.month / 12)
    features_df['month_cos'] = np.cos(2 * np.pi * feature_dates.month / 12)
    features_df['is_quarter_end'] = (
        (feature_dates.month % 3 == 0) &
        (feature_dates.day > 20)
    ).astype(int)

    log.info(
        "Feature context done  final shape=%s  cols=%d  elapsed=%.1fs",
        features_df.shape, len(features_df.columns), time.time() - _t_feat_start,
    )

    return {
        "features_df": features_df,
        "stocks": stocks,
        "stocks_exclude_vnindex": stocks_exclude_vnindex,
        "benchmark_symbol": _BENCH_PX_SYM,
    }


def build_features_from_ohlc_panel(
    df: Any,
    watchlist_symbols: list[str] | None = None,
) -> Any:
    """Build inference-ready features from an already-loaded OHLC panel."""
    return _build_feature_context_from_ohlc_panel(df, watchlist_symbols)["features_df"]


def _run_feature_pipeline_impl(refresh_stock_cache: bool = False) -> dict[str, Any]:
    """Build features, trades, and training labels in memory."""
    import json
    import numpy as np
    import pandas as pd

    log.info("Feature pipeline  start  refresh_cache=%s", refresh_stock_cache)
    df = load_ohlc_panel(output_dir=Path.cwd(), refresh_cache=refresh_stock_cache)
    log.info("Building feature context ...")
    feature_context = _build_feature_context_from_ohlc_panel(df)
    features_df = feature_context["features_df"]
    stocks_exclude_vnindex = feature_context["stocks_exclude_vnindex"]

    MS_POSITION_BUDGET = 100.0

    total_trades = pd.DataFrame()
    total_open_trades = pd.DataFrame()

    log.info("Running TTM breakout strategies ...")
    for ver in FIXED_TTM_PARAMS:
        if ver == 'v3':
            continue

        log.info("  Strategy version=%s", ver)
        strategy = BreakoutTTMV1(
            stocks_exclude_vnindex,
            ver,
            init_cash=MS_POSITION_BUDGET,
        )
        param_dict = strategy.param_dict
        portfolio = strategy.get_portfolio()

        trades = pd.DataFrame(portfolio.trades.records)
        trades['metadata'] = json.dumps(param_dict)
        open_trade = pd.DataFrame(portfolio.trades.open.records)
        open_trade['metadata'] = json.dumps(param_dict)

        log.info("    closed=%d  open=%d", len(trades), len(open_trade))
        total_trades = pd.concat([total_trades, trades])
        total_open_trades = pd.concat([total_open_trades, open_trade])

    total_trades['type'] = 'closed_trades'
    total_open_trades['type'] = 'open_trades'

    open_trade_keys = pd.MultiIndex.from_frame(total_open_trades[['col', 'entry_idx']])
    total_trade_keys = pd.MultiIndex.from_frame(total_trades[['col', 'entry_idx']])
    mask = ~total_trade_keys.isin(open_trade_keys)
    filtered_total_trades = total_trades[mask]

    all_trades_df = pd.concat([filtered_total_trades, total_open_trades])
    all_trades_df = (
        all_trades_df
        .drop_duplicates(subset=['col', 'entry_idx'], keep='first')
        .reset_index(drop=True)
    )
    all_trades_df['symbol'] = all_trades_df.apply(
        lambda x: stocks_exclude_vnindex.close.columns[x['col']], axis=1)
    all_trades_df['entry_date'] = all_trades_df.apply(
        lambda x: stocks_exclude_vnindex.index[x['entry_idx']], axis=1)
    all_trades_df['exit_date'] = all_trades_df.apply(
        lambda x: stocks_exclude_vnindex.index[min(int(x['exit_idx']), len(stocks_exclude_vnindex) - 1)]
                  if pd.notna(x['exit_idx']) else stocks_exclude_vnindex.index[-1],
        axis=1,
    )
    all_trades_df['date'] = all_trades_df['entry_date']
    all_trades_df.sort_values(by='entry_date', ascending=False, inplace=True)

    ROUND_TRIP_COST   = 0.003
    MIN_RETURN_EDGE   = 0.005
    LABEL_THRESHOLD   = ROUND_TRIP_COST + MIN_RETURN_EDGE

    closed_trades = all_trades_df[all_trades_df['type'] == 'closed_trades'].copy()
    closed_trades['net_return'] = closed_trades['return'] - ROUND_TRIP_COST
    closed_trades['Y'] = (closed_trades['net_return'] > MIN_RETURN_EDGE).astype(int)
    closed_trades = closed_trades.sort_values('entry_date').reset_index(drop=True)
    features_df_reset = features_df.reset_index()

    SAFE_FEATURE_COLS = [
        c for c in features_df_reset.columns
        if not c.startswith('next_') and c not in ['open', 'high', 'low', 'close', 'volume']
    ]

    training_df = pd.merge(
        closed_trades[['symbol', 'date', 'entry_date', 'exit_date',
                       'return', 'net_return', 'Y']],
        features_df_reset[SAFE_FEATURE_COLS],
        left_on=['date', 'symbol'],
        right_on=['date', 'symbol'],
        how='inner',
    )

    training_df = training_df.sort_values('entry_date').reset_index(drop=True)
    training_df = training_df.dropna()
    log.info(
        "Training df  rows=%d  positives=%d (%.1f%%)  date_range=%s→%s",
        len(training_df),
        int(training_df['Y'].sum()),
        float(training_df['Y'].mean()) * 100,
        str(training_df['entry_date'].min())[:10],
        str(training_df['entry_date'].max())[:10],
    )
    training_feature_columns = [
        'rsi_window_5', 'rsi_window_14', 'mfi_21',
        'obv', 'volume_threshold_ma_10', 'volume_threshold_ma_20',
        'efi_zscore_10', 'efi_zscore_20',
        'ema_10_distance', 'ema_20_distance', 'ema_50_distance', 'ema_200_distance',
        'kf_distance', 'zscore_kf_10', 'zscore_kf_20',
        'vwap_distance_highest', 'vwap_distance_lowest',
        'mrs_10', 'mrs_20', 'rs_10', 'rs_20',
        'msr_rank_10', 'msr_rank_20',
        'zscore_10_log_return', 'zscore_20_log_return',
        'yz_vol_10', 'yz_vol_20',
        'dc_tmv',
        'yz_vol_zscore_10', 'yz_vol_zscore_20',
        'yz_vol_accel',
        'mrs_overext_ratio',
        'ema_near_far_ratio',
        'rsi_rank_14',
        'vol_spike',
        'close_position',
        'dist_20d_high',
        'dist_60d_high',
        'rv_20', 'vol_pctile', 'atr_pct',
        'range_pos_60', 'dist_from_high_60',
        'trend_20', 'sma_alignment',
        'vol_zscore', 'dollar_vol_pctile',
        'rs_vnindex', 'corr_vnindex', 'beta_60d',
        'gkyz_vol_10', 'gkyz_vol_20',
        'vnindex_gkyz_vol_10', 'vnindex_gkyz_vol_20',
        'dow_sin', 'dow_cos', 'month_sin', 'month_cos', 'is_quarter_end',
    ]

    training_feature_columns = [c for c in training_feature_columns if c in training_df.columns]

    return locals()


def _run_training_pipeline_impl(
    training_df: Any,
    training_feature_columns: list[str],
    optuna_n_jobs: int | None = None,
) -> dict[str, Any]:
    """Train base models, ensembles, and evaluation artifacts from in-memory features."""
    import json
    import warnings
    import joblib
    import numpy as np
    import pandas as pd
    from datetime import datetime

    _t_train_start = time.time()
    warnings.filterwarnings('ignore')
    training_df = training_df.copy()
    training_df['entry_date'] = pd.to_datetime(training_df['entry_date'])
    training_df['exit_date']  = pd.to_datetime(training_df['exit_date'])
    training_df = training_df.sort_values('entry_date').reset_index(drop=True)

    training_feature_columns = [c for c in training_feature_columns if c in training_df.columns]
    log.info("Training pipeline  rows=%d  features=%d", len(training_df), len(training_feature_columns))
    TEST_MONTHS = int(os.getenv("META_LABEL_TEST_MONTHS", "12"))
    EMBARGO_DAYS = int(os.getenv("META_LABEL_EMBARGO_DAYS", "10"))

    max_entry_date = training_df['entry_date'].max()
    train_end_date = max_entry_date - pd.DateOffset(months=TEST_MONTHS)
    train_mask = training_df['entry_date'] <= train_end_date
    embargo_start = train_end_date
    embargo_end = embargo_start + pd.Timedelta(days=EMBARGO_DAYS)
    test_mask = training_df['entry_date'] > embargo_end
    if not train_mask.any() or not test_mask.any():
        raise ValueError(
            "Dynamic train/test split produced an empty partition. "
            "Adjust META_LABEL_TEST_MONTHS or META_LABEL_EMBARGO_DAYS."
        )

    split_metadata = {
        "max_entry_date": str(max_entry_date.date()),
        "train_end_date": str(train_end_date.date()),
        "embargo_end": str(embargo_end.date()),
        "test_months": TEST_MONTHS,
        "embargo_days": EMBARGO_DAYS,
    }
    log.info(
        "Train/test split  train=%d  test=%d  embargo_end=%s",
        int(train_mask.sum()), int(test_mask.sum()), str(embargo_end.date()),
    )

    X_train = training_df.loc[train_mask, training_feature_columns].copy()
    y_train = training_df.loc[train_mask, 'Y'].copy()
    X_test  = training_df.loc[test_mask,  training_feature_columns].copy()
    y_test  = training_df.loc[test_mask,  'Y'].copy()
    returns_test = training_df.loc[test_mask, 'net_return'].copy()
    dates_train  = training_df.loc[train_mask, 'entry_date'].copy()
    dates_test   = training_df.loc[test_mask,  'entry_date'].copy()
    exit_dates_train = training_df.loc[train_mask, 'exit_date'].copy()
    X_production = training_df[training_feature_columns].copy()
    y_production = training_df['Y'].copy()
    dates_production = training_df['entry_date'].copy()
    exit_dates_production = training_df['exit_date'].copy()

    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    X_train_scaled = pd.DataFrame(
        scaler.fit_transform(X_train),
        columns=X_train.columns,
        index=X_train.index,
    )
    X_test_scaled = pd.DataFrame(
        scaler.transform(X_test),
        columns=X_test.columns,
        index=X_test.index,
    )
    production_scaler = StandardScaler()
    X_production_scaled = pd.DataFrame(
        production_scaler.fit_transform(X_production),
        columns=X_production.columns,
        index=X_production.index,
    )

    def compute_sample_uniqueness(entry_dates: pd.Series, exit_dates: pd.Series) -> np.ndarray:
        n = len(entry_dates)
        entries = entry_dates.values
        exits   = exit_dates.values
        overlaps = np.zeros(n)
        for i in range(n):
            overlaps[i] = ((entries <= exits[i]) & (exits >= entries[i])).sum()
        weights = 1.0 / np.maximum(overlaps, 1)
        return weights / weights.mean()

    train_weights = compute_sample_uniqueness(
        dates_train.reset_index(drop=True),
        exit_dates_train.reset_index(drop=True),
    )
    production_weights = compute_sample_uniqueness(
        dates_production.reset_index(drop=True),
        exit_dates_production.reset_index(drop=True),
    )

    purged_cv = PurgedKFold(
        n_splits=5,
        entry_dates=dates_train,
        exit_dates=exit_dates_train,
        embargo_pct=0.01,
    )
    from sklearn.metrics import (
        roc_auc_score, precision_score, recall_score, f1_score,
        brier_score_loss,
    )

    def evaluate_meta_model(y_true, y_proba, returns, threshold=0.55):
        y_pred = (y_proba >= threshold).astype(int)
        take_mask = y_pred == 1
        base_returns     = returns
        filtered_returns = returns[take_mask]

        def sharpe(r):
            return r.mean() / (r.std() + 1e-9) * np.sqrt(252) if len(r) > 1 else 0

        metrics = {
            'auc':              roc_auc_score(y_true, y_proba),
            'brier_score':      brier_score_loss(y_true, y_proba),
            'precision':        precision_score(y_true, y_pred, zero_division=0),
            'recall':           recall_score(y_true, y_pred, zero_division=0),
            'f1':               f1_score(y_true, y_pred, zero_division=0),
            'trades_taken':     int(take_mask.sum()),
            'trades_filtered':  int((~take_mask).sum()),
            'pct_filtered':     float((~take_mask).mean()),
            'base_mean_ret':    float(base_returns.mean()),
            'filt_mean_ret':    float(filtered_returns.mean()) if len(filtered_returns) else 0,
            'base_sharpe':      sharpe(base_returns),
            'filt_sharpe':      sharpe(filtered_returns) if len(filtered_returns) > 1 else 0,
            'opt_threshold':    float(threshold),
        }
        metrics['sharpe_lift'] = metrics['filt_sharpe'] - metrics['base_sharpe']
        return metrics

    def find_optimal_threshold(
        y_true, y_proba, returns,
        lo: float = 0.30, hi: float = 0.80, step: float = 0.01,
        min_trades: int = 20,
        metric: str = 'filt_sharpe',
    ) -> float:
        """Scan thresholds [lo, hi] and return the one maximising `metric`."""
        best_t, best_score = 0.50, float('-inf')
        t = lo
        while t <= hi + 1e-9:
            mask = y_proba >= t
            if mask.sum() < min_trades:
                t = round(t + step, 4)
                continue
            filt = returns[mask]
            if len(filt) < 2:
                t = round(t + step, 4)
                continue
            sharpe = filt.mean() / (filt.std() + 1e-9) * np.sqrt(252)
            if metric == 'filt_sharpe':
                score = sharpe
            elif metric == 'precision':
                score = float(precision_score(y_true, mask.astype(int), zero_division=0))
            elif metric == 'f1':
                score = float(f1_score(y_true, mask.astype(int), zero_division=0))
            else:
                score = sharpe
            if score > best_score:
                best_score, best_t = score, round(t, 4)
            t = round(t + step, 4)
        return best_t

    import optuna
    from xgboost import XGBClassifier

    n_pos = (y_train == 1).sum()
    n_neg = (y_train == 0).sum()
    scale_pos_weight = float(n_neg / max(n_pos, 1))

    ES_ROUNDS = 50

    n_optuna = _resolve_optuna_n_jobs(optuna_n_jobs)
    log.info(
        "Optuna parallel  n_jobs=%s  (OPTUNA_N_JOBS env or --optuna-jobs; -1 = all CPUs)",
        n_optuna,
    )
    if USE_GPU and n_optuna != 1:
        log.warning(
            "USE_GPU with Optuna n_jobs!=1: multiple workers may contend on one GPU; "
            "use --optuna-jobs 1 if you see OOM or slowdowns."
        )

    xgb_kw = _gpu_kwargs("xgb")
    lgbm_kw = _gpu_kwargs("lgbm")
    cat_kw = _gpu_kwargs("cat")

    log.info("[1/3] XGBoost — Optuna HPO  n_trials=80  n_jobs=%s", n_optuna)
    sampler = optuna.samplers.TPESampler(seed=42)
    study_xgb = optuna.create_study(direction="maximize", sampler=sampler)
    study_xgb.optimize(
        partial(
            _optuna_objective_xgb,
            X_train_scaled=X_train_scaled,
            y_train=y_train,
            train_weights=train_weights,
            purged_cv=purged_cv,
            scale_pos_weight=scale_pos_weight,
            es_rounds=ES_ROUNDS,
            xgb_gpu_kwargs=xgb_kw,
        ),
        n_trials=80,
        n_jobs=n_optuna,
        show_progress_bar=False,
    )
    log.info("      XGBoost HPO done  best_auc=%.4f  params=%s", study_xgb.best_value, study_xgb.best_params)

    _es_cut = int(len(X_train_scaled) * 0.80)
    X_es,  y_es  = X_train_scaled.iloc[:_es_cut], y_train.iloc[:_es_cut]
    X_cal, y_cal = X_train_scaled.iloc[_es_cut:], y_train.iloc[_es_cut:]
    w_es = train_weights[:_es_cut]

    log.info("      XGBoost — final fit")
    final_xgb = XGBClassifier(
        **study_xgb.best_params,
        objective='binary:logistic',
        eval_metric='auc',
        n_estimators=1000,
        early_stopping_rounds=ES_ROUNDS,
        scale_pos_weight=scale_pos_weight,
        random_state=42,
        verbosity=0,
        **_gpu_kwargs('xgb'),
    )
    final_xgb.fit(
        X_es, y_es,
        sample_weight=w_es,
        eval_set=[(X_cal, y_cal)],
        verbose=False,
    )

    _raw_xgb = final_xgb.predict_proba(X_test_scaled)[:, 1]
    _prior_pos = y_train.mean()
    _spw = scale_pos_weight
    y_proba_xgb = _raw_xgb / (_raw_xgb + (1.0 - _raw_xgb) / _spw)

    metrics_xgb = evaluate_meta_model(y_test, y_proba_xgb, returns_test, threshold=0.50)
    log.info("      XGBoost holdout  auc=%.4f  precision=%.3f  recall=%.3f  filt_sharpe=%.3f",
             metrics_xgb['auc'], metrics_xgb['precision'], metrics_xgb['recall'], metrics_xgb['filt_sharpe'])
    os.makedirs(MODELS_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d')

    from lightgbm import LGBMClassifier
    from lightgbm import early_stopping as lgb_es, log_evaluation as lgb_log

    log.info("[2/3] LightGBM — Optuna HPO  n_trials=80  n_jobs=%s", n_optuna)
    study_lgbm = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=42))
    study_lgbm.optimize(
        partial(
            _optuna_objective_lgbm,
            X_train_scaled=X_train_scaled,
            y_train=y_train,
            train_weights=train_weights,
            purged_cv=purged_cv,
            es_rounds=ES_ROUNDS,
            lgbm_gpu_kwargs=lgbm_kw,
        ),
        n_trials=80,
        n_jobs=n_optuna,
        show_progress_bar=False,
    )
    log.info("      LightGBM HPO done  best_auc=%.4f", study_lgbm.best_value)
    log.info("      LightGBM — final fit")
    final_lgbm = LGBMClassifier(
        **study_lgbm.best_params,
        objective='binary',
        n_estimators=1000,
        class_weight='balanced',
        random_state=42,
        verbosity=-1,
        **_gpu_kwargs('lgbm'),
    )
    final_lgbm.fit(
        X_es, y_es,
        sample_weight=w_es,
        eval_set=[(X_cal, y_cal)],
        callbacks=[lgb_es(ES_ROUNDS, verbose=False), lgb_log(period=-1)],
    )

    _raw_lgbm = final_lgbm.predict_proba(X_test_scaled)[:, 1]
    _prior_pos = y_train.mean()
    _class_ratio = (1.0 - _prior_pos) / _prior_pos
    y_proba_lgbm = _raw_lgbm / (_raw_lgbm + (1.0 - _raw_lgbm) / _class_ratio)

    metrics_lgbm = evaluate_meta_model(y_test, y_proba_lgbm, returns_test, threshold=0.50)
    log.info("      LightGBM holdout  auc=%.4f  precision=%.3f  recall=%.3f  filt_sharpe=%.3f",
             metrics_lgbm['auc'], metrics_lgbm['precision'], metrics_lgbm['recall'], metrics_lgbm['filt_sharpe'])

    from catboost import CatBoostClassifier

    log.info("[3/3] CatBoost — Optuna HPO  n_trials=60  n_jobs=%s", n_optuna)
    study_cat = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=42))
    study_cat.optimize(
        partial(
            _optuna_objective_cat,
            X_train_scaled=X_train_scaled,
            y_train=y_train,
            train_weights=train_weights,
            purged_cv=purged_cv,
            es_rounds=ES_ROUNDS,
            cat_gpu_kwargs=cat_kw,
        ),
        n_trials=60,
        n_jobs=n_optuna,
        show_progress_bar=False,
    )
    log.info("      CatBoost HPO done  best_auc=%.4f", study_cat.best_value)
    log.info("      CatBoost — final fit")
    final_cat = CatBoostClassifier(
        **study_cat.best_params,
        objective='Logloss',
        iterations=1000,
        early_stopping_rounds=ES_ROUNDS,
        auto_class_weights='Balanced',
        random_seed=42,
        verbose=False,
        **_gpu_kwargs('cat'),
    )
    final_cat.fit(
        X_es, y_es,
        sample_weight=w_es,
        eval_set=(X_cal, y_cal),
    )

    y_proba_cat = final_cat.predict_proba(X_test_scaled)[:, 1]
    metrics_cat = evaluate_meta_model(y_test, y_proba_cat, returns_test, threshold=0.50)
    log.info("      CatBoost holdout  auc=%.4f  precision=%.3f  recall=%.3f  filt_sharpe=%.3f",
             metrics_cat['auc'], metrics_cat['precision'], metrics_cat['recall'], metrics_cat['filt_sharpe'])

    y_proba_xgb_raw = y_proba_xgb
    y_proba_lgbm_raw = y_proba_lgbm
    y_proba_cat_raw = y_proba_cat

    from sklearn.linear_model import LogisticRegression

    def _rebalance_weighted_proba(raw_proba, class_ratio):
        raw_proba = np.clip(np.asarray(raw_proba, dtype=float), 1e-6, 1 - 1e-6)
        return raw_proba / (raw_proba + (1.0 - raw_proba) / class_ratio)

    base_model_probas = {
        'XGBoost': y_proba_xgb,
        'LightGBM': y_proba_lgbm,
        'CatBoost': y_proba_cat,
    }

    y_proba_xgb_cal = _rebalance_weighted_proba(
        final_xgb.predict_proba(X_cal)[:, 1], scale_pos_weight
    )
    y_proba_lgbm_cal = _rebalance_weighted_proba(
        final_lgbm.predict_proba(X_cal)[:, 1], (1.0 - y_train.mean()) / y_train.mean()
    )
    y_proba_cat_cal = final_cat.predict_proba(X_cal)[:, 1]

    base_model_probas_cal = {
        'XGBoost': y_proba_xgb_cal,
        'LightGBM': y_proba_lgbm_cal,
        'CatBoost': y_proba_cat_cal,
    }

    val_auc_by_model = {
        name: roc_auc_score(y_cal, proba)
        for name, proba in base_model_probas_cal.items()
    }
    auc_edge_weights = np.array([
        max(val_auc_by_model[name] - 0.5, 0.0)
        for name in base_model_probas
    ], dtype=float)
    if auc_edge_weights.sum() == 0:
        auc_edge_weights = np.ones(len(base_model_probas), dtype=float)
    auc_edge_weights = auc_edge_weights / auc_edge_weights.sum()

    _test_matrix = np.column_stack([base_model_probas[name] for name in base_model_probas])
    _cal_matrix = np.column_stack([base_model_probas_cal[name] for name in base_model_probas_cal])

    ensemble_probas = {
        'Ensemble: Soft Voting': _test_matrix.mean(axis=1),
        'Ensemble: Val-AUC Weighted': np.average(_test_matrix, axis=1, weights=auc_edge_weights),
        'Ensemble: Rank Average': np.column_stack([
            pd.Series(base_model_probas[name]).rank(pct=True).to_numpy()
            for name in base_model_probas
        ]).mean(axis=1),
    }
    ensemble_probas_cal = {
        'Ensemble: Soft Voting': _cal_matrix.mean(axis=1),
        'Ensemble: Val-AUC Weighted': np.average(_cal_matrix, axis=1, weights=auc_edge_weights),
        'Ensemble: Rank Average': np.column_stack([
            pd.Series(base_model_probas_cal[name]).rank(pct=True).to_numpy()
            for name in base_model_probas_cal
        ]).mean(axis=1),
    }

    stacker = LogisticRegression(max_iter=1000, class_weight='balanced', random_state=42)
    stacker.fit(_cal_matrix, y_cal, sample_weight=train_weights[_es_cut:])
    ensemble_probas['Ensemble: Stacked Logistic'] = stacker.predict_proba(_test_matrix)[:, 1]
    ensemble_probas_cal['Ensemble: Stacked Logistic'] = stacker.predict_proba(_cal_matrix)[:, 1]

    model_probas = {**base_model_probas, **ensemble_probas}
    model_metrics = {}
    for name, proba in model_probas.items():
        opt_t = find_optimal_threshold(y_test, proba, returns_test)
        model_metrics[name] = evaluate_meta_model(y_test, proba, returns_test, threshold=opt_t)
        log.info(
            "Optimal threshold  model=%s  threshold=%.2f  filt_sharpe=%.3f",
            name,
            opt_t,
            model_metrics[name].get("filt_sharpe", float("nan")),
        )

    _selectable_ensembles = [name for name in ensemble_probas if name != 'Ensemble: Stacked Logistic']
    best_ensemble_name = max(
        _selectable_ensembles,
        key=lambda name: roc_auc_score(y_cal, ensemble_probas_cal[name]),
    )
    best_ensemble_proba = ensemble_probas[best_ensemble_name]
    log.info(
        "Ensemble selected  best=%s  auc_cal=%.4f",
        best_ensemble_name,
        roc_auc_score(y_cal, ensemble_probas_cal[best_ensemble_name]),
    )

    log.info("Production refit on all %d labeled rows ...", len(training_df))
    prod_n_pos = int((y_production == 1).sum())
    prod_n_neg = int((y_production == 0).sum())
    prod_scale_pos_weight = prod_n_neg / max(prod_n_pos, 1)
    prod_class_ratio = (1.0 - y_production.mean()) / max(float(y_production.mean()), 1e-9)

    _prod_cut = int(len(X_production_scaled) * 0.80)
    _prod_cut = min(max(_prod_cut, 1), len(X_production_scaled) - 1)
    X_prod_es = X_production_scaled.iloc[:_prod_cut]
    y_prod_es = y_production.iloc[:_prod_cut]
    X_prod_cal = X_production_scaled.iloc[_prod_cut:]
    y_prod_cal = y_production.iloc[_prod_cut:]
    w_prod_es = production_weights[:_prod_cut]

    _xgb_bp = {k: v for k, v in study_xgb.best_params.items() if k not in ('early_stopping_rounds', 'callbacks')}
    production_xgb_es = XGBClassifier(
        **_xgb_bp,
        objective='binary:logistic',
        eval_metric='auc',
        n_estimators=1000,
        early_stopping_rounds=ES_ROUNDS,
        scale_pos_weight=prod_scale_pos_weight,
        random_state=42,
        verbosity=0,
        **_gpu_kwargs('xgb'),
    )
    production_xgb_es.fit(
        X_prod_es, y_prod_es,
        sample_weight=w_prod_es,
        eval_set=[(X_prod_cal, y_prod_cal)],
        verbose=False,
    )
    _prod_xgb_rounds = getattr(production_xgb_es, 'best_iteration', None)
    _prod_xgb_rounds = int(_prod_xgb_rounds + 1) if _prod_xgb_rounds is not None else 1000
    production_xgb = XGBClassifier(
        **_xgb_bp,
        objective='binary:logistic',
        eval_metric='auc',
        n_estimators=_prod_xgb_rounds,
        scale_pos_weight=prod_scale_pos_weight,
        random_state=42,
        verbosity=0,
        **_gpu_kwargs('xgb'),
    )
    production_xgb.fit(X_production_scaled, y_production, sample_weight=production_weights)

    production_lgbm_es = LGBMClassifier(
        **study_lgbm.best_params,
        objective='binary',
        n_estimators=1000,
        class_weight='balanced',
        random_state=42,
        verbosity=-1,
        **_gpu_kwargs('lgbm'),
    )
    production_lgbm_es.fit(
        X_prod_es, y_prod_es,
        sample_weight=w_prod_es,
        eval_set=[(X_prod_cal, y_prod_cal)],
        callbacks=[lgb_es(ES_ROUNDS, verbose=False), lgb_log(period=-1)],
    )
    _prod_lgbm_rounds = int(getattr(production_lgbm_es, 'best_iteration_', None) or 1000)
    production_lgbm = LGBMClassifier(
        **study_lgbm.best_params,
        objective='binary',
        n_estimators=_prod_lgbm_rounds,
        class_weight='balanced',
        random_state=42,
        verbosity=-1,
        **_gpu_kwargs('lgbm'),
    )
    production_lgbm.fit(X_production_scaled, y_production, sample_weight=production_weights)

    production_cat_es = CatBoostClassifier(
        **study_cat.best_params,
        objective='Logloss',
        iterations=1000,
        early_stopping_rounds=ES_ROUNDS,
        auto_class_weights='Balanced',
        random_seed=42,
        verbose=False,
        **_gpu_kwargs('cat'),
    )
    production_cat_es.fit(
        X_prod_es, y_prod_es,
        sample_weight=w_prod_es,
        eval_set=(X_prod_cal, y_prod_cal),
    )
    _prod_cat_rounds = production_cat_es.get_best_iteration()
    _prod_cat_rounds = int(_prod_cat_rounds + 1) if _prod_cat_rounds is not None else int(production_cat_es.tree_count_)
    production_cat = CatBoostClassifier(
        **study_cat.best_params,
        objective='Logloss',
        iterations=max(_prod_cat_rounds, 1),
        auto_class_weights='Balanced',
        random_seed=42,
        verbose=False,
        **_gpu_kwargs('cat'),
    )
    production_cat.fit(X_production_scaled, y_production, sample_weight=production_weights)
    log.info("Production refit done  elapsed=%.1fs", time.time() - _t_train_start)

    log.info("Saving model artifacts to %s ...", MODELS_DIR)
    production_xgb.save_model(str(MODELS_DIR / f'xgboost_meta_{ts}.ubj'))
    production_lgbm.booster_.save_model(str(MODELS_DIR / f'lightgbm_meta_{ts}.txt'))
    production_cat.save_model(str(MODELS_DIR / f'catboost_meta_{ts}.cbm'))
    joblib.dump(production_scaler, MODELS_DIR / f'meta_label_scaler_{ts}.joblib')
    log.info("Models saved  ts=%s", ts)

    ensemble_weights = {
        name: float(weight)
        for name, weight in zip(base_model_probas.keys(), auc_edge_weights)
    }
    training_metadata = {
        "split": split_metadata,
        "production_rows": int(len(training_df)),
        "feature_columns": training_feature_columns,
        "best_ensemble_name": best_ensemble_name,
        "ensemble_weights": ensemble_weights,
        "holdout_metrics": {
            name: {metric: float(value) for metric, value in metrics.items()}
            for name, metrics in model_metrics.items()
        },
        "production_class_ratio": float(prod_class_ratio),
        "production_scale_pos_weight": float(prod_scale_pos_weight),
    }
    with (MODELS_DIR / f'meta_label_training_metadata_{ts}.json').open('w', encoding='utf-8') as f:
        json.dump(training_metadata, f, indent=2)

    return locals()


def build_meta_label_features(
    output_dir: str = str(PIPELINE_OUTPUT_DIR),
    refresh_stock_cache: bool | None = None,
) -> dict[str, Any]:
    """Build trade labels/features and return training data in memory."""
    _configure_runtime()
    output_path = Path(output_dir).resolve()
    if refresh_stock_cache is None:
        refresh_stock_cache = _get_env_bool("META_LABEL_REFRESH_STOCK_CACHE", False)

    log.info("=== build_meta_label_features  output_dir=%s ===", output_path)
    t0 = time.time()
    try:
        with _working_directory(output_path):
            namespace = _run_feature_pipeline_impl(refresh_stock_cache=refresh_stock_cache)
    finally:
        _collect_garbage()

    training_df = namespace["training_df"]
    feature_columns = namespace["training_feature_columns"]
    log.info(
        "=== build_meta_label_features done  rows=%d  features=%d  elapsed=%.1fs ===",
        len(training_df), len(feature_columns), time.time() - t0,
    )
    return {
        "training_df": training_df,
        "feature_columns": feature_columns,
        "feature_count": len(feature_columns),
        "training_rows": int(len(training_df)),
    }


def train_meta_label_models(
    training_df: Any,
    training_feature_columns: list[str],
    output_dir: str = str(PIPELINE_OUTPUT_DIR),
    models_dir: str = str(MODELS_DIR),
    optuna_n_jobs: int | None = None,
) -> dict[str, Any]:
    """Train meta-label models from in-memory training data."""
    global MODELS_DIR

    _configure_runtime()
    output_path = Path(output_dir).resolve()
    model_path = Path(models_dir).resolve()
    model_path.mkdir(parents=True, exist_ok=True)
    log.info("=== train_meta_label_models  models_dir=%s ===", model_path)
    t0 = time.time()
    before_mtimes = {p.resolve(): p.stat().st_mtime for p in model_path.glob("*") if p.is_file()}

    previous_models_dir = MODELS_DIR
    MODELS_DIR = model_path
    try:
        with _working_directory(output_path):
            namespace = _run_training_pipeline_impl(
                training_df, training_feature_columns, optuna_n_jobs=optuna_n_jobs
            )
    finally:
        MODELS_DIR = previous_models_dir
        _collect_garbage()

    after = [p.resolve() for p in model_path.glob("*") if p.is_file()]
    created_or_updated = [
        p for p in after
        if p not in before_mtimes or p.stat().st_mtime > before_mtimes[p]
    ]
    created_or_updated = sorted(created_or_updated, key=lambda p: p.stat().st_mtime)

    artifacts = {
        "models_dir": str(model_path),
        "artifacts": [_artifact_info(path) for path in created_or_updated],
        "best_ensemble_name": namespace.get("best_ensemble_name"),
        "model_count": len(namespace.get("model_probas", {})),
        "model_metrics": _serialize_model_metrics(namespace.get("model_metrics", {})),
    }
    comparison = _format_metric_comparison(namespace.get("model_metrics", {}), artifacts["best_ensemble_name"])
    log.info("Model comparison:\n%s", comparison)
    log.info(
        "=== train_meta_label_models done  artifacts=%d  elapsed=%.1fs ===",
        len(created_or_updated), time.time() - t0,
    )

    log.info("Uploading %d artifact(s) to R2 ...", len(created_or_updated))
    for p in created_or_updated:
        r2_key = R2_MODELS_PREFIX + p.name
        try:
            _r2_upload(p, r2_key)
        except Exception as exc:
            log.warning("R2 upload failed for %s: %s", p.name, exc)

    return artifacts


def train_meta_label_models_pipeline(
    output_dir: str = str(PIPELINE_OUTPUT_DIR),
    models_dir: str = str(MODELS_DIR),
    refresh_stock_cache: bool = False,
    optuna_n_jobs: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}

    feature_payload = build_meta_label_features(
        output_dir=output_dir,
        refresh_stock_cache=refresh_stock_cache,
    )
    result["features"] = {
        "feature_count": feature_payload["feature_count"],
        "training_rows": feature_payload["training_rows"],
    }

    result["training"] = train_meta_label_models(
        training_df=feature_payload["training_df"],
        training_feature_columns=feature_payload["feature_columns"],
        output_dir=output_dir,
        models_dir=models_dir,
        optuna_n_jobs=optuna_n_jobs,
    )
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Train meta-label models pipeline")
    parser.add_argument("--output-dir", default=str(PIPELINE_OUTPUT_DIR))
    parser.add_argument("--models-dir", default=str(MODELS_DIR))
    parser.add_argument("--refresh-stock-cache", action="store_true", default=False)
    parser.add_argument("--gpu", action="store_true", default=False,
                        help="Enable GPU training (XGBoost: cuda, LightGBM: gpu, CatBoost: GPU)")
    parser.add_argument(
        "--optuna-jobs",
        type=int,
        default=None,
        metavar="N",
        help="Parallel Optuna trials per study (-1 = all CPUs). Overrides OPTUNA_N_JOBS if set.",
    )
    args = parser.parse_args()

    if args.gpu or _get_env_bool("USE_GPU"):
        globals()["USE_GPU"] = True
        log.info("GPU training ENABLED  (xgb=cuda  lgbm=gpu  cat=GPU)")

    result = train_meta_label_models_pipeline(
        output_dir=args.output_dir,
        models_dir=args.models_dir,
        refresh_stock_cache=args.refresh_stock_cache,
        optuna_n_jobs=args.optuna_jobs,
    )
    print(result)
