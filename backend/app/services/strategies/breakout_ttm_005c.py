"""
TTM Breakout v1/v2/v3 with KAMA slope filter + exits matching `notebooks/backtest_005c.ipynb`.

`BreakoutTTMVersion2` in `breakout_ttm.py` uses an older signal/exit spec. Use
:class:`BreakoutTTM005C` for the same API shape (``data`` + ``get_portfolio()``) as Version2.

Module-level ``compute_signals`` and ``compute_exits`` remain available for custom wiring
(e.g. MS-sized portfolios); they are not re-exported from ``app.services.strategies``.
"""
from __future__ import annotations

import numpy as np
import numba as nb
import pandas as pd
import vectorbt as vbt

from app.services.indicators import exrem_func_nb
from app.services.indicators.trailing_sl import atr_trailing_nb
from app.services.indicators.wiliams_vix_fix import williams_vix_fix_indicator


FIXED_TTM_PARAMS = {
    'v1': {
        'bb_window': 15,
        'bb_multiplier': 1.0,
        'bb_matype': 3,
        'kc_window': 38,
        'kc_multiplier': 1.3,
        'kc_atr_period': 5,
        'donichan_window': 9,
        'osc_smoothing_period': 11,
        'atr_period': 6,
        'atr_multiplier': 2.0,
        'low_stop_lookback': 3,
        'consecutive_neg_threshold': 13,
        'william_vix_period': 17,
        'kama_period': 8,
        'kama_fast': 5,
        'kama_slow': 41,
        'kama_slope_win': 5,
        'flat_threshold_pct': 2.7,
    },
    'v2': {
        'bb_window': 15,
        'bb_multiplier': 1.3,
        'bb_matype': 0,
        'kc_window': 52,
        'kc_multiplier': 1.2,
        'kc_atr_period': 14,
        'donichan_window': 9,
        'osc_smoothing_period': 13,
        'atr_period': 7,
        'atr_multiplier': 2.6,
        'low_stop_lookback': 3,
        'consecutive_neg_threshold': 8,
        'william_vix_period': 18,
        'kama_period': 11,
        'kama_fast': 2,
        'kama_slow': 36,
        'kama_slope_win': 5,
        'flat_threshold_pct': 2.5,
    },
    'v3': {
        'bb_window': 10,
        'bb_multiplier': 1.2,
        'bb_matype': 3,
        'kc_window': 39,
        'kc_multiplier': 1.6,
        'kc_atr_period': 6,
        'donichan_window': 14,
        'osc_smoothing_period': 15,
        'atr_period': 11,
        'atr_multiplier': 2.6,
        'low_stop_lookback': 3,
        'consecutive_neg_threshold': 4,
        'william_vix_period': 17,
        'kama_period': 16,
        'kama_fast': 5,
        'kama_slow': 39,
        'kama_slope_win': 5,
        'flat_threshold_pct': 1.8,
    },
}


@nb.njit
def shift_2d(arr, num, fill_value=np.nan):
    """Shift a 2-D array along axis 0 (time axis)."""
    result = np.empty_like(arr)
    n = arr.shape[0]
    if num > 0:
        result[:num, :] = fill_value
        result[num:, :] = arr[: n - num, :]
    elif num < 0:
        result[n + num :, :] = fill_value
        result[: n + num, :] = arr[-num:, :]
    else:
        result[:, :] = arr
    return result


@nb.njit
def count_consecutive_neg_2d(arr):
    """Count consecutive negative values per column (used for TTM momentum bars)."""
    n, m = arr.shape
    out = np.zeros((n, m), dtype=np.int64)
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
def kama_2d(prices_np, period=10, fast=2, slow=30):
    """
    Kaufman's Adaptive Moving Average applied column-wise.
    """
    n, m = prices_np.shape
    out = np.full((n, m), np.nan)
    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    for j in range(m):
        kama = prices_np[period - 1, j]
        out[period - 1, j] = kama
        for i in range(period, n):
            direction = abs(prices_np[i, j] - prices_np[i - period, j])
            volatility = 0.0
            for k in range(1, period + 1):
                volatility += abs(prices_np[i - k + 1, j] - prices_np[i - k, j])
            er = direction / volatility if volatility != 0.0 else 0.0
            sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
            kama = kama + sc * (prices_np[i, j] - kama)
            out[i, j] = kama
    return out


@nb.njit(cache=True)
def slope_flat_2d(kama_np, slope_window, flat_threshold_pct):
    """
    True where KAMA is 'flat': |pct change over slope_window bars| < flat_threshold_pct.
    """
    n, m = kama_np.shape
    out = np.zeros((n, m), dtype=nb.boolean)
    for j in range(m):
        for i in range(slope_window, n):
            prev = kama_np[i - slope_window, j]
            if prev != 0.0 and not np.isnan(prev) and not np.isnan(kama_np[i, j]):
                slope_pct = abs((kama_np[i, j] - prev) / prev * 100.0)
                out[i, j] = slope_pct < flat_threshold_pct
    return out


