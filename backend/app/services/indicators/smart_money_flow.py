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


# ── Default (optimised) params ─────────────────────────────────────────────────
SMF_DEFAULTS = {
    "trend_len":   34,
    "basis_type":  "EMA",
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
    hl       = h - l
    clv      = np.where(hl == 0, 0.0, ((c - l) - (h - c)) / hl)
    raw_flow = clv * v
    mf_num   = pd.Series(raw_flow).rolling(mf_len).sum().values
    mf_den   = pd.Series(np.abs(raw_flow)).rolling(mf_len).sum().values
    mf_raw   = np.where(mf_den == 0, 0.0, mf_num / mf_den)
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
    Compute Smart Money Flow Cloud for multiple symbols.

    Thin multi-symbol wrapper around :func:`smart_money_flow`.

    Parameters
    ----------
    open_df, high_df, low_df, close_df, vol_df : pd.DataFrame
        OHLCV DataFrames aligned on the same DatetimeIndex (rows=time, cols=symbols).
    basis_type : int or str
        0 or "EMA" (default) / 1 or "ALMA".
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

    idx = close_df.index
    results: dict = {}

    for sym in close_df.columns:
        raw = smart_money_flow(
            open_=   open_df[sym].values,
            high=    high_df[sym].values,
            low=     low_df[sym].values,
            close=   close_df[sym].values,
            volume=  vol_df[sym].values,
            trend_len=trend_len,
            basis_type=basis_type,
            alma_offset=alma_offset,
            alma_sigma=alma_sigma,
            basis_smooth=basis_smooth,
            mf_len=mf_len,
            mf_smooth=mf_smooth,
            mf_power=mf_power,
            atr_len=atr_len,
            min_mult=min_mult,
            max_mult=max_mult,
            dot_cooldown=dot_cooldown,
        )
        results[sym] = {k: pd.Series(v, index=idx) for k, v in raw.items()}

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
