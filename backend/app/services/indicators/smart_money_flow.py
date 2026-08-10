"""
Smart Money Flow Cloud indicator.

Python port of Smart Money Flow Cloud [BOSWaves] (PineScript).

Key concepts
------------
* CLV-based money flow normalised over a rolling window → [-1, 1]
* Adaptive ATR bands whose width scales with flow *strength*
* State machine: bull (+1) when close crosses above upper band,
  bear (-1) when close falls below lower band
* Retest dots with configurable cooldown

Default parameters are the optimised values found in backtest_005b.
"""

import numpy as np
import pandas as pd
import talib
from numba import njit, prange


# ── Default (optimised) params ─────────────────────────────────────────────────
SMF_DEFAULTS = {
    "trend_len":   34,
    "basis_type":  "ALMA",
    "alma_offset": 0.85,
    "alma_sigma":  6.0,
    "basis_smooth": 3,
    "mf_len":      24,
    "mf_smooth":    5,
    "mf_power":    1.2,
    "atr_len":      14,
    "min_mult":    0.9,
    "max_mult":    2.2,
    "dot_cooldown": 12,
}


def coerce_smf_basis_type(raw: object, default: str | None = None) -> str:
    """
    Normalise basis_type for :func:`smart_money_flow`.

    * ``0`` / ``"0"`` / ``"EMA"`` → ``"EMA"``
    * ``1`` / ``"1"`` / ``"ALMA"`` → ``"ALMA"``
    """
    default = default or SMF_DEFAULTS["basis_type"]
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return "ALMA" if int(raw) == 1 else "EMA"
    s = str(raw).strip().upper()
    if s in ("1", "ALMA"):
        return "ALMA"
    if s in ("0", "EMA"):
        return "EMA"
    return default


def build_smart_money_flow_kwargs(ind_params: dict | None) -> dict:
    """Merge ``ind_params`` with :data:`SMF_DEFAULTS` and return typed kwargs for :func:`smart_money_flow`."""
    p = {**SMF_DEFAULTS, **(ind_params or {})}
    p["basis_type"] = coerce_smf_basis_type(p.get("basis_type"))
    return {
        "trend_len": int(p["trend_len"]),
        "basis_type": p["basis_type"],
        "alma_offset": float(p["alma_offset"]),
        "alma_sigma": float(p["alma_sigma"]),
        "basis_smooth": int(p["basis_smooth"]),
        "mf_len": int(p["mf_len"]),
        "mf_smooth": int(p["mf_smooth"]),
        "mf_power": float(p["mf_power"]),
        "atr_len": int(p["atr_len"]),
        "min_mult": float(p["min_mult"]),
        "max_mult": float(p["max_mult"]),
        "dot_cooldown": int(p["dot_cooldown"]),
    }


def _alma_1d(series: np.ndarray, period: int, offset: float, sigma: float) -> np.ndarray:
    """Arnaud Legoux Moving Average (1-D numpy)."""
    m = offset * (period - 1)
    s = period / sigma
    w = np.exp(-((np.arange(period) - m) ** 2) / (2 * s * s))
    w /= w.sum()
    out = np.full(len(series), np.nan)
    for i in range(period - 1, len(series)):
        out[i] = np.dot(w, series[i - period + 1 : i + 1])
    return out