def compute_signals(
    close,
    high,
    low,
    bb_window: int = 16,
    bb_multiplier: float = 1.0,
    bb_matype: int = 0,
    kc_window: int = 40,
    kc_multiplier: float = 1.2,
    kc_atr_period: int = 10,
    donichan_window: int = 12,
    osc_smoothing_period: int = 10,
    entry_version: str = 'v3',
    consecutive_neg_threshold: int = 7,
    william_vix_period: int = 20,
    use_kama_slope: bool = True,
    kama_period: int = 10,
    kama_fast: int = 2,
    kama_slow: int = 30,
    kama_slope_win: int = 5,
    flat_threshold_pct: float = 1.0,
):
    BB = vbt.IndicatorFactory.from_talib('BBANDS')
    ATR = vbt.IndicatorFactory.from_talib('ATR')
    EMA = vbt.IndicatorFactory.from_talib('EMA')
    SMA = vbt.IndicatorFactory.from_talib('SMA')
    MAX = vbt.IndicatorFactory.from_talib('MAX')
    LREG = vbt.IndicatorFactory.from_talib('LINEARREG')

    bb = BB.run(
        close,
        timeperiod=bb_window,
        nbdevup=bb_multiplier,
        nbdevdn=bb_multiplier,
        matype=bb_matype,
    )
    atr = ATR.run(high, low, close, timeperiod=kc_atr_period)
    ema = EMA.run(close, timeperiod=kc_window)

    bb_upper_np = bb.upperband.to_numpy()
    bb_lower_np = bb.lowerband.to_numpy()
    ema_np = ema.real.to_numpy()
    atr_np = atr.real.to_numpy()
    kc_upper_np = ema_np + kc_multiplier * atr_np
    kc_lower_np = ema_np - kc_multiplier * atr_np

    hh = MAX.run(high, timeperiod=donichan_window).real.to_numpy()
    ll = MAX.run(low, timeperiod=donichan_window).real.to_numpy()
    sma = SMA.run(close, timeperiod=donichan_window).real.to_numpy()
    close_np = close.to_numpy()
    histogram = close_np - ((hh + ll) / 2 + sma) / 2
    ttms_np = LREG.run(
        pd.DataFrame(histogram, index=close.index, columns=close.columns),
        timeperiod=osc_smoothing_period,
    ).real.to_numpy()

    _, _, _, wvf_signal = williams_vix_fix_indicator(
        close,
        high,
        low,
        period=william_vix_period,
        mult=bb_multiplier,
        bbl=bb_window,
        lb=20,
        ph=0.85,
        ltLB=33,
        mtLB=10,
        strength_str=1,
    )
    wvf_np = wvf_signal if isinstance(wvf_signal, np.ndarray) else wvf_signal.to_numpy()

    if use_kama_slope:
        kama_np = kama_2d(close_np.astype(np.float64), kama_period, kama_fast, kama_slow)
        flat_np = slope_flat_2d(kama_np, kama_slope_win, flat_threshold_pct)
    else:
        flat_np = np.ones_like(close_np, dtype=np.bool_)

    if entry_version == 'v1':
        sqz_on_np = (bb_upper_np < kc_upper_np) & (bb_lower_np > kc_lower_np)
        sqz_off_np = (bb_upper_np > kc_upper_np) & (bb_lower_np < kc_lower_np)
        no_sqz_np = (~sqz_on_np) & (~sqz_off_np)
        entries_np = no_sqz_np & (ttms_np > 0) & flat_np

    elif entry_version == 'v2':
        sqz_on_np = (bb_upper_np < kc_upper_np) & (bb_lower_np > kc_lower_np)
        sqz_off_np = (bb_upper_np > kc_upper_np) & (bb_lower_np < kc_lower_np)
        no_sqz_np = (~sqz_on_np) & (~sqz_off_np)
        ttms_1 = shift_2d(ttms_np, 1)
        ttms_2 = shift_2d(ttms_np, 2)
        ttms_3 = shift_2d(ttms_np, 3)
        crossed_now = (ttms_1 < 0) & (ttms_np > 0)
        crossed_1ago = (ttms_2 < 0) & (ttms_1 > 0)
        crossed_2ago = (ttms_3 < 0) & (ttms_2 > 0)
        cond_2 = (ttms_np > ttms_1) & crossed_1ago
        cond_3 = (ttms_np > ttms_2) & crossed_2ago
        entries_np = no_sqz_np & (crossed_now | cond_2 | cond_3) & flat_np

    else:
        # squeeze_diff_np = bb_upper_np - kc_upper_np
        # consec_neg = count_consecutive_neg_2d(ttms_np)

        # entry_1 = (
        #     (shift_2d(squeeze_diff_np, 1) < 0)
        #     & (squeeze_diff_np > 0)
        #     & (ttms_np > 0)
        #     & flat_np
        # )
        # entry_2 = (
        #     (shift_2d(squeeze_diff_np, 1) < 0)
        #     & (squeeze_diff_np > 0)
        #     & (consec_neg > consecutive_neg_threshold)
        # )
        entries_np = wvf_np

    return pd.DataFrame(entries_np, index=close.index, columns=close.columns)


