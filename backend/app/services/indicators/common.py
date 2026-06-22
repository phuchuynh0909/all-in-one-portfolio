from numba import njit
import numba as nb
import numpy as np

@njit
def exrem_func_nb(entries, exits):
    rows, cols = entries.shape
    result = np.full(entries.shape, False)
    for col in nb.prange(cols):
        in_position = False
        for i in range(rows):
            if np.isnan(entries[i, col]) or np.isnan(exits[i, col]):
                continue
            if entries[i, col] and not in_position:
                in_position = True
                result[i, col] = True
            elif exits[i, col] and in_position:
                in_position = False
    return result


@njit
def lowest_at_entry(low, entry):
    lowest_low = np.full(entry.shape, np.nan, dtype=np.float64)
    for col in range(lowest_low.shape[1]):
        for i in range(1, lowest_low.shape[0]):
            if np.isnan(low[i, col]):
                lowest_low[i, col] = np.nan
                continue    
            if entry[i, col]:
                lowest_low[i, col] = low[i, col]
            else:
                lowest_low[i, col] = lowest_low[i-1, col]
    return lowest_low


@nb.njit(cache=True)
def shift_2d(arr: np.ndarray, num: int, fill_value: float = np.nan) -> np.ndarray:
    """Shift a 2-D array along axis 0 by `num` rows (positive = shift down)."""
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


@nb.njit(cache=True)
def count_consecutive_neg_2d(arr: np.ndarray) -> np.ndarray:
    """Count consecutive negative values per column, resetting to 0 on non-negative."""
    n, m = arr.shape
    out  = np.zeros((n, m), dtype=np.int64)
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
def autocorr_2d(prices_np: np.ndarray, ret_period: int = 5, window: int = 60) -> np.ndarray:
    """
    Rolling lag-1 autocorrelation of `ret_period`-bar returns over a `window`-bar lookback.

    Positive → returns tend to continue (momentum).
    Negative → returns mean-revert.
    NaN for the first (ret_period + window - 1) rows.
    """
    n, m = prices_np.shape
    out  = np.full((n, m), np.nan)
    for j in range(m):
        for i in range(ret_period + window - 1, n):
            rets  = np.empty(window)
            valid = True
            for k in range(window):
                cur  = i - window + 1 + k
                prev = cur - ret_period
                p_c  = prices_np[cur, j]
                p_p  = prices_np[prev, j]
                if p_p == 0.0 or np.isnan(p_c) or np.isnan(p_p):
                    valid = False
                    break
                rets[k] = p_c / p_p - 1.0
            if not valid:
                continue
            mean = 0.0
            for k in range(window):
                mean += rets[k]
            mean /= window
            cov = 0.0
            var = 0.0
            for k in range(1, window):
                dx   = rets[k]     - mean
                dx1  = rets[k - 1] - mean
                cov += dx * dx1
                var += dx * dx
            var += (rets[0] - mean) ** 2
            if var > 0.0:
                out[i, j] = cov / var
    return out


@nb.njit(cache=True)
def ema_span_2d(arr: np.ndarray, span: int) -> np.ndarray:
    """EMA with pandas-compatible ewm(span=span, adjust=False)."""
    alpha = 2.0 / (span + 1)
    n, m = arr.shape
    out = np.empty_like(arr)
    for j in range(m):
        out[0, j] = arr[0, j]
        for i in range(1, n):
            out[i, j] = alpha * arr[i, j] + (1.0 - alpha) * out[i - 1, j]
    return out


@nb.njit(cache=True)
def obv_2d(close_np: np.ndarray, volume_np: np.ndarray) -> np.ndarray:
    """
    On-Balance Volume for 2-D arrays (rows=time, cols=symbols).

    Accumulates +volume on up-closes and -volume on down-closes, column-wise.
    Returns float64 array of the same shape.
    """
    n, m = close_np.shape
    out  = np.zeros((n, m), dtype=np.float64)
    for j in range(m):
        obv = 0.0
        for i in range(n):
            if i > 0:
                if close_np[i, j] > close_np[i - 1, j]:
                    obv += volume_np[i, j]
                elif close_np[i, j] < close_np[i - 1, j]:
                    obv -= volume_np[i, j]
            out[i, j] = obv
    return out


EPS = 1e-10

@njit
def relative_strength_nb(close, benmark_close, window):
    rs = np.full(close.shape, np.nan, dtype=np.float64)
    mrs = np.full(close.shape, np.nan, dtype=np.float64)
    rs_ratio = close / (benmark_close + EPS)

    mean_rs_ratio = np.full(close.shape, np.nan, dtype=np.float64)
    for col in range(close.shape[1]):
        for i in range(window, close.shape[0]):
            mean_rs_ratio[i, col] = np.mean(rs_ratio[i-window:i, col])

    for col in range(close.shape[1]):
        for i in range(window, close.shape[0]):
            rs[i, col] = (rs_ratio[i, col] / (rs_ratio[i-window, col] + EPS)) * 100 - 100 
            mrs[i, col] = ((rs_ratio[i, col] / (mean_rs_ratio[i, col] + EPS)) - 1) * 100
            
    return rs, mrs