def smart_money_flow(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    trend_len: int   = SMF_DEFAULTS["trend_len"],
    basis_type: str  = SMF_DEFAULTS["basis_type"],
    alma_offset: float = SMF_DEFAULTS["alma_offset"],
    alma_sigma: float  = SMF_DEFAULTS["alma_sigma"],
    basis_smooth: int  = SMF_DEFAULTS["basis_smooth"],
    mf_len: int        = SMF_DEFAULTS["mf_len"],
    mf_smooth: int     = SMF_DEFAULTS["mf_smooth"],
    mf_power: float    = SMF_DEFAULTS["mf_power"],
    atr_len: int       = SMF_DEFAULTS["atr_len"],
    min_mult: float    = SMF_DEFAULTS["min_mult"],
    max_mult: float    = SMF_DEFAULTS["max_mult"],
    dot_cooldown: int  = SMF_DEFAULTS["dot_cooldown"],
) -> dict:
    """
    Compute Smart Money Flow Cloud for a single symbol.

    Parameters
    ----------
    open_, high, low, close, volume : np.ndarray (1-D, float64)
        OHLCV arrays of equal length.

    Returns
    -------
    dict with keys:
        last_signal    : np.ndarray[int8]   — +1 bull / -1 bear (state machine)
        switch_up      : np.ndarray[bool]   — bar where regime turns bullish
        switch_down    : np.ndarray[bool]   — bar where regime turns bearish
        upper          : np.ndarray[float]  — adaptive upper band
        lower          : np.ndarray[float]  — adaptive lower band
        b_close        : np.ndarray[float]  — basis line (close-based EMA/ALMA)
        b_open         : np.ndarray[float]  — basis line (open-based EMA/ALMA)
        mf_smooth      : np.ndarray[float]  — smoothed money flow  [-1, 1]
        strength       : np.ndarray[float]  — flow strength        [0, 1]
        bull_dot       : np.ndarray[bool]   — bull retest dot
        bear_dot       : np.ndarray[bool]   — bear retest dot
        strength_signed: np.ndarray[float]  — tanh-scaled trend strength
    """
    o = np.asarray(open_,  dtype=np.float64)
    h = np.asarray(high,   dtype=np.float64)
    l = np.asarray(low,    dtype=np.float64)
    c = np.asarray(close,  dtype=np.float64)
    v = np.asarray(volume, dtype=np.float64)
    n = len(c)

    # ── Basis ─────────────────────────────────────────────────────────────────
    if basis_type == "ALMA":
        raw_o = _alma_1d(o, trend_len, alma_offset, alma_sigma)
        raw_c = _alma_1d(c, trend_len, alma_offset, alma_sigma)
    else:
        raw_o = pd.Series(o).ewm(span=trend_len, adjust=False).mean().values
        raw_c = pd.Series(c).ewm(span=trend_len, adjust=False).mean().values

    if basis_smooth > 1:
        b_o = pd.Series(raw_o).ewm(span=basis_smooth, adjust=False).mean().values
        b_c = pd.Series(raw_c).ewm(span=basis_smooth, adjust=False).mean().values
    else:
        b_o, b_c = raw_o, raw_c
    b_main = b_c

    # ── Smart Money Flow ──────────────────────────────────────────────────────
    # Both ratios below are guarded with ``where=`` rather than np.where(): the
    # latter evaluates the division for *every* element first, so a flat bar
    # (h == l, e.g. limit-locked) or a zero-flow window computes 0/0 and warns
    # before the mask discards it.
    hl       = h - l
    clv      = np.zeros_like(hl)
    np.divide((c - l) - (h - c), hl, out=clv, where=hl != 0)
    raw_flow = clv * v
    mf_num   = pd.Series(raw_flow).rolling(mf_len).sum().values
    mf_den   = pd.Series(np.abs(raw_flow)).rolling(mf_len).sum().values
    mf_raw   = np.zeros_like(mf_den)
    np.divide(mf_num, mf_den, out=mf_raw, where=mf_den != 0)
    mf_sm    = (pd.Series(mf_raw).ewm(span=mf_smooth, adjust=False).mean().values
                if mf_smooth > 1 else mf_raw)
    strength = np.clip(np.power(np.abs(mf_sm), mf_power), 0.0, 1.0)
    mult     = min_mult + (max_mult - min_mult) * strength

    # ── Adaptive bands ────────────────────────────────────────────────────────
    atr   = talib.ATR(h, l, c, timeperiod=atr_len)
    upper = b_main + atr * mult
    lower = b_main - atr * mult

    # ── State machine ─────────────────────────────────────────────────────────
    long_cond  = (np.roll(c, 1) <= np.roll(upper, 1)) & (c > upper)
    short_cond = (np.roll(c, 1) >= np.roll(lower, 1)) & (c < lower)
    long_cond[0] = short_cond[0] = False

    last_sig    = np.zeros(n, dtype=np.int8)
    last_sig[0] = 1 if c[0] >= b_main[0] else -1
    for i in range(1, n):
        if long_cond[i]:    last_sig[i] =  1
        elif short_cond[i]: last_sig[i] = -1
        else:               last_sig[i] = last_sig[i - 1]

    switch_up   = (last_sig ==  1) & (np.roll(last_sig, 1) == -1)
    switch_down = (last_sig == -1) & (np.roll(last_sig, 1) ==  1)
    switch_up[0] = switch_down[0] = False

    # ── Retest dots with cooldown ─────────────────────────────────────────────
    bear_cond = (last_sig == -1) & (h > b_c)
    bull_cond = (last_sig ==  1) & (l < b_c)
    bear_dot  = np.zeros(n, dtype=bool)
    bull_dot  = np.zeros(n, dtype=bool)
    last_bear = last_bull = -dot_cooldown - 1
    for i in range(n):
        if bear_cond[i] and (dot_cooldown == 0 or i - last_bear >= dot_cooldown):
            bear_dot[i] = True; last_bear = i
        if bull_cond[i] and (dot_cooldown == 0 or i - last_bull >= dot_cooldown):
            bull_dot[i] = True; last_bull = i

    # ── Trend strength (tanh-scaled) ──────────────────────────────────────────
    mintick = 0.01
    up_span = np.maximum(upper - b_main, mintick)
    dn_span = np.maximum(b_main - lower, mintick)
    raw_str = np.where(last_sig == 1,
                       (c - b_main) / up_span,
                       -(b_main - c) / dn_span)
    x       = np.clip(raw_str * 1.5, -10, 10)
    v_tanh  = (np.exp(x) - np.exp(-x)) / (np.exp(x) + np.exp(-x))
    str_signed = pd.Series(v_tanh).ewm(span=3, adjust=False).mean().values

    return {
        "last_signal":     last_sig,
        "switch_up":       switch_up,
        "switch_down":     switch_down,
        "upper":           upper,
        "lower":           lower,
        "b_close":         b_c,
        "b_open":          b_o,
        "mf_smooth":       mf_sm,
        "strength":        strength,
        "bull_dot":        bull_dot,
        "bear_dot":        bear_dot,
        "strength_signed": str_signed,
    }