def compute_exits(
    close,
    high,
    low,
    atr_multiplier: float = 1.9,
    atr_period: int = 10,
    low_stop_lookback: int = 3,
):
    atr_raw = vbt.IndicatorFactory.from_talib('ATR').run(high, low, close, timeperiod=atr_period)
    ATRTrailing = vbt.IndicatorFactory(
        input_names=['close', 'atr'],
        param_names=['atr_multiplier'],
        output_names=['atr_trailing'],
    ).from_apply_func(atr_trailing_nb)
    atr_sl = ATRTrailing.run(close, atr_raw.real, atr_multiplier=atr_multiplier)
    exits_df = close.vbt.crossed_below(atr_sl.atr_trailing)

    MIN        = vbt.IndicatorFactory.from_talib('MIN')
    lowest_low = MIN.run(low, timeperiod=low_stop_lookback).real * 0.99  # 1% buffer below lowest low
    sl_stop_df = ((close - lowest_low) / close).clip(lower=0)

    return exits_df, sl_stop_df


_EXIT_PARAM_KEYS = frozenset({'atr_period', 'atr_multiplier', 'low_stop_lookback'})


class BreakoutTTM005C:
    """
    VectorBT OHLCV bundle (same layout as ``BreakoutTTMVersion2``) for the 005c TTM spec:
    KAMA-gated entries, Williams VIX Fix on v3, ATR trail + swing ``sl_stop`` exits.

    Prefer this over calling ``compute_signals`` / ``compute_exits`` directly unless you
    are composing custom portfolios (e.g. MS sizing in ``backtest_005c.ipynb``).
    """

    def __init__(
        self,
        data: pd.DataFrame,
        entry_version: str,
        *,
        use_kama_slope: bool = True,
        init_cash: float = 100.0,
        **param_overrides,
    ):
        if entry_version not in FIXED_TTM_PARAMS:
            raise ValueError(
                f'entry_version must be one of {sorted(FIXED_TTM_PARAMS)}, got {entry_version!r}'
            )
        self.data = data
        self.entry_version = entry_version
        self.use_kama_slope = use_kama_slope
        self.init_cash = init_cash
        self._params: dict = {**FIXED_TTM_PARAMS[entry_version], **param_overrides}

    @property
    def param_dict(self) -> dict:
        """All numeric/string params plus ``entry_version`` (e.g. for trade metadata)."""
        return {'entry_version': self.entry_version, **self._params}

    def get_entries(self) -> pd.DataFrame:
        sig_kw = {k: v for k, v in self._params.items() if k not in _EXIT_PARAM_KEYS}
        return compute_signals(
            self.data.close,
            self.data.high,
            self.data.low,
            entry_version=self.entry_version,
            use_kama_slope=self.use_kama_slope,
            **sig_kw,
        )

    def get_exits_and_stop(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        p = self._params
        return compute_exits(
            self.data.close,
            self.data.high,
            self.data.low,
            atr_multiplier=p['atr_multiplier'],
            atr_period=p['atr_period'],
            low_stop_lookback=p['low_stop_lookback'],
        )

    def get_portfolio(self, *, apply_exrem: bool = True, **portfolio_kwargs) -> vbt.Portfolio:
        """
        Build the VectorBT portfolio from raw entry/exit booleans.

        ``apply_exrem`` — If True, run AmiBroker-style **ExRem** on entries using the
        ATR trail exit mask: only the first entry bar after a flat period counts until
        an exit fires (same idea as :class:`BreakoutTTMVersion2` in ``breakout_ttm.py``).
        Exits are unchanged; ``sl_stop`` is unchanged.
        """
        entries = self.get_entries()
        exits, sl_stop_df = self.get_exits_and_stop()
        if apply_exrem:
            en = entries.fillna(False).to_numpy(dtype=np.bool_)
            ex = exits.fillna(False).to_numpy(dtype=np.bool_)
            entries = pd.DataFrame(
                exrem_func_nb(en, ex),
                index=entries.index,
                columns=entries.columns,
            )
        kw = {
            'close': self.data.close,
            'entries': entries,
            'exits': exits,
            'sl_stop': sl_stop_df,
            'freq': '1d',
            'group_by': ['symbol'],
            'cash_sharing': False,
            'init_cash': self.init_cash,
        }
        kw.update(portfolio_kwargs)
        return vbt.Portfolio.from_signals(**kw)
