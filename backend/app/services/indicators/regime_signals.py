"""
Market regime indicators used as entry filters.

Three boolean signals per (date, symbol):
  risk_regime        — per-symbol GKYZ hysteresis (True = high-vol / risk-on)
  market_risk_regime — VNINDEX GKYZ hysteresis    (True = high-vol / risk-on)
  breadth_regime     — McClellan Summation > SMA20 (True = bullish breadth)
"""

import numpy as np
import pandas as pd

from .gkyz_volatility import calculate_gkyz_volatility

GKYZ_WINDOW: int   = 21
GKYZ_UPPER:  float = 0.8
GKYZ_LOWER:  float = 0.2


def gkyz_hysteresis(
    open_arr:  np.ndarray,
    high_arr:  np.ndarray,
    low_arr:   np.ndarray,
    close_arr: np.ndarray,
    index:     pd.Index,
    window:    int   = GKYZ_WINDOW,
    upper:     float = GKYZ_UPPER,
    lower:     float = GKYZ_LOWER,
) -> pd.Series:
    """
    GKYZ normalized volatility with sticky hysteresis thresholds.

    Returns a boolean Series (same index as `index`):
      True  — risk-on  (GKYZ crossed above `upper`, stays until it crosses below `lower`)
      False — risk-off (GKYZ crossed below `lower`, stays until it crosses above `upper`)
    """
    arr = calculate_gkyz_volatility(
        open_arr.astype(np.float64), high_arr.astype(np.float64),
        low_arr.astype(np.float64),  close_arr.astype(np.float64),
        window=window, normalize=True,
    )
    risk_on = np.zeros(len(arr), dtype=bool)
    state   = False
    for i, v in enumerate(arr):
        if not np.isnan(v):
            if not state and v > upper:
                state = True
            elif state and v < lower:
                state = False
        risk_on[i] = state
    return pd.Series(risk_on, index=index)


def mcclellan_breadth_regime(close_df: pd.DataFrame) -> pd.Series:
    """
    McClellan Summation Index > 20-day SMA.

    Args:
        close_df: wide-format close prices (date index, symbol columns)

    Returns:
        Boolean Series indexed by date.
        True  — bullish breadth (Summation above its 20-day SMA)
        False — bearish breadth
    """
    daily_chg = close_df.diff()
    advances  = (daily_chg > 0).sum(axis=1)
    declines  = (daily_chg < 0).sum(axis=1)
    total     = (advances + declines).replace(0, np.nan)
    ad_ratio  = (advances - declines) / total * 1000
    mcc_osc   = (
        ad_ratio.ewm(span=19, adjust=False).mean()
        - ad_ratio.ewm(span=39, adjust=False).mean()
    )
    mcc_sum   = mcc_osc.cumsum()
    sma20     = mcc_sum.rolling(20, min_periods=20).mean()
    return mcc_sum > sma20


def compute_regime_signals(
    stocks: pd.DataFrame,
    market_symbol: str = 'VNINDEX',
    gkyz_window: int   = GKYZ_WINDOW,
    gkyz_upper:  float = GKYZ_UPPER,
    gkyz_lower:  float = GKYZ_LOWER,
) -> pd.DataFrame:
    """
    Compute all three regime signals for every (date, symbol) in *stocks*.

    Args:
        stocks:        MultiLevel-column DataFrame with levels [ohlcv, symbol]
                       as produced by ``stocks.unstack(level=1)``.
        market_symbol: Column name for the market index used as the market-wide regime.
        gkyz_window:   Lookback window for GKYZ volatility.
        gkyz_upper:    Threshold to flip into risk-on state.
        gkyz_lower:    Threshold to flip back to risk-off state.

    Returns:
        DataFrame with columns [date, symbol, risk_regime,
        market_risk_regime, breadth_regime].
    """
    close_df = stocks['close']
    dates    = stocks.index
    symbols  = close_df.columns

    # ── VNINDEX market regime (single series, broadcast to all symbols) ───────
    market_regime = gkyz_hysteresis(
        stocks['open'][market_symbol].values,
        stocks['high'][market_symbol].values,
        stocks['low'][market_symbol].values,
        stocks['close'][market_symbol].values,
        index=dates,
        window=gkyz_window, upper=gkyz_upper, lower=gkyz_lower,
    )

    # ── Per-symbol GKYZ regime ────────────────────────────────────────────────
    sym_regime = pd.DataFrame(False, index=dates, columns=symbols)
    for sym in symbols:
        sym_regime[sym] = gkyz_hysteresis(
            stocks['open'][sym].values,
            stocks['high'][sym].values,
            stocks['low'][sym].values,
            stocks['close'][sym].values,
            index=dates,
            window=gkyz_window, upper=gkyz_upper, lower=gkyz_lower,
        ).values

    # ── Breadth regime ────────────────────────────────────────────────────────
    breadth = mcclellan_breadth_regime(close_df)

    # ── Flatten to (date, symbol) rows ────────────────────────────────────────
    result = (
        sym_regime.stack()
        .rename('risk_regime')
        .reset_index()
    )
    result.columns = pd.Index(['date', 'symbol', 'risk_regime'])
    result['market_risk_regime'] = result['date'].map(market_regime)
    result['breadth_regime']     = result['date'].map(breadth)

    return result
