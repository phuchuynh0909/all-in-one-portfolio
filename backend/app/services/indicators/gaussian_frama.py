"""
Gaussian FRAMA (G-FRAMA) — QuantEdgeB.

Port of the TradingView PineScript indicator "G-FRAMA | QuantEdgeB".

Pipeline
--------
1. Gaussian filter of ``close`` (symmetric Gaussian-weighted MA, ``len_FG`` /
   ``sigma``).
2. FRAMA (fractal-adaptive MA) driven by the Gaussian-filtered series, with the
   fractal dimension measured from raw ``high``/``low`` over ``fm_len``.
3. ATR envelope: ``LongV = FRAMA + mult_ATR * ATR`` (upper "blue" band) and
   ``ShortV = FRAMA - ATR`` (lower band).
4. Regime state ``QB``: +1 (blue / bullish) once ``close > LongV``, -1
   (red / bearish) once ``close < ShortV``, else it holds the last state.

All functions are column-wise (rows = time, cols = symbols) to match the
vectorbt backtest layout.
"""
import numpy as np
import numba as nb


@nb.njit(cache=True)
def gaussian_filter_2d(src: np.ndarray, length: int, sigma: float) -> np.ndarray:
    """Symmetric Gaussian-weighted moving average, column-wise.

    weight(i) = exp(-0.5 * ((i - (length-1)/2) / sigma)^2), i = 0..length-1,
    applied to ``src[t-i]`` and normalised by the weight sum (mirrors the
    PineScript ``F_Gaussian``).
    """
    n, m = src.shape
    out = np.full((n, m), np.nan)
    weights = np.empty(length)
    wsum = 0.0
    for i in range(length):
        w = np.exp(-0.5 * ((i - (length - 1) / 2.0) / sigma) ** 2)
        weights[i] = w
        wsum += w
    for j in range(m):
        for t in range(length - 1, n):
            acc = 0.0
            valid = True
            for i in range(length):
                v = src[t - i, j]
                if np.isnan(v):
                    valid = False
                    break
                acc += v * weights[i]
            if valid:
                out[t, j] = acc / wsum
    return out


@nb.njit(cache=True)
def atr_wilder_2d(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                  period: int) -> np.ndarray:
    """Wilder's ATR (RMA of true range), column-wise — matches talib.ATR."""
    n, m = close.shape
    out = np.full((n, m), np.nan)
    for j in range(m):
        tr_sum = 0.0
        prev_atr = np.nan
        for t in range(n):
            if t == 0:
                tr = high[t, j] - low[t, j]
            else:
                a = high[t, j] - low[t, j]
                b = abs(high[t, j] - close[t - 1, j])
                c = abs(low[t, j] - close[t - 1, j])
                tr = a
                if b > tr:
                    tr = b
                if c > tr:
                    tr = c
            if t < period:
                tr_sum += tr
                if t == period - 1:
                    prev_atr = tr_sum / period
                    out[t, j] = prev_atr
            else:
                prev_atr = (prev_atr * (period - 1) + tr) / period
                out[t, j] = prev_atr
    return out


