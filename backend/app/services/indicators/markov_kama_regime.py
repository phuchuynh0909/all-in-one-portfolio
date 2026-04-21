from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tools.sm_exceptions import ConvergenceWarning, ValueWarning


REGIME_LABEL_TO_CODE = {
    'Bearish_High_Var': -2,
    'Bearish_Low_Var': -1,
    'Neutral': 0,
    'Bullish_Low_Var': 1,
    'Bullish_High_Var': 2,
}


def compute_kama(
    series: pd.Series,
    window: int = 10,
    fast: int = 2,
    slow: int = 30,
) -> pd.Series:
    series = pd.Series(series, dtype=float)
    change = series.diff(window).abs()
    volatility = series.diff().abs().rolling(window).sum()
    efficiency_ratio = (change / volatility.replace(0, np.nan)).clip(lower=0, upper=1)

    fast_sc = 2.0 / (fast + 1.0)
    slow_sc = 2.0 / (slow + 1.0)
    smoothing_constant = (efficiency_ratio * (fast_sc - slow_sc) + slow_sc) ** 2

    kama = pd.Series(np.nan, index=series.index, dtype=float)
    if len(series) <= window:
        return kama

    seed = series.iloc[: window + 1]
    if seed.notna().any():
        kama.iloc[window] = seed.mean()

    for i in range(window + 1, len(series)):
        alpha = smoothing_constant.iloc[i]
        prev = kama.iloc[i - 1]
        if np.isnan(prev):
            prev = series.iloc[i - 1]
        if np.isnan(prev):
            continue
        kama.iloc[i] = prev if np.isnan(alpha) else prev + alpha * (series.iloc[i] - prev)

    return kama


def build_trend_overlay(
    log_close: pd.Series,
    kama_window: int = 10,
    kama_fast: int = 2,
    kama_slow: int = 30,
    gamma: float = 1.0,
    filter_window: int | None = None,
) -> pd.DataFrame:
    filter_window = kama_window if filter_window is None else filter_window

    kama = compute_kama(log_close, window=kama_window, fast=kama_fast, slow=kama_slow)
    period_low = kama.rolling(kama_window, min_periods=kama_window).min().shift(1)
    period_high = kama.rolling(kama_window, min_periods=kama_window).max().shift(1)
    filter_band = gamma * kama.diff(kama_window).rolling(filter_window, min_periods=filter_window).std()

    trend = pd.Series(0, index=log_close.index, dtype='int64')
    trend[kama > period_low + filter_band] = 1
    trend[kama < period_high - filter_band] = -1

    return pd.DataFrame({
        'kama': kama,
        'period_low': period_low,
        'period_high': period_high,
        'filter_band': filter_band,
        'trend': trend,
    })


def prepare_markov_inputs(log_returns: pd.Series, lag_order: int = 1) -> tuple[pd.Series, pd.DataFrame]:
    y = pd.Series(log_returns, dtype=float).dropna().rename('ret')
    exog = pd.concat(
        {f'lag_{lag}': y.shift(lag) for lag in range(1, lag_order + 1)},
        axis=1,
    ) if lag_order > 0 else pd.DataFrame(index=y.index)
    data = pd.concat([y, exog], axis=1).dropna()
    return data['ret'], data.drop(columns='ret')


def _probabilities_to_frame(probs, index: pd.Index) -> pd.DataFrame:
    if isinstance(probs, pd.DataFrame):
        return probs
    return pd.DataFrame(probs, index=index)


def label_states_by_variance(returns: pd.Series, probs: pd.DataFrame) -> dict:
    probs = _probabilities_to_frame(probs, returns.index)
    aligned_returns = returns.loc[probs.index]
    state_var: dict = {}

    for state in probs.columns:
        weights = probs[state].clip(lower=1e-12)
        mean_ret = np.average(aligned_returns, weights=weights)
        state_var[state] = np.average((aligned_returns - mean_ret) ** 2, weights=weights)

    ordered = sorted(state_var, key=state_var.get)
    return {'low': ordered[0], 'high': ordered[-1], 'state_var': state_var}


def fit_markov_volatility(log_returns: pd.Series, lag_order: int = 1) -> dict:
    endog, exog = prepare_markov_inputs(log_returns, lag_order=lag_order)
    if len(endog) < max(30, lag_order + 5):
        raise ValueError('Not enough data to fit Markov volatility model')

    model_kwargs = {
        'endog': endog,
        'k_regimes': 2,
        'trend': 'n',
        'switching_variance': True,
    }
    if not exog.empty:
        model_kwargs['exog'] = exog

    with warnings.catch_warnings():
        warnings.simplefilter('ignore', ConvergenceWarning)
        warnings.simplefilter('ignore', RuntimeWarning)
        warnings.simplefilter('ignore', ValueWarning)
        model = sm.tsa.MarkovRegression(**model_kwargs)
        result = model.fit(disp=False)

    smoothed_probs = _probabilities_to_frame(result.smoothed_marginal_probabilities, endog.index)
    filtered_probs = _probabilities_to_frame(result.filtered_marginal_probabilities, endog.index)
    mapping = label_states_by_variance(endog, smoothed_probs)

    return {
        'result': result,
        'mapping': mapping,
        'smoothed_probs': pd.DataFrame({
            'low_var_prob': smoothed_probs[mapping['low']],
            'high_var_prob': smoothed_probs[mapping['high']],
        }),
        'filtered_probs': pd.DataFrame({
            'low_var_prob': filtered_probs[mapping['low']],
            'high_var_prob': filtered_probs[mapping['high']],
        }),
    }


