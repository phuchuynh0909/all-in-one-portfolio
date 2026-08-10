import numpy as np
import numba as nb
import talib


@nb.njit(cache=True)
def _chandelier_nb(close, high, low, atr, multiplier, length):
    """
    Numba kernel for Chandelier Exit on 2-D arrays (rows=time, cols=symbols).

    Returns:
        chand     — trailing stop line (nan before warmup)
        direction — +1 long / -1 short (int64)
    """
    n, m = close.shape
    chand     = np.full((n, m), np.nan)
    direction = np.zeros((n, m), dtype=np.int64)
    for col in range(m):
        d = 1
        for i in range(n):
            c = close[i, col]
            a = atr[i, col]
            if np.isnan(c) or np.isnan(a) or i < length - 1:
                direction[i, col] = d
                continue
            hc = -1e18; hh = -1e18; lc = 1e18; ll = 1e18
            for k in range(i - length + 1, i + 1):
                cc = close[k, col]; hk = high[k, col]; lk = low[k, col]
                if cc > hc: hc = cc
                if hk > hh: hh = hk
                if cc < lc: lc = cc
                if lk < ll: ll = lk
            highest_high = 0.5 * (hc + hh)
            lowest_low   = 0.5 * (lc + ll)
            chand_long  = highest_high - a * multiplier
            chand_short = lowest_low   + a * multiplier
            if c > chand_short:
                d = 1
            elif c < chand_long:
                d = -1
            direction[i, col] = d
            chand[i, col] = chand_long if d > 0 else chand_short
    return chand, direction


def chandelier_exit(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    length: int = 31,
    multiplier: float = 2.2,
) -> dict:
    """
    Chandelier Exit indicator (port of Pine Script reference).

    Pine logic:
        atr         = ta.atr(length)
        highestHigh = avg(highest(close, length), highest(high, length))
        lowestLow   = avg(lowest(close, length),  lowest(low,  length))
        chandLong   = highestHigh - atr * mult
        chandShort  = lowestLow   + atr * mult
        dir         = close > chandShort ? 1 : close < chandLong ? -1 : dir[prev]

    Returns:
        value     — combined line (long value when dir=+1, short value when dir=-1)
        direction — +1 (uptrend) or -1 (downtrend), nan where not yet computed
        long      — long trail values, nan where dir != +1
        short     — short trail values, nan where dir != -1
    """
    atr = talib.ATR(high, low, close, timeperiod=length)

    highest_close = talib.MAX(close, timeperiod=length)
    highest_high  = talib.MAX(high,  timeperiod=length)
    lowest_close  = talib.MIN(close, timeperiod=length)
    lowest_low    = talib.MIN(low,   timeperiod=length)

    hh = 0.5 * (highest_close + highest_high)
    ll = 0.5 * (lowest_close  + lowest_low)

    chand_long_arr  = hh - atr * multiplier
    chand_short_arr = ll + atr * multiplier

    n = len(close)
    long_out      = np.full(n, np.nan)
    short_out     = np.full(n, np.nan)
    value_out     = np.full(n, np.nan)
    direction_out = np.full(n, np.nan)
    direction     = 1

    for i in range(n):
        if np.isnan(chand_long_arr[i]) or np.isnan(chand_short_arr[i]):
            continue
        c = close[i]
        if c > chand_short_arr[i]:
            direction = 1
        elif c < chand_long_arr[i]:
            direction = -1
        direction_out[i] = direction
        if direction > 0:
            long_out[i]  = chand_long_arr[i]
            value_out[i] = chand_long_arr[i]
        else:
            short_out[i] = chand_short_arr[i]
            value_out[i] = chand_short_arr[i]

    return {
        "value":     value_out,
        "direction": direction_out,
        "long":      long_out,
        "short":     short_out,
    }
