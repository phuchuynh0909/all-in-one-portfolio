"""
Hawkes BSI indicators and signal state machine.

Used by the backtest in analysis/backtest_hawkes_quant.py. The live signal
worker that also consumed this was removed; the module is kept for that
research path, and the backend has its own BVC indicator in
backend/app/services/indicators/hawkes_bvc.py for the Future page study.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_hawkes_bsi(
    bars: pd.DataFrame,
    kappa: float,
    quantile_lookback: int = 20,
    q_lo_pct: float = 5.0,
    q_hi_pct: float = 95.0,
) -> pd.DataFrame:
    """
    Hawkes BSI with rolling quantile thresholds.

    BSI[i] = BSI[i-1] * exp(-κ) + (buyvolume[i] - sellvolume[i])
    q_lo[i] = q_lo_pct-th percentile of BSI over last quantile_lookback bars
    q_hi[i] = q_hi_pct-th percentile of BSI over last quantile_lookback bars

    Adds columns: bsi, q_lo, q_hi
    """
    alpha = np.exp(-kappa)
    dv    = (bars["buyvolume"].fillna(0) - bars["sellvolume"].fillna(0)).to_numpy(float)
    bsi   = np.empty_like(dv)
    val   = 0.0
    for i in range(len(dv)):
        val    = val * alpha + dv[i]
        bsi[i] = val

    s      = pd.Series(bsi)
    q_lo_s = s.rolling(quantile_lookback, min_periods=2).quantile(q_lo_pct / 100.0)
    q_hi_s = s.rolling(quantile_lookback, min_periods=2).quantile(q_hi_pct / 100.0)

    bars         = bars.copy()
    bars["bsi"]  = bsi
    bars["q_lo"] = q_lo_s.to_numpy()
    bars["q_hi"] = q_hi_s.to_numpy()
    return bars


def compute_kama(
    bars: pd.DataFrame,
    period: int = 10,
    fast: int = 2,
    slow: int = 30,
) -> pd.DataFrame:
    """
    Kaufman's Adaptive Moving Average.

    ER[i]   = |close[i] - close[i-period]| / sum(|close[k]-close[k-1]|, k=i-period+1..i)
    fast_sc = 2 / (fast + 1)
    slow_sc = 2 / (slow + 1)
    sc[i]   = (ER[i] * (fast_sc - slow_sc) + slow_sc) ** 2
    KAMA[i] = KAMA[i-1] + sc[i] * (close[i] - KAMA[i-1])

    Adds column: kama
    """
    closes  = bars["close"].to_numpy(float)
    n       = len(closes)
    kama    = np.full(n, np.nan)
    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    kama[period - 1] = closes[period - 1]

    for i in range(period, n):
        direction  = abs(closes[i] - closes[i - period])
        volatility = np.sum(np.abs(np.diff(closes[i - period: i + 1])))
        er         = direction / volatility if volatility > 0 else 0.0
        sc         = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        kama[i]    = kama[i - 1] + sc * (closes[i] - kama[i - 1])

    bars         = bars.copy()
    bars["kama"] = kama
    return bars


def compute_alma(
    bars: pd.DataFrame,
    window: int = 9,
    offset: float = 0.85,
    sigma: float = 6.0,
) -> pd.DataFrame:
    """
    Arnaud Legoux Moving Average (ALMA).

    m    = floor(offset * (window - 1))
    s    = window / sigma
    w[k] = exp(-((k - m)^2) / (2 * s^2))   for k in 0..window-1
    ALMA[i] = sum(w[k] * close[i - window + 1 + k]) / sum(w)

    Adds column: alma
    """
    closes = bars["close"].to_numpy(float)
    n      = len(closes)
    result = np.full(n, np.nan)
    m      = int(np.floor(offset * (window - 1)))
    s      = window / sigma
    w      = np.array([np.exp(-((k - m) ** 2) / (2 * s ** 2)) for k in range(window)])
    w     /= w.sum()

    for i in range(window - 1, n):
        result[i] = np.dot(w, closes[i - window + 1: i + 1])

    bars         = bars.copy()
    bars["alma"] = result
    return bars


def generate_signals(
    bars: pd.DataFrame,
    allow_short: bool = True,
    use_kama_gate: bool = True,
    sl_bars: int = 10,
    calm_bars: int = 5,
    calm_threshold: float = 500.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Quantile-breakout directional state machine.

    LONG  entry: BSI crosses above q_hi
                 price_now > price_at_last_below_q_lo  AND  close > KAMA (if gate)
                 at least 1 of last calm_bars bars has abs(BSI) < calm_threshold
    SHORT entry: BSI crosses below q_lo
                 close < KAMA (if gate)
                 at least 1 of last calm_bars bars has abs(BSI) < calm_threshold

    SL (active for first sl_bars bars after entry):
      long  → min(low[i-sl_bars+1 : i+1])
      short → max(high[i-sl_bars+1 : i+1])

    LONG  exit: BSI drops below q_lo
    SHORT exit: BSI rises above q_hi

    Returns: long_entries, short_entries, long_exits, short_exits (bool[n])
    """
    bsi    = bars["bsi"].to_numpy(float)
    q_lo   = bars["q_lo"].to_numpy(float)
    q_hi   = bars["q_hi"].to_numpy(float)
    closes = bars["close"].to_numpy(float)
    highs  = bars["high"].to_numpy(float)
    lows   = bars["low"].to_numpy(float)
    kama   = bars["kama"].to_numpy(float) if (use_kama_gate and "kama" in bars.columns) else None
    n      = len(bars)

    long_entries  = np.zeros(n, bool)
    short_entries = np.zeros(n, bool)
    long_exits    = np.zeros(n, bool)
    short_exits   = np.zeros(n, bool)

    pos              = 0
    entry_bar        = -1
    bars_in_trade    = 0
    sl_price         = np.nan
    in_upper_zone    = False
    in_lower_zone    = False
    last_below_price = np.nan
    last_below_bar   = -1

    for i in range(n - 1):
        b = bsi[i]
        if np.isnan(b) or np.isnan(q_lo[i]) or np.isnan(q_hi[i]):
            continue

        if b < q_lo[i]:
            last_below_price = closes[i]
            last_below_bar   = i

        if pos != 0:
            if i != entry_bar:
                bars_in_trade += 1

                if bars_in_trade <= sl_bars and not np.isnan(sl_price):
                    sl_hit = (pos == +1 and lows[i] <= sl_price) or \
                             (pos == -1 and highs[i] >= sl_price)
                    if sl_hit:
                        if pos == +1:
                            long_exits[i + 1]  = True
                        else:
                            short_exits[i + 1] = True
                        pos = 0; bars_in_trade = 0; sl_price = np.nan
                        in_upper_zone = False; in_lower_zone = False
                        continue

                if pos == +1 and b < q_lo[i]:
                    long_exits[i + 1] = True
                    pos = 0; bars_in_trade = 0; sl_price = np.nan
                    in_upper_zone = False; in_lower_zone = False
                elif pos == -1 and b >= q_hi[i]:
                    short_exits[i + 1] = True
                    pos = 0; bars_in_trade = 0; sl_price = np.nan
                    in_upper_zone = False; in_lower_zone = False
            continue

        win_lo   = max(0, i - sl_bars + 1)
        k_val    = kama[i] if kama is not None else np.nan
        calm_lo  = max(0, i - calm_bars + 1)
        had_calm = bool(np.any(np.abs(bsi[calm_lo: i + 1]) < calm_threshold))

        if b >= q_hi[i]:
            in_lower_zone = False
            if not in_upper_zone:
                in_upper_zone = True
                if had_calm and last_below_bar >= 0 and not np.isnan(last_below_price):
                    price_up = closes[i] > last_below_price
                    kama_ok  = kama is None or np.isnan(k_val) or closes[i] > k_val
                    if price_up and kama_ok:
                        j = i + 1
                        long_entries[j] = True
                        pos = +1; entry_bar = j; bars_in_trade = 0
                        sl_price = float(np.min(lows[win_lo: i + 1]))

        elif b < q_lo[i]:
            in_upper_zone = False
            if not in_lower_zone:
                in_lower_zone = True
                if allow_short and had_calm:
                    kama_ok = kama is None or np.isnan(k_val) or closes[i] < k_val
                    if kama_ok:
                        j = i + 1
                        short_entries[j] = True
                        pos = -1; entry_bar = j; bars_in_trade = 0
                        sl_price = float(np.max(highs[win_lo: i + 1]))

        else:
            in_upper_zone = False
            in_lower_zone = False

    return long_entries, short_entries, long_exits, short_exits