# ── Numba-accelerated 2-D kernel ───────────────────────────────────────────────

def _alma_weights(period: int, offset: float = 0.85, sigma: float = 6.0) -> np.ndarray:
    """Precompute normalised ALMA weights (length = period)."""
    m = offset * (period - 1)
    s = period / sigma
    w = np.exp(-((np.arange(period) - m) ** 2) / (2.0 * s * s))
    return (w / w.sum()).astype(np.float64)


@njit(parallel=True, cache=False)
def _smf_nb(
    o, h, l, c, v,   # (n, m) float64 — OHLCV, all symbols
    alma_w,           # (trend_len,) float64 — precomputed ALMA weights
    use_alma,         # int  0=EMA  1=ALMA
    trend_len,        # int
    alpha_basis,      # float  EMA alpha for basis (ignored when use_alma=1)
    alpha_smooth,     # float  EMA alpha for basis smooth (1.0 = no-op when span=1)
    mf_len,           # int
    alpha_mf,         # float  EMA alpha for money-flow smooth
    mf_power,         # float
    atr_len,          # int
    min_mult,         # float
    max_mult,         # float
    dot_cooldown,     # int
):
    """
    Vectorised Numba kernel: compute all SMF Cloud fields for m symbols in parallel.

    Returns 12 arrays each of shape (n, m):
        b_c, b_o, mf_sm, strn, upper, lower,
        sig (int8), sw_up, sw_dn, bull_dot, bear_dot, str_sg
    """
    n, m = c.shape

    b_c    = np.empty((n, m), dtype=np.float64)
    b_o    = np.empty((n, m), dtype=np.float64)
    mf_sm  = np.empty((n, m), dtype=np.float64)
    strn   = np.empty((n, m), dtype=np.float64)
    upper  = np.empty((n, m), dtype=np.float64)
    lower  = np.empty((n, m), dtype=np.float64)
    sig    = np.zeros((n, m), dtype=np.int8)
    sw_up  = np.zeros((n, m), dtype=np.bool_)
    sw_dn  = np.zeros((n, m), dtype=np.bool_)
    bul_d  = np.zeros((n, m), dtype=np.bool_)
    bea_d  = np.zeros((n, m), dtype=np.bool_)
    str_sg = np.empty((n, m), dtype=np.float64)

    for col in prange(m):

        # ── 1. Basis: ALMA convolution or EMA ────────────────────────────
        if use_alma:
            p = len(alma_w)
            for i in range(p - 1):
                b_c[i, col] = np.nan
                b_o[i, col] = np.nan
            for i in range(p - 1, n):
                sc = 0.0
                so = 0.0
                for k in range(p):
                    j = i - p + 1 + k
                    sc += alma_w[k] * c[j, col]
                    so += alma_w[k] * o[j, col]
                b_c[i, col] = sc
                b_o[i, col] = so
        else:
            b_c[0, col] = c[0, col]
            b_o[0, col] = o[0, col]
            one_m = 1.0 - alpha_basis
            for i in range(1, n):
                b_c[i, col] = alpha_basis * c[i, col] + one_m * b_c[i - 1, col]
                b_o[i, col] = alpha_basis * o[i, col] + one_m * b_o[i - 1, col]

        # ── 2. Basis smooth (in-place EMA; alpha=1.0 → no-op) ────────────
        if alpha_smooth < 1.0:
            start = 0
            while start < n and np.isnan(b_c[start, col]):
                start += 1
            one_m_s = 1.0 - alpha_smooth
            for i in range(start + 1, n):
                b_c[i, col] = alpha_smooth * b_c[i, col] + one_m_s * b_c[i - 1, col]
                b_o[i, col] = alpha_smooth * b_o[i, col] + one_m_s * b_o[i - 1, col]

        # ── 3. Smart Money Flow via ring-buffer rolling sum ───────────────
        ring_f = np.zeros(mf_len)
        ring_a = np.zeros(mf_len)
        ri = 0
        mf_num = 0.0
        mf_den = 0.0
        for i in range(n):
            hl = h[i, col] - l[i, col]
            clv = ((c[i, col] - l[i, col]) - (h[i, col] - c[i, col])) / hl if hl != 0.0 else 0.0
            rf = clv * v[i, col]
            af = rf if rf >= 0.0 else -rf
            mf_num += rf - ring_f[ri]
            mf_den += af - ring_a[ri]
            ring_f[ri] = rf
            ring_a[ri] = af
            ri = (ri + 1) % mf_len
            if i < mf_len - 1:
                mf_sm[i, col] = np.nan
            else:
                mf_sm[i, col] = mf_num / mf_den if mf_den != 0.0 else 0.0

        # MF EMA smooth in-place (alpha_mf=1.0 when mf_smooth=1 → no-op)
        if alpha_mf < 1.0:
            one_m_mf = 1.0 - alpha_mf
            start_mf = mf_len - 1
            for i in range(start_mf + 1, n):
                mf_sm[i, col] = alpha_mf * mf_sm[i, col] + one_m_mf * mf_sm[i - 1, col]

        # ── 4. Strength from smoothed MF ─────────────────────────────────
        for i in range(n):
            mf_i = mf_sm[i, col]
            if np.isnan(mf_i):
                strn[i, col] = np.nan
            else:
                s = mf_i if mf_i >= 0.0 else -mf_i
                s = s ** mf_power
                if s > 1.0:
                    s = 1.0
                strn[i, col] = s

        # ── 5. ATR (Wilder's) + adaptive bands ───────────────────────────
        # talib.ATR seeds the SMA from TR[1..atr_len] (excludes bar 0 which
        # has no previous close), placing the first valid ATR at bar atr_len.
        atr_sum = 0.0
        for i in range(1, atr_len + 1):
            hl_ = h[i, col] - l[i, col]
            hc_ = h[i, col] - c[i - 1, col]
            if hc_ < 0.0:
                hc_ = -hc_
            lc_ = l[i, col] - c[i - 1, col]
            if lc_ < 0.0:
                lc_ = -lc_
            tr = hl_ if hl_ >= hc_ and hl_ >= lc_ else (hc_ if hc_ >= lc_ else lc_)
            atr_sum += tr
        atr_prev = atr_sum / atr_len

        # Bars before first valid ATR (bars 0 to atr_len-1)
        for i in range(atr_len):
            upper[i, col] = np.nan
            lower[i, col] = np.nan

        # First valid ATR bar
        i0 = atr_len
        bc_i = b_c[i0, col]
        s_i  = strn[i0, col]
        if not np.isnan(bc_i) and not np.isnan(s_i):
            mult = min_mult + (max_mult - min_mult) * s_i
            upper[i0, col] = bc_i + atr_prev * mult
            lower[i0, col] = bc_i - atr_prev * mult
        else:
            upper[i0, col] = np.nan
            lower[i0, col] = np.nan

        # Remaining bars: update ATR (Wilder's recursive) and compute bands
        for i in range(atr_len + 1, n):
            hl_ = h[i, col] - l[i, col]
            hc_ = h[i, col] - c[i - 1, col]
            if hc_ < 0.0:
                hc_ = -hc_
            lc_ = l[i, col] - c[i - 1, col]
            if lc_ < 0.0:
                lc_ = -lc_
            tr = hl_ if hl_ >= hc_ and hl_ >= lc_ else (hc_ if hc_ >= lc_ else lc_)
            atr_prev = (atr_prev * (atr_len - 1) + tr) / atr_len

            bc_i = b_c[i, col]
            s_i  = strn[i, col]
            if not np.isnan(bc_i) and not np.isnan(s_i):
                mult = min_mult + (max_mult - min_mult) * s_i
                upper[i, col] = bc_i + atr_prev * mult
                lower[i, col] = bc_i - atr_prev * mult
            else:
                upper[i, col] = np.nan
                lower[i, col] = np.nan

        # ── 6. State machine + retest dots ────────────────────────────────
        bc0 = b_c[0, col]
        sig[0, col] = np.int8(1) if (not np.isnan(bc0) and c[0, col] >= bc0) else np.int8(-1)
        last_bear = -dot_cooldown - 1
        last_bull = -dot_cooldown - 1

        for i in range(1, n):
            u_prev = upper[i - 1, col]
            l_prev = lower[i - 1, col]
            u_curr = upper[i, col]
            l_curr = lower[i, col]
            c_prev = c[i - 1, col]
            c_curr = c[i, col]

            long_cond  = (not np.isnan(u_prev) and not np.isnan(u_curr)
                          and c_prev <= u_prev and c_curr > u_curr)
            short_cond = (not np.isnan(l_prev) and not np.isnan(l_curr)
                          and c_prev >= l_prev and c_curr < l_curr)

            if long_cond:
                sig[i, col] = np.int8(1)
            elif short_cond:
                sig[i, col] = np.int8(-1)
            else:
                sig[i, col] = sig[i - 1, col]

            prev_s = sig[i - 1, col]
            curr_s = sig[i, col]
            if curr_s == np.int8(1) and prev_s == np.int8(-1):
                sw_up[i, col] = True
            elif curr_s == np.int8(-1) and prev_s == np.int8(1):
                sw_dn[i, col] = True

            bc_i = b_c[i, col]
            if not np.isnan(bc_i):
                if curr_s == np.int8(-1) and h[i, col] > bc_i:
                    if dot_cooldown == 0 or i - last_bear >= dot_cooldown:
                        bea_d[i, col] = True
                        last_bear = i
                if curr_s == np.int8(1) and l[i, col] < bc_i:
                    if dot_cooldown == 0 or i - last_bull >= dot_cooldown:
                        bul_d[i, col] = True
                        last_bull = i

        # ── 7. Trend strength: tanh-scaled, EMA smoothed (span=3, α=0.5) ─
        mintick = 0.01
        for i in range(n):
            bc_i = b_c[i, col]
            u_i  = upper[i, col]
            l_i  = lower[i, col]
            if np.isnan(bc_i) or np.isnan(u_i):
                str_sg[i, col] = np.nan
                continue
            up_span = u_i - bc_i
            if up_span < mintick:
                up_span = mintick
            dn_span = bc_i - l_i
            if dn_span < mintick:
                dn_span = mintick
            raw_str = ((c[i, col] - bc_i) / up_span
                       if sig[i, col] == np.int8(1)
                       else -(bc_i - c[i, col]) / dn_span)
            x = raw_str * 1.5
            if x > 10.0:
                x = 10.0
            elif x < -10.0:
                x = -10.0
            ex  = np.exp(x)
            emx = np.exp(-x)
            str_sg[i, col] = (ex - emx) / (ex + emx)

        # EMA smooth str_sg (span=3 → α=0.5) — in-place
        alpha_ss = 0.5
        one_m_ss = 0.5
        start_ss = 0
        while start_ss < n and np.isnan(str_sg[start_ss, col]):
            start_ss += 1
        for i in range(start_ss + 1, n):
            if not np.isnan(str_sg[i, col]):
                str_sg[i, col] = alpha_ss * str_sg[i, col] + one_m_ss * str_sg[i - 1, col]

    return b_c, b_o, mf_sm, strn, upper, lower, sig, sw_up, sw_dn, bul_d, bea_d, str_sg


