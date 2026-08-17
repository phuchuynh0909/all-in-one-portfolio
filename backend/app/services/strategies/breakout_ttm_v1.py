"""
TTM Breakout v1/v2/v3/v4 with KAMA slope filter + exits matching `notebooks/backtest_005c.ipynb`.

`BreakoutTTMVersion2` in `breakout_ttm.py` uses an older signal/exit spec. Use
:class:`BreakoutTTMV1` for the same API shape (``data`` + ``get_portfolio()``) as Version2.

Module-level ``compute_signals`` and ``compute_exits`` remain available for custom wiring
(e.g. MS-sized portfolios); they are not re-exported from ``app.services.strategies``.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import numba as nb
import pandas as pd
import vectorbt as vbt

from app.services.indicators import (
    exrem_func_nb,
    atr_trailing_nb,
    williams_vix_fix_indicator,
    shift_2d,
    count_consecutive_neg_2d,
    kama_2d,
    slope_flat_2d,
)
from app.services.indicators.trailing_sl import (
    pearson_r_2d,
    atr_trailing_adaptive_nb,
)

_TTM_BEST_PARAMS_JSON = (
    Path(__file__).resolve().parents[3] / "assets" / "ttm_best_params_005_final.json"
)

# Optuna JSON omits KAMA / signal keys — merged under JSON values when present
_TTM_PARAM_FALLBACKS: dict[str, dict] = {
    "v1": {
        "consecutive_neg_threshold": 14,
        "william_vix_period": 14,
        "kama_period": 6,
        "kama_fast": 4,
        "kama_slow": 43,
        "kama_slope_win": 5,
        "flat_threshold_pct": 1.9,
    },
    "v2": {
        "consecutive_neg_threshold": 6,
        "william_vix_period": 27,
        "kama_period": 7,
        "kama_fast": 3,
        "kama_slow": 37,
        "kama_slope_win": 4,
        "flat_threshold_pct": 2.0,
    },
    "v3": {
        "kama_period": 13,
        "kama_fast": 5,
        "kama_slow": 37,
        "kama_slope_win": 16,
        "flat_threshold_pct": 2.8,
    },
    # v4 = the KAMA-centred v2 squeeze breakout ported from
    # ``backtest_strategies/breakout_ttm_kama.py`` (BreakoutTTMKamaStrategyBT, the
    # v2 entry). Both the Bollinger and Keltner bases are KAMA(close); the exit is
    # a Pearson-r adaptive ATR trail + a capped swing-low stop. Defaults are that
    # class's v2 OOS-selected values (ttm_best_params_011.json). Fallback-only —
    # not read from the 005 Optuna JSON.
    "v4": {
        # ── KAMA basis (shared BB + KC middle line) ──
        "kama_fast": 2,
        "kama_slow": 30,
        # ── KAMA-centred Bollinger / Keltner ──
        "bb_window": 21,
        "bb_multiplier": 2.0,
        "kc_window": 21,
        "kc_multiplier": 0.8,
        "kc_atr_period": 5,
        # ── TTM oscillator ──
        "donichan_window": 10,
        "osc_smoothing_period": 10,
        # ── Pearson-r adaptive ATR trailing exit ──
        "adaptive_atr": True,
        "atr_period": 17,
        "atr_mult_min": 1.9,
        "atr_mult_max": 3.7,
        "corr_window": 15,
        "mult_mode": "linear",
        "r_threshold": 0.6,
        "slope_k": 0.01,
        "r_smooth": 5,
        # ── Fixed swing-low stop (capped, no 1% buffer, no early-MAE exit) ──
        "low_stop_lookback": 4,
        "max_sl": 0.06,
        "low_stop_buffer": 1.0,
        "use_early_exit": False,
    },
}


def _load_fixed_ttm_params() -> dict[str, dict]:
    with _TTM_BEST_PARAMS_JSON.open() as f:
        from_json = json.load(f)

    # Union of JSON + fallback-only versions (e.g. v4 lives only in the fallbacks).
    versions = set(_TTM_PARAM_FALLBACKS) | set(from_json)
    return {
        ver: {**_TTM_PARAM_FALLBACKS.get(ver, {}), **from_json.get(ver, {})}
        for ver in versions
    }


FIXED_TTM_PARAMS = _load_fixed_ttm_params()


@nb.njit
def _early_exit_nb(close_2d, low_2d, entries_2d, window, pct):
    """
    For each symbol: after an entry, if the bar LOW drops > pct from entry close
    within the first `window` bars, emit an exit signal.
    Uses low (not close) so intraday wicks are captured as true MAE.
    """
    n_rows, n_cols = close_2d.shape
    out = np.zeros((n_rows, n_cols), dtype=np.bool_)
    for col in range(n_cols):
        entry_bar   = -1
        entry_close = np.nan
        for row in range(n_rows):
            if entries_2d[row, col]:
                entry_bar   = row
                entry_close = close_2d[row, col]
            if entry_bar >= 0:
                bars_since = row - entry_bar
                if 1 <= bars_since <= window:
                    mae = (entry_close - low_2d[row, col]) / entry_close
                    if mae > pct:
                        out[row, col] = True
                        entry_bar = -1   # stop watching after firing
    return out


def early_exit_signal(close, low, entries, window=5, pct=0.05):
    """Exit when low-based MAE exceeds ``pct`` within ``window`` bars after entry."""
    result = _early_exit_nb(
        close.to_numpy().astype(np.float64),
        low.to_numpy().astype(np.float64),
        entries.to_numpy().astype(np.bool_),
        window,
        pct,
    )
    return pd.DataFrame(result, index=close.index, columns=close.columns)


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
    MIN = vbt.IndicatorFactory.from_talib('MIN')
    LREG = vbt.IndicatorFactory.from_talib('LINEARREG')
    STDDEV = vbt.IndicatorFactory.from_talib('STDDEV')

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
    ll = MIN.run(low, timeperiod=donichan_window).real.to_numpy()
    sma = SMA.run(close, timeperiod=donichan_window).real.to_numpy()
    close_np = close.to_numpy()
    histogram = close_np - ((hh + ll) / 2 + sma) / 2
    ttms_np = LREG.run(
        pd.DataFrame(histogram, index=close.index, columns=close.columns),
        timeperiod=osc_smoothing_period,
    ).real.to_numpy()

    if entry_version == 'v1':
        sqz_on_np  = (bb_upper_np < kc_upper_np) & (bb_lower_np > kc_lower_np)
        sqz_off_np = (bb_upper_np > kc_upper_np) & (bb_lower_np < kc_lower_np)
        entries_np = sqz_off_np & (ttms_np > 0)

    elif entry_version == 'v2':
        sqz_on_np  = (bb_upper_np < kc_upper_np) & (bb_lower_np > kc_lower_np)
        sqz_off_np = (bb_upper_np > kc_upper_np) & (bb_lower_np < kc_lower_np)
        ttms_1 = shift_2d(ttms_np, 1)
        ttms_2 = shift_2d(ttms_np, 2)
        ttms_3 = shift_2d(ttms_np, 3)
        crossed_now  = (ttms_1 < 0) & (ttms_np > 0)
        crossed_1ago = (ttms_2 < 0) & (ttms_1 > 0)
        crossed_2ago = (ttms_3 < 0) & (ttms_2 > 0)
        cond_2 = (ttms_np > ttms_1) & crossed_1ago
        cond_3 = (ttms_np > ttms_2) & crossed_2ago
        entries_np = sqz_off_np & (crossed_now | cond_2 | cond_3)

    elif entry_version == 'v4':
        # KAMA-centred squeeze (ported from breakout_ttm_kama's v2): the basis of
        # BOTH the Bollinger and the Keltner band is KAMA(close) instead of the
        # SMA (BBANDS matype) / EMA used above. KAMA period is the band window,
        # sharing kama_fast / kama_slow. Entry is the same TTM zero-cross as v2.
        close_2d = close.to_numpy().astype(np.float64)

        bb_basis_np = kama_2d(close_2d, bb_window, kama_fast, kama_slow)
        bb_std_np = STDDEV.run(close, timeperiod=bb_window, nbdev=1.0).real.to_numpy()
        bb_upper_np = bb_basis_np + bb_multiplier * bb_std_np
        bb_lower_np = bb_basis_np - bb_multiplier * bb_std_np

        kc_atr_np = ATR.run(high, low, close, timeperiod=kc_atr_period).real.to_numpy()
        kc_basis_np = kama_2d(close_2d, kc_window, kama_fast, kama_slow)
        kc_upper_np = kc_basis_np + kc_multiplier * kc_atr_np
        kc_lower_np = kc_basis_np - kc_multiplier * kc_atr_np

        sqz_off_np = (bb_upper_np > kc_upper_np) & (bb_lower_np < kc_lower_np)
        ttms_1 = shift_2d(ttms_np, 1)
        ttms_2 = shift_2d(ttms_np, 2)
        ttms_3 = shift_2d(ttms_np, 3)
        crossed_now  = (ttms_1 < 0) & (ttms_np > 0)
        crossed_1ago = (ttms_2 < 0) & (ttms_1 > 0)
        crossed_2ago = (ttms_3 < 0) & (ttms_2 > 0)
        cond_2 = (ttms_np > ttms_1) & crossed_1ago
        cond_3 = (ttms_np > ttms_2) & crossed_2ago
        entries_np = sqz_off_np & (crossed_now | cond_2 | cond_3)

    else:
        _, _, _, wvf_signal = williams_vix_fix_indicator(
            close, high, low, period=william_vix_period,
            mult=bb_multiplier,  bbl=bb_window,
            lb=20, ph=0.85, ltLB=33, mtLB=10, strength_str=1,
        )
        wvf_np = wvf_signal if isinstance(wvf_signal, np.ndarray) else wvf_signal.to_numpy()

        squeeze_diff_np = bb_upper_np - kc_upper_np
        consec_neg = count_consecutive_neg_2d(ttms_np)
        entry_1 = (
            (shift_2d(squeeze_diff_np, 1) < 0)
            & (squeeze_diff_np > 0)
            & (consec_neg > consecutive_neg_threshold)
        )
        entries_np = wvf_np | entry_1

    return pd.DataFrame(entries_np, index=close.index, columns=close.columns)


_SIGNAL_PARAM_KEYS = frozenset(inspect.signature(compute_signals).parameters) - {
    'close', 'high', 'low',
}


def adaptive_atr_trail_2d(
    close,
    high,
    low,
    atr_period: int,
    corr_window: int,
    atr_mult_min: float,
    atr_mult_max: float,
    mult_mode: str = 'linear',
    r_threshold: float = 0.6,
    slope_k: float = 0.01,
    r_smooth: int = 1,
) -> pd.DataFrame:
    """
    Pearson-r adaptive ATR trailing stop (2-D / vectorbt). Column-wise port of
    ``adaptive_atr_trail`` in ``backtest_strategies/breakout_ttm_kama.py``: a
    rolling price-vs-time correlation (``corr_window``) gauges trend linearity and
    scales the ATR multiplier between ``atr_mult_min`` and ``atr_mult_max`` — wide
    stop in clean trends, tight stop in chop.

    ``mult_mode`` maps trend strength to the multiplier:
      'linear'          : scale = clip(r, 0, 1)
      'threshold'       : scale ramps only once r > r_threshold
      'slope'           : clip(r,0,1) * tanh(slope% / slope_k)
      'slope_threshold' : threshold(r) * slope strength
    ``r_smooth`` (EMA span, 1 = off) damps bar-to-bar jitter in r.
    """
    close_2d = close.to_numpy().astype(np.float64)
    atr_np = vbt.IndicatorFactory.from_talib('ATR').run(
        high, low, close, timeperiod=atr_period,
    ).real.to_numpy()

    r = pearson_r_2d(close_2d, corr_window)
    if r_smooth and r_smooth > 1:
        r = pd.DataFrame(r).ewm(span=r_smooth, adjust=False).mean().to_numpy()
    lin = np.clip(np.nan_to_num(r, nan=0.0), 0.0, 1.0)   # trend linearity (long-only)

    if mult_mode == 'linear':
        scale = lin
    elif mult_mode == 'threshold':
        scale = np.clip((lin - r_threshold) / max(1e-9, 1.0 - r_threshold), 0.0, 1.0)
    elif mult_mode in ('slope', 'slope_threshold'):
        slope_np = vbt.IndicatorFactory.from_talib('LINEARREG_SLOPE').run(
            close, timeperiod=corr_window,
        ).real.to_numpy()
        slope_pct = np.nan_to_num(slope_np / close_2d, nan=0.0)          # trend % per bar
        slope_scl = np.clip(np.tanh(slope_pct / slope_k), 0.0, 1.0)
        if mult_mode == 'slope':
            scale = lin * slope_scl
        else:  # slope_threshold
            thr   = np.clip((lin - r_threshold) / max(1e-9, 1.0 - r_threshold), 0.0, 1.0)
            scale = thr * slope_scl
    else:
        raise ValueError(f'unknown mult_mode {mult_mode!r}')

    mult = atr_mult_min + (atr_mult_max - atr_mult_min) * scale
    trail = atr_trailing_adaptive_nb(close_2d, atr_np, mult)
    return pd.DataFrame(trail, index=close.index, columns=close.columns)


def compute_exits(
    close,
    high,
    low,
    atr_multiplier: float = 1.9,
    atr_period: int = 10,
    low_stop_lookback: int = 3,
    entries: pd.DataFrame | None = None,
    early_exit_window: int = 5,
    early_exit_pct: float = 0.05,
    use_early_exit: bool = True,
    low_stop_buffer: float = 0.99,
    max_sl: float | None = None,
    adaptive_atr: bool = False,
    atr_mult_min: float = 1.9,
    atr_mult_max: float = 3.7,
    corr_window: int = 15,
    mult_mode: str = 'linear',
    r_threshold: float = 0.6,
    slope_k: float = 0.01,
    r_smooth: int = 1,
):
    """
    Returns (exits_df, sl_stop_df).

    exits_df: ATR trailing-stop crossings, optionally OR-ed with early MAE exits
              when ``entries`` is provided and ``use_early_exit`` (low drops >
              early_exit_pct from entry close within early_exit_window bars).
              With ``adaptive_atr`` the trail uses the Pearson-r adaptive
              multiplier (v4 / breakout_ttm_kama spec) instead of a fixed one.
    sl_stop_df: per-bar relative stop from the rolling minimum low
                (``low_stop_buffer`` scales the level; ``max_sl`` caps the stop).
    """
    if adaptive_atr:
        atr_sl = adaptive_atr_trail_2d(
            close, high, low,
            atr_period=atr_period, corr_window=corr_window,
            atr_mult_min=atr_mult_min, atr_mult_max=atr_mult_max,
            mult_mode=mult_mode, r_threshold=r_threshold,
            slope_k=slope_k, r_smooth=r_smooth,
        )
        exits_df = close.vbt.crossed_below(atr_sl)
    else:
        atr_raw = vbt.IndicatorFactory.from_talib('ATR').run(high, low, close, timeperiod=atr_period)
        ATRTrailing = vbt.IndicatorFactory(
            input_names=['close', 'atr'],
            param_names=['atr_multiplier'],
            output_names=['atr_trailing'],
        ).from_apply_func(atr_trailing_nb)
        atr_sl = ATRTrailing.run(close, atr_raw.real, atr_multiplier=atr_multiplier)
        exits_df = close.vbt.crossed_below(atr_sl.atr_trailing)

    if entries is not None and use_early_exit:
        exits_df = exits_df | early_exit_signal(
            close, low, entries,
            window=early_exit_window,
            pct=early_exit_pct,
        )

    MIN = vbt.IndicatorFactory.from_talib('MIN')
    lowest_low = MIN.run(low, timeperiod=low_stop_lookback).real * low_stop_buffer
    sl_stop_df = ((close - lowest_low) / close).clip(lower=0)
    if max_sl is not None:
        sl_stop_df = sl_stop_df.clip(upper=max_sl)

    return exits_df, sl_stop_df


_EXIT_PARAM_KEYS = frozenset(inspect.signature(compute_exits).parameters) - {
    'close', 'high', 'low', 'entries',
}


class BreakoutTTMV1:
    """
    VectorBT OHLCV bundle (same layout as ``BreakoutTTMVersion2``) for the 005c TTM spec:
    KAMA-gated entries, Williams VIX Fix on v3, ATR trail + swing ``sl_stop`` exits.

    ``entry_version='v4'`` bundles the KAMA-centred v2 squeeze breakout from
    ``backtest_strategies/breakout_ttm_kama.py`` (BreakoutTTMKamaStrategyBT): both
    band bases are KAMA(close) and the exit is a Pearson-r adaptive ATR trail plus
    a capped swing-low ``sl_stop`` (no 1% buffer, no early-MAE exit). The bar-by-bar
    engine's volume filter, KAMA profit-take scale-out, and swing-range position
    sizing are omitted — they are not expressible as from_signals boolean masks.

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
        sig_kw = {k: v for k, v in self._params.items() if k in _SIGNAL_PARAM_KEYS}
        return compute_signals(
            self.data.close,
            self.data.high,
            self.data.low,
            entry_version=self.entry_version,
            use_kama_slope=self.use_kama_slope,
            **sig_kw,
        )

    def get_exits_and_stop(
        self,
        entries: pd.DataFrame | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        exit_kw = {k: v for k, v in self._params.items() if k in _EXIT_PARAM_KEYS}
        return compute_exits(
            self.data.close,
            self.data.high,
            self.data.low,
            entries=entries,
            **exit_kw,
        )

    def get_portfolio(self, *, apply_exrem: bool = True, **portfolio_kwargs) -> vbt.Portfolio:
        """
        Build the VectorBT portfolio from raw entry/exit booleans.

        ``apply_exrem`` — If True, run AmiBroker-style **ExRem** on entries using the
        ATR trail exit mask: only the first entry bar after a flat period counts until
        an exit fires (same idea as :class:`BreakoutTTMVersion2` in ``breakout_ttm.py``).
        Early MAE exits are computed after ExRem so they align with traded entries.

        Entries and ordinary exits fill at the **signal bar close** (``price=close``),
        not at the next bar's open.
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
        exits, sl_stop_df = self.get_exits_and_stop(entries)
        kw = {
            'close': self.data.close,
            'entries': entries,
            'exits': exits,
            'sl_stop': sl_stop_df,
            'freq': '1d',
            'group_by': ['symbol'],
            'cash_sharing': False,
            'init_cash': self.init_cash,
            # Signal-bar close fill (not next-bar open; vbt price=-np.inf uses open).
            'price': self.data.close,
        }
        kw.update(portfolio_kwargs)
        return vbt.Portfolio.from_signals(**kw)