def combine_regimes(
    index_like,
    vol_probs: pd.DataFrame,
    trend_data: pd.DataFrame,
    prob_threshold: float = 0.55,
) -> pd.DataFrame:
    out = pd.DataFrame(index=index_like)
    out = out.join(vol_probs[['low_var_prob', 'high_var_prob']], how='left')
    out = out.join(trend_data[['kama', 'trend']], how='left')

    out['label'] = 'Neutral'
    low_var = out['low_var_prob'] >= prob_threshold
    high_var = out['high_var_prob'] >= prob_threshold
    bullish = out['trend'] > 0
    bearish = out['trend'] < 0

    out.loc[low_var & bullish, 'label'] = 'Bullish_Low_Var'
    out.loc[low_var & bearish, 'label'] = 'Bearish_Low_Var'
    out.loc[high_var & bullish, 'label'] = 'Bullish_High_Var'
    out.loc[high_var & bearish, 'label'] = 'Bearish_High_Var'
    out['regime_code'] = out['label'].map(REGIME_LABEL_TO_CODE).fillna(0).astype(np.int32)

    return out


def markov_kama_regime_table(
    close: np.ndarray,
    *,
    index: pd.Index | None = None,
    kama_window: int = 10,
    kama_fast: int = 2,
    kama_slow: int = 30,
    gamma: float = 1.0,
    filter_window: int | None = None,
    lag_order: int = 1,
    prob_threshold: float = 0.55,
    use_filtered_probs: bool = True,
) -> pd.DataFrame:
    close_array = np.asarray(close, dtype=np.float64)
    out_index = pd.RangeIndex(len(close_array)) if index is None else index

    out = pd.DataFrame(index=out_index)
    out['kama'] = np.nan
    out['trend'] = 0
    out['low_var_prob'] = 0.0
    out['high_var_prob'] = 0.0
    out['label'] = 'Neutral'
    out['regime_code'] = 0

    if len(close_array) == 0:
        return out

    close_series = pd.Series(close_array, index=out_index, dtype=float)
    valid_close = close_series.where(close_series > 0)
    log_close = np.log(valid_close)

    trend_data = build_trend_overlay(
        log_close,
        kama_window=kama_window,
        kama_fast=kama_fast,
        kama_slow=kama_slow,
        gamma=gamma,
        filter_window=filter_window,
    )
    out[['kama', 'trend']] = trend_data[['kama', 'trend']]

    try:
        markov_fit = fit_markov_volatility(log_close.diff(), lag_order=lag_order)
        vol_probs = markov_fit['filtered_probs'] if use_filtered_probs else markov_fit['smoothed_probs']
        out = combine_regimes(out_index, vol_probs, trend_data, prob_threshold=prob_threshold)
    except Exception:
        out['regime_code'] = out['label'].map(REGIME_LABEL_TO_CODE).fillna(0).astype(np.int32)

    out['trend'] = out['trend'].fillna(0).astype(np.int32)
    out['low_var_prob'] = out['low_var_prob'].fillna(0.0).astype(float)
    out['high_var_prob'] = out['high_var_prob'].fillna(0.0).astype(float)
    return out


def markov_kama_regime(
    close: np.ndarray,
    *,
    index: pd.Index | None = None,
    kama_window: int = 10,
    kama_fast: int = 2,
    kama_slow: int = 30,
    gamma: float = 1.0,
    filter_window: int | None = None,
    lag_order: int = 1,
    prob_threshold: float = 0.55,
    use_filtered_probs: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    regime_table = markov_kama_regime_table(
        close,
        index=index,
        kama_window=kama_window,
        kama_fast=kama_fast,
        kama_slow=kama_slow,
        gamma=gamma,
        filter_window=filter_window,
        lag_order=lag_order,
        prob_threshold=prob_threshold,
        use_filtered_probs=use_filtered_probs,
    )

    regime = regime_table['regime_code'].to_numpy(dtype=np.int32)
    low_var_prob = regime_table['low_var_prob'].to_numpy(dtype=np.float64)
    high_var_prob = regime_table['high_var_prob'].to_numpy(dtype=np.float64)
    trend = regime_table['trend'].to_numpy(dtype=np.float64)
    kama = np.exp(regime_table['kama'].to_numpy(dtype=np.float64))

    return regime, low_var_prob, high_var_prob, trend, kama