_BASIS_TYPE_MAP = {0: "EMA", 1: "ALMA"}


def smart_money_flow_cloud(
    open_df:  pd.DataFrame,
    high_df:  pd.DataFrame,
    low_df:   pd.DataFrame,
    close_df: pd.DataFrame,
    vol_df:   pd.DataFrame,
    trend_len:    int          = SMF_DEFAULTS["trend_len"],
    basis_type:   "int | str"  = SMF_DEFAULTS["basis_type"],
    alma_offset:  float        = SMF_DEFAULTS["alma_offset"],
    alma_sigma:   float        = SMF_DEFAULTS["alma_sigma"],
    basis_smooth: int          = SMF_DEFAULTS["basis_smooth"],
    mf_len:       int          = SMF_DEFAULTS["mf_len"],
    mf_smooth:    int          = SMF_DEFAULTS["mf_smooth"],
    mf_power:     float        = SMF_DEFAULTS["mf_power"],
    atr_len:      int          = SMF_DEFAULTS["atr_len"],
    min_mult:     float        = SMF_DEFAULTS["min_mult"],
    max_mult:     float        = SMF_DEFAULTS["max_mult"],
    dot_cooldown: int          = SMF_DEFAULTS["dot_cooldown"],
) -> dict:
    """
    Compute Smart Money Flow Cloud for multiple symbols (Numba-accelerated).

    Processes all symbols in a single parallel Numba kernel instead of a
    Python loop over symbols, giving ~10-50x speedup over the pure-Python
    implementation.

    Parameters
    ----------
    open_df, high_df, low_df, close_df, vol_df : pd.DataFrame
        OHLCV DataFrames aligned on the same DatetimeIndex (rows=time, cols=symbols).
    basis_type : int or str
        0 or "EMA" / 1 or "ALMA" (default).
    All other parameters : see :func:`smart_money_flow`.

    Returns
    -------
    dict[str, dict[str, pd.Series]]
        Outer key = symbol. Inner keys:
            last_signal, switch_up, switch_down, upper, lower,
            b_close, b_open, mf_smooth, strength,
            bull_dot, bear_dot, strength_signed
    """
    if isinstance(basis_type, int):
        basis_type = _BASIS_TYPE_MAP.get(basis_type, "EMA")
    basis_type = coerce_smf_basis_type(basis_type)

    use_alma     = 1 if basis_type == "ALMA" else 0
    alma_w       = _alma_weights(trend_len, alma_offset, alma_sigma)
    alpha_basis  = 2.0 / (trend_len + 1)
    alpha_smooth = 2.0 / (basis_smooth + 1)   # 1.0 when basis_smooth=1 → no-op
    alpha_mf     = 2.0 / (mf_smooth + 1)      # 1.0 when mf_smooth=1 → no-op

    # Convert DataFrames to C-contiguous float64 2-D arrays
    o_np = np.ascontiguousarray(open_df.values,  dtype=np.float64)
    h_np = np.ascontiguousarray(high_df.values,  dtype=np.float64)
    l_np = np.ascontiguousarray(low_df.values,   dtype=np.float64)
    c_np = np.ascontiguousarray(close_df.values, dtype=np.float64)
    v_np = np.ascontiguousarray(vol_df.values,   dtype=np.float64)

    b_c, b_o, mf, strn, upper, lower, sig, sw_up, sw_dn, bul_d, bea_d, str_sg = _smf_nb(
        o_np, h_np, l_np, c_np, v_np,
        alma_w,
        use_alma,
        trend_len,
        alpha_basis,
        alpha_smooth,
        mf_len,
        alpha_mf,
        mf_power,
        atr_len,
        min_mult,
        max_mult,
        dot_cooldown,
    )

    idx  = close_df.index
    syms = list(close_df.columns)
    results: dict = {}
    for j, sym in enumerate(syms):
        results[sym] = {
            "last_signal":     pd.Series(sig[:, j],    index=idx),
            "switch_up":       pd.Series(sw_up[:, j],  index=idx),
            "switch_down":     pd.Series(sw_dn[:, j],  index=idx),
            "upper":           pd.Series(upper[:, j],  index=idx),
            "lower":           pd.Series(lower[:, j],  index=idx),
            "b_close":         pd.Series(b_c[:, j],    index=idx),
            "b_open":          pd.Series(b_o[:, j],    index=idx),
            "mf_smooth":       pd.Series(mf[:, j],     index=idx),
            "strength":        pd.Series(strn[:, j],   index=idx),
            "bull_dot":        pd.Series(bul_d[:, j],  index=idx),
            "bear_dot":        pd.Series(bea_d[:, j],  index=idx),
            "strength_signed": pd.Series(str_sg[:, j], index=idx),
        }
    return results


def smf_regime_masks(smf: dict) -> tuple:
    """
    Extract regime boolean DataFrames from :func:`smart_money_flow_cloud` output.

    Parameters
    ----------
    smf : dict
        Return value of :func:`smart_money_flow_cloud`.

    Returns
    -------
    bull_regime_df : pd.DataFrame[bool]
        True on bars where SMF state == +1 (bullish), indexed date × symbol.
    switch_down_df : pd.DataFrame[bool]
        True on the bar the regime flips to bearish (force-exit signal).
    """
    syms = list(smf.keys())
    bull = pd.DataFrame({sym: smf[sym]["last_signal"] == 1 for sym in syms})
    down = pd.DataFrame({sym: smf[sym]["switch_down"]      for sym in syms})
    return bull, down
