"""
Swing (pivot) high/low detection.

A swing high is a bar whose high is the highest high over ``swing_length``
candles *before and after* it; a swing low is the mirror on lows. This matches
the smart-money-concepts ``swing_highs_lows`` definition.

⚠️  Look-ahead: because a swing needs ``swing_length`` *future* candles to be
confirmed, any centered detector peeks forward at the pivot bar. Two functions
are provided:

  * ``swing_highs_lows`` — faithful SMC port (HighLow / Level). It uses a
    centered window AND forces the first/last bars, so it reads the whole
    series. Use it for charting / analysis on COMPLETE data only — never feed
    its raw output into a live/backtest decision (that is look-ahead bias).

  * ``swing_high_low`` — backtest-safe. Same centered swing definition, but a
    pivot is only revealed ``swing_length`` bars later (once its forward window
    has closed), so the "last swing" arrays it returns carry no look-ahead.
"""
import numpy as np
import pandas as pd
from numba import njit


# ── Faithful smart-money-concepts port (LOOK-AHEAD — charting only) ────────────
def swing_highs_lows(high, low, swing_length: int = 50):
    """
    Port of smartmoneyconcepts.swing_highs_lows.

    Returns a DataFrame with:
      HighLow : 1 at a swing high, -1 at a swing low, NaN otherwise
      Level   : the high (swing high) or low (swing low) at that bar
    """
    high = pd.Series(np.asarray(high, dtype=np.float64))
    low = pd.Series(np.asarray(low, dtype=np.float64))

    win = swing_length * 2
    shl = np.where(
        high == high.shift(-(win // 2)).rolling(win).max(),
        1,
        np.where(
            low == low.shift(-(win // 2)).rolling(win).min(),
            -1,
            np.nan,
        ),
    )

    # collapse consecutive same-type pivots, keeping the more extreme one
    while True:
        positions = np.where(~np.isnan(shl))[0]
        if len(positions) < 2:
            break

        current = shl[positions[:-1]]
        nxt = shl[positions[1:]]
        highs = high.iloc[positions[:-1]].values
        lows = low.iloc[positions[:-1]].values
        next_highs = high.iloc[positions[1:]].values
        next_lows = low.iloc[positions[1:]].values

        index_to_remove = np.zeros(len(positions), dtype=bool)

        consecutive_highs = (current == 1) & (nxt == 1)
        index_to_remove[:-1] |= consecutive_highs & (highs < next_highs)
        index_to_remove[1:] |= consecutive_highs & (highs >= next_highs)

        consecutive_lows = (current == -1) & (nxt == -1)
        index_to_remove[:-1] |= consecutive_lows & (lows > next_lows)
        index_to_remove[1:] |= consecutive_lows & (lows <= next_lows)

        if not index_to_remove.any():
            break
        shl[positions[index_to_remove]] = np.nan

    positions = np.where(~np.isnan(shl))[0]
    if len(positions) > 0:
        if shl[positions[0]] == 1:
            shl[0] = -1
        if shl[positions[0]] == -1:
            shl[0] = 1
        if shl[positions[-1]] == -1:
            shl[-1] = 1
        if shl[positions[-1]] == 1:
            shl[-1] = -1

    level = np.where(
        ~np.isnan(shl),
        np.where(shl == 1, high.values, low.values),
        np.nan,
    )
    return pd.DataFrame({"HighLow": shl, "Level": level})


# ── Backtest-safe (no look-ahead) swing tracking ──────────────────────────────
@njit(cache=True)
def _centered_pivots(high, low, sl):
    """swing high at i: high[i] >= every high in [i-sl, i+sl] (mirror for lows)."""
    n = high.shape[0]
    is_sh = np.zeros(n, dtype=np.bool_)
    is_sl = np.zeros(n, dtype=np.bool_)
    for i in range(sl, n - sl):
        h = high[i]
        l = low[i]
        sh = True
        slw = True
        for k in range(1, sl + 1):
            if high[i - k] > h or high[i + k] > h:
                sh = False
            if low[i - k] < l or low[i + k] < l:
                slw = False
            if not sh and not slw:
                break
        is_sh[i] = sh
        is_sl[i] = slw
    return is_sh, is_sl


@njit(cache=True)
def _last_confirmed(is_sh, is_sl, high, low, lag):
    """As-of last swing-high/low price + bar index, revealed `lag` bars late."""
    n = high.shape[0]
    sh_price = np.full(n, np.nan)
    sl_price = np.full(n, np.nan)
    sh_idx = np.full(n, -1, dtype=np.int64)
    sl_idx = np.full(n, -1, dtype=np.int64)
    cur_shp = np.nan
    cur_slp = np.nan
    cur_shi = -1
    cur_sli = -1
    for t in range(n):
        p = t - lag          # pivot at p becomes confirmed exactly at bar t
        if p >= 0:
            if is_sh[p]:
                cur_shp = high[p]
                cur_shi = p
            if is_sl[p]:
                cur_slp = low[p]
                cur_sli = p
        sh_price[t] = cur_shp
        sl_price[t] = cur_slp
        sh_idx[t] = cur_shi
        sl_idx[t] = cur_sli
    return sh_price, sl_price, sh_idx, sl_idx


def swing_high_low(high, low, swing_length: int = 20):
    """
    Look-ahead-free swing tracking for backtests.

    Same centered swing definition as ``swing_highs_lows`` (highest high /
    lowest low over ``swing_length`` bars each side), but every pivot is only
    revealed ``swing_length`` bars after it prints — so the "last swing" arrays
    are safe to read bar-by-bar.

    Returns a dict of length-n arrays:
      is_swing_high / is_swing_low : bool markers at the pivot bar
      last_sh_price / last_sl_price : price of the last CONFIRMED swing high/low
      last_sh_idx   / last_sl_idx   : bar index of those pivots (-1 until first)
    """
    high = np.ascontiguousarray(high, dtype=np.float64)
    low = np.ascontiguousarray(low, dtype=np.float64)
    sl = int(swing_length)
    is_sh, is_slw = _centered_pivots(high, low, sl)
    sh_price, sl_price, sh_idx, sl_idx = _last_confirmed(is_sh, is_slw, high, low, sl)
    return {
        "is_swing_high": is_sh,
        "is_swing_low": is_slw,
        "last_sh_price": sh_price,
        "last_sl_price": sl_price,
        "last_sh_idx": sh_idx,
        "last_sl_idx": sl_idx,
    }