@nb.njit(cache=True)
def frama_2d(src: np.ndarray, high: np.ndarray, low: np.ndarray,
             fm_len: int, upper_limit: int, lower_limit: int) -> np.ndarray:
    """Fractal-Adaptive MA of ``src`` (the Gaussian filter), column-wise.

    Fractal dimension ``D`` is measured from raw ``high``/``low`` over ``fm_len``
    (mirrors PineScript ``F_FRAMA(SRC, LEN1=fm_len, LEN2=upper, LEN3=lower)``).
    """
    n, m = src.shape
    out = np.full((n, m), np.nan)
    half = fm_len // 2
    w = np.log(2.0 / (lower_limit + 1))
    lo_alpha = 2.0 / (lower_limit + 1)
    for j in range(m):
        prev_D = 0.0
        f = np.nan
        for t in range(n):
            if t < fm_len:
                continue
            hh = -1e18; ll = 1e18
            for k in range(fm_len):
                hv = high[t - k, j]; lv = low[t - k, j]
                if hv > hh: hh = hv
                if lv < ll: ll = lv
            HL = (hh - ll) / fm_len
            hh1 = -1e18; ll1 = 1e18
            for k in range(half):
                hv = high[t - k, j]; lv = low[t - k, j]
                if hv > hh1: hh1 = hv
                if lv < ll1: ll1 = lv
            HL1 = (hh1 - ll1) / half
            hh2 = -1e18; ll2 = 1e18
            for k in range(half):
                hv = high[t - half - k, j]; lv = low[t - half - k, j]
                if hv > hh2: hh2 = hv
                if lv < ll2: ll2 = lv
            HL2 = (hh2 - ll2) / half

            if HL1 > 0.0 and HL2 > 0.0 and HL > 0.0:
                D = (np.log(HL1 + HL2) - np.log(HL)) / np.log(2.0)
            else:
                D = prev_D
            prev_D = D

            alpha = np.exp(w * (D - 1.0))
            if alpha > 1.0:
                alpha = 1.0
            elif alpha < 0.01:
                alpha = 0.01
            oldN = (2.0 - alpha) / alpha
            newN = (lower_limit - upper_limit) * (oldN - 1.0) / (lower_limit - 1) + upper_limit
            newalpha = 2.0 / (newN + 1.0)
            if newalpha < lo_alpha:
                newalpha = lo_alpha
            elif newalpha > 1.0:
                newalpha = 1.0

            s = src[t, j]
            if np.isnan(s):
                continue
            if np.isnan(f):
                f = s
            else:
                f = (1.0 - newalpha) * f + newalpha * s
            out[t, j] = f
    return out


@nb.njit(cache=True)
def gframa_state_2d(close: np.ndarray, long_v: np.ndarray,
                    short_v: np.ndarray) -> np.ndarray:
    """Regime state QB: +1 once close>LongV (blue), -1 once close<ShortV (red),
    holds previous state otherwise. Mirrors the PineScript ``var QB`` logic."""
    n, m = close.shape
    out = np.zeros((n, m), dtype=np.float64)
    for j in range(m):
        qb = 0.0
        for t in range(n):
            c = close[t, j]
            lv = long_v[t, j]
            sv = short_v[t, j]
            if not np.isnan(lv) and not np.isnan(sv):
                long_c = c > lv
                short_c = c < sv
                if long_c and not short_c:
                    qb = 1.0
                if short_c:
                    qb = -1.0
            out[t, j] = qb
    return out


def gaussian_frama(close, high, low,
                   gaussian_length: int = 4,
                   sigma: float = 2.0,
                   fm_len: int = 20,
                   upper_limit: int = 8,
                   lower_limit: int = 40,
                   atr_period: int = 14,
                   atr_mult: float = 1.9):
    """Full G-FRAMA computation for 2-D price arrays.

    Returns a dict with ``frama``, ``long_v`` (upper "blue" band),
    ``short_v`` (lower band), ``qb`` (regime state +1/-1/0) and ``atr``.
    All entries are float64 arrays shaped like ``close``.
    """
    close = np.asarray(close, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    if close.ndim == 1:
        close = close.reshape(-1, 1)
        high = high.reshape(-1, 1)
        low = low.reshape(-1, 1)

    gauss = gaussian_filter_2d(close, gaussian_length, sigma)
    frama = frama_2d(gauss, high, low, fm_len, upper_limit, lower_limit)
    atr = atr_wilder_2d(high, low, close, atr_period)
    long_v = frama + atr_mult * atr
    short_v = frama - atr
    qb = gframa_state_2d(close, long_v, short_v)
    return {
        "frama": frama,
        "long_v": long_v,
        "short_v": short_v,
        "qb": qb,
        "atr": atr,
    }
