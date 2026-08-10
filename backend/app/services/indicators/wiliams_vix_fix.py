import numpy as np
from numba import njit, prange
import numba as nb
import vectorbt as vbt
import pandas as pd
import numba

@numba.njit
def shift_numba(arr: np.ndarray, num: int, fill_value=np.nan):
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

import traceback
## Williams Vix Fix Formula
def williams_vix_fix_indicator(close, high, low, period=22, mult=2.0,
    bbl: int = 20,
    lb: int = 50,
    ph: float = 0.85, # percentile high
    ltLB: int = 40,
    mtLB: int = 14,
    strength_str: int = 1):
    
    try:
        MAX = vbt.IndicatorFactory.from_talib("MAX")
        SMA = vbt.IndicatorFactory.from_talib("SMA")
        STDDEV = vbt.IndicatorFactory.from_talib("STDDEV")
        
        # 1) WVF
        highest_close = MAX.run(close, timeperiod=period).real
        wvf = ((highest_close - low) / highest_close) * 100.0
    
        # 2) Bands
        midLine = SMA.run(wvf, timeperiod=bbl).real
        sDev = STDDEV.run(wvf, timeperiod=bbl).real * mult
        upperBand = midLine + sDev
        rangeHigh = MAX.run(wvf, timeperiod=lb).real * ph

        # # 3) Conditions
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

        upRange = (low_np > shift_numba(low_np, 1)) & (close_np > shift_numba(close_np, 1))

        filtered = (
            ((shift_numba(wvf_np, 1) >= shift_numba(upperBand_np, 1)) | (shift_numba(wvf_np, 1) >= shift_numba(rangeHigh_np, 1)))
            & (wvf_np < upperBand_np)
            & (wvf_np < rangeHigh_np)
        )

        cond_FE = (
            upRange
            & (close_np > shift_numba(close_np, strength_str))
            & ((close_np < shift_numba(close_np, ltLB)) | (close_np < shift_numba(close_np, mtLB)))
            & filtered
        )
    except Exception as e:
        print(f"Error calculating Williams Vix Fix: {e}")
        print(traceback.format_exc())
        return np.nan, np.nan, np.nan, np.nan

    return wvf, rangeHigh, filtered, cond_FE