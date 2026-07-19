"""TTM Squeeze — aligned with ``backtest_005`` / ``breakout_ttm_v1``."""

from __future__ import annotations

import numpy as np
import vectorbt as vbt

from app.services.strategies.breakout_ttm_v1 import FIXED_TTM_PARAMS

# squeeze_state values (default = no squeeze)
SQUEEZE_NONE = 0
SQUEEZE_ON = 1
SQUEEZE_OFF = 2

_V1 = FIXED_TTM_PARAMS["v1"]

def _as_np(x) -> np.ndarray:
    if hasattr(x, "to_numpy"):
        return x.to_numpy()
    return np.asarray(x)


def squeeze_ttm(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    bb_period: int = _V1["bb_window"],
    bb_mult: float = _V1["bb_multiplier"],
    bb_matype: int = _V1["bb_matype"],
    kc_period: int = _V1["kc_window"],
    kc_mult: float = _V1["kc_multiplier"],
    kc_atr_period: int | None = _V1["kc_atr_period"],
    donichan_period: int = _V1["donichan_window"],
    osc_smoothing_period: int = _V1["osc_smoothing_period"],
):
    """
    TTM Squeeze momentum + 3-state squeeze flag.
    Default kwargs match ``ttm_best_params_005.json`` v2 (via ``FIXED_TTM_PARAMS``).
    """
    bb = vbt.IndicatorFactory.from_talib("BBANDS").run(
        close,
        timeperiod=bb_period,
        nbdevup=bb_mult,
        nbdevdn=bb_mult,
        matype=bb_matype,
    )
    atr = vbt.IndicatorFactory.from_talib("ATR").run(
        high, low, close, timeperiod=kc_atr_period or kc_period,
    )
    ema = vbt.IndicatorFactory.from_talib("EMA").run(close, timeperiod=kc_period)

    bb_upper = _as_np(bb.upperband)

    ema_np = _as_np(ema.real)
    atr_np = _as_np(atr.real)
    kc_upper = ema_np + kc_mult * atr_np

    diff = bb_upper - kc_upper

    squeeze_state = np.full(diff.shape, SQUEEZE_NONE, dtype=np.int8)
    valid = np.isfinite(diff)
    squeeze_state[valid & (diff < 0)] = SQUEEZE_ON
    squeeze_state[valid & (diff > 0)] = SQUEEZE_OFF

    sma = vbt.IndicatorFactory.from_talib("SMA").run(close, timeperiod=donichan_period).real
    hh = vbt.IndicatorFactory.from_talib("MAX").run(high, timeperiod=donichan_period).real
    ll = vbt.IndicatorFactory.from_talib("MIN").run(low, timeperiod=donichan_period).real
    histogram = close - ((hh + ll) / 2 + sma) / 2

    ttms = vbt.IndicatorFactory.from_talib("LINEARREG").run(
        histogram, timeperiod=osc_smoothing_period,
    ).real

    return diff, ttms, squeeze_state
