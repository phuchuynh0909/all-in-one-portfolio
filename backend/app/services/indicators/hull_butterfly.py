"""
Hull Butterfly Oscillator (HBO) — LuxAlgo.

Port of the TradingView PineScript "Hull Butterfly Oscillator [LuxAlgo]".

The oscillator ``hso`` is the difference between a Hull-weighted convolution of
``src`` and its time-reversed mirror; a cumulative-mean band (``cmean``) scaled
by ``mult`` defines the ±levels. The discrete state ``os`` is:

    os = +1  when hso is rising while below the -cmean band   (bullish reversal)
    os = -1  when hso is falling while above the +cmean band  (bearish reversal)
    os =  0  on a cross of either band, else it holds.

Column-wise (rows = time, cols = symbols) to match the backtest layout.
"""
import numpy as np
import numba as nb


def hull_coeffs(length: int) -> np.ndarray:
    """Precompute the Hull convolution coefficients for a given ``length``.

    Reproduces the ``barstate.isfirst`` coefficient build in the PineScript
    (linearly-combined WMA coeffs -> zero pad -> WMA convolution). ``unshift``
    prepends, so each accumulation stage is reversed vs. append order.
    """
    short_len = int(length / 2)
    hull_len = int(np.sqrt(length))
    den1 = short_len * (short_len + 1) / 2.0
    den2 = length * (length + 1) / 2.0
    den3 = hull_len * (hull_len + 1) / 2.0

    tmp = []
    for i in range(length):
        sum1 = max(short_len - i, 0)
        sum2 = length - i
        tmp.append(2.0 * (sum1 / den1) - (sum2 / den2))
    lcwa = list(reversed(tmp))                      # unshift order
    lcwa = [0.0] * (hull_len - 1) + lcwa            # zero padding (unshift 0s)

    size = len(lcwa)
    tmp2 = []
    for i in range(hull_len, size):
        s3 = 0.0
        for j in range(i - hull_len, i):
            s3 += lcwa[j] * (i - j)
        tmp2.append(s3 / den3)
    hull = list(reversed(tmp2))                     # unshift order
    return np.asarray(hull, dtype=np.float64)


@nb.njit(cache=True)
def hull_butterfly_2d(src: np.ndarray, coeffs: np.ndarray, mult: float):
    """Compute (hso, os) column-wise for the given coefficients.

    ``hso[t] = sum_i src[t-i]*c[i] - sum_i src[t-(L-i)]*c[i]`` where ``L =
    len(coeffs)-1``. ``cmean`` is the cumulative mean of |hso| times ``mult``.
    """
    n, m = src.shape
    L = coeffs.shape[0] - 1
    hso = np.full((n, m), np.nan)
    os = np.zeros((n, m), dtype=np.float64)
    for j in range(m):
        cum_abs = 0.0
        count = 0
        prev_hso = np.nan
        prev_cmean = np.nan
        prev_os = 0.0
        for t in range(n):
            if t < L:
                continue
            hma = 0.0
            inv = 0.0
            valid = True
            for i in range(L + 1):
                a = src[t - i, j]
                b = src[t - (L - i), j]
                if np.isnan(a) or np.isnan(b):
                    valid = False
                    break
                hma += a * coeffs[i]
                inv += b * coeffs[i]
            if not valid:
                continue
            h = hma - inv
            hso[t, j] = h

            count += 1
            cum_abs += abs(h)
            cmean = cum_abs / count * mult

            if not np.isnan(prev_hso) and not np.isnan(prev_cmean):
                crossed = (
                    (prev_hso < prev_cmean and h > cmean)
                    or (prev_hso > prev_cmean and h < cmean)
                    or (prev_hso < -prev_cmean and h > -cmean)
                    or (prev_hso > -prev_cmean and h < -cmean)
                )
                if crossed:
                    cur = 0.0
                elif h < prev_hso and h > cmean:
                    cur = -1.0
                elif h > prev_hso and h < -cmean:
                    cur = 1.0
                else:
                    cur = prev_os
            else:
                cur = 0.0

            os[t, j] = cur
            prev_os = cur
            prev_hso = h
            prev_cmean = cmean
    return hso, os


def hull_butterfly(src, length: int = 14, mult: float = 2.0):
    """Full HBO for 2-D (or 1-D) ``src``. Returns (hso, os) arrays like ``src``."""
    src = np.asarray(src, dtype=np.float64)
    if src.ndim == 1:
        src = src.reshape(-1, 1)
    coeffs = hull_coeffs(length)
    return hull_butterfly_2d(src, coeffs, float(mult))
