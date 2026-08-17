from __future__ import annotations

import gc
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from prefect import flow, task


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_OUTPUT_DIR = PROJECT_ROOT / "notebooks"
MODELS_DIR = PROJECT_ROOT / "models"


@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _get_env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _configure_runtime() -> None:
    cache_root = Path(os.getenv("META_LABEL_TASK_CACHE_DIR", "/tmp/meta-label-prefect-cache"))
    numba_cache_dir = cache_root / "numba"
    numba_cache_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("XDG_CACHE_HOME", str(cache_root))
    os.environ.setdefault("NUMBA_CACHE_DIR", str(numba_cache_dir))

    backend_path = PROJECT_ROOT / "backend"
    for path in (str(PROJECT_ROOT), str(backend_path)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _artifact_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Expected artifact was not created: {path}")
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "modified_at": path.stat().st_mtime,
    }


def _serialize_model_metrics(model_metrics: dict[str, dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    serialized: dict[str, dict[str, float | int]] = {}
    for model_name, metrics in model_metrics.items():
        serialized[model_name] = {}
        for metric_name, value in metrics.items():
            if isinstance(value, (int, float)):
                serialized[model_name][metric_name] = value
            else:
                serialized[model_name][metric_name] = float(value)
    return serialized


def _format_metric_comparison(
    model_metrics: dict[str, dict[str, Any]],
    best_ensemble_name: str | None,
) -> str:
    rows = sorted(
        model_metrics.items(),
        key=lambda item: item[1].get("auc", float("-inf")),
        reverse=True,
    )
    lines = [
        "Final model comparison",
        "model                          auc     f1   precision  recall  brier  filt_sharpe  lift",
        "----------------------------  -----  -----  ---------  ------  -----  -----------  -----",
    ]
    for model_name, metrics in rows:
        marker = "*" if model_name == best_ensemble_name else " "
        lines.append(
            f"{marker}{model_name[:27]:<27}  "
            f"{metrics.get('auc', 0):>5.3f}  "
            f"{metrics.get('f1', 0):>5.3f}  "
            f"{metrics.get('precision', 0):>9.3f}  "
            f"{metrics.get('recall', 0):>6.3f}  "
            f"{metrics.get('brier_score', 0):>5.3f}  "
            f"{metrics.get('filt_sharpe', 0):>11.3f}  "
            f"{metrics.get('sharpe_lift', 0):>5.3f}"
        )
    if best_ensemble_name:
        lines.append(f"Best ensemble: {best_ensemble_name}")
    return "\n".join(lines)


def _collect_garbage() -> None:
    gc.collect()


def _load_watchlist_symbols(watchlist_path: Path | None = None) -> list[str]:
    import pandas as pd

    path = watchlist_path or (PROJECT_ROOT / "backend/models/watchlist.csv")
    watchlist_df = pd.read_csv(path)
    return watchlist_df.iloc[:, 0].astype(str).tolist()


def load_ohlc_panel(
    output_dir: str | Path = PIPELINE_OUTPUT_DIR,
    refresh_cache: bool = False,
    cache_filename: str = "stocks_data_latest.h5",
    years: int = 10,
    watchlist_path: str | Path | None = None,
) -> Any:
    """Load the OHLC panel from local HDF cache or Delta Lake.

    Returns a wide DataFrame with OHLC fields on level 0 and symbols on level 1.
    """
    import os
    import pandas as pd
    from deltalake import DeltaTable
    from dotenv import load_dotenv

    load_dotenv()

    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    cache_path = output_path / cache_filename
    store_key = "stocks"

    if cache_path.exists() and not refresh_cache:
        with pd.HDFStore(cache_path, mode="r") as store:
            return store[store_key]

    minio_endpoint = os.getenv("MINIO_ENDPOINT", "192.168.1.3:9000")
    aws_access_key_id = os.getenv("MINIO_ACCESS_KEY", os.getenv("AWS_ACCESS_KEY_ID", "CzOwnLkEDXQy951AOqes"))
    aws_secret_access_key = os.getenv("MINIO_SECRET_KEY", os.getenv("AWS_SECRET_ACCESS_KEY", "fdRe91TOtqTl0icUkZLsUnWvZa90aZ5qG5rVEf7S"))
    remote_host = os.getenv("META_LABEL_REMOTE_HOST", f"http://{minio_endpoint}")
    storage_options = {
        "AWS_ACCESS_KEY_ID": aws_access_key_id,
        "AWS_SECRET_ACCESS_KEY": aws_secret_access_key,
        "AWS_ENDPOINT_URL": remote_host,
        "AWS_ALLOW_HTTP": "true",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_REGION": "us-east-1",
        "aws_conditional_put": "etag",
    }

    watchlist_symbols = _load_watchlist_symbols(Path(watchlist_path) if watchlist_path else None)
    start_date = pd.Timestamp.now() - pd.DateOffset(years=years)
    dt = DeltaTable("s3://delta-table-storage/stocks", storage_options=storage_options)
    raw_df = dt.to_pandas(
        filters=[("date", ">=", start_date), ("symbol", "in", watchlist_symbols)],
        columns=["symbol", "date", "close", "open", "high", "low", "volume"],
    )
    raw_df = raw_df.drop_duplicates(subset=["date", "symbol"], keep="last")
    panel = raw_df.set_index(["date", "symbol"]).unstack(level=1).bfill().ffill()

    with pd.HDFStore(cache_path, mode="w") as store:
        store.put(store_key, panel)
    return panel


def _build_feature_context_from_ohlc_panel(
    df: Any,
    watchlist_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Build the full feature frame and return internals needed by training-label generation."""
    import numpy as np
    import pandas as pd
    import vectorbt as vbt

    if watchlist_symbols is None:
        watchlist_symbols = _load_watchlist_symbols()
    stocks = df.loc[:, df.columns.get_level_values('symbol').isin(watchlist_symbols)]
    # Exclude VNINDEX from tradable universe (used as benchmark only)
    symbols_in_columns = stocks.columns.get_level_values(1)
    columns_to_keep_mask = symbols_in_columns != 'VNINDEX'
    stocks_exclude_vnindex = stocks.loc[:, columns_to_keep_mask]

    # HDF5 may have been saved before VNINDEX (or index rows) existed in the panel
    _panel_syms = df.columns.get_level_values('symbol').unique()
    _BENCH_PX_SYM = next((s for s in ('VNINDEX', 'VN30') if s in _panel_syms), None)
    import numba as nb
    from app.services.indicators import (
        avwap_func_nb,
        yang_zhang_volatility_nb,
        zscore_nb,
        relative_strength_nb,
        squeeze_ttm,
    )

    # ── Raw arrays ──────────────────────────────────────────────────────────────
    close_df  = stocks_exclude_vnindex.close
    open_df   = stocks_exclude_vnindex.open
    high_df   = stocks_exclude_vnindex.high
    low_df    = stocks_exclude_vnindex.low
    volume_df = stocks_exclude_vnindex.volume

    index   = close_df.index
    symbols = close_df.columns

    close_2d  = close_df.to_numpy().astype(np.float64)
    open_2d   = open_df.to_numpy().astype(np.float64)
    high_2d   = high_df.to_numpy().astype(np.float64)
    low_2d    = low_df.to_numpy().astype(np.float64)
    volume_2d = volume_df.to_numpy().astype(np.float64)

    EPS = 1e-10

    def _df(arr):
        """Wrap 2-D array into DataFrame with stock index/columns."""
        return pd.DataFrame(arr, index=index, columns=symbols)

    def _safe_log_ratio_df(num_df, denom_df):
        """log1p((num - denom) / denom), NaN-safe."""
        safe = denom_df.where(denom_df != 0, np.nan)
        return np.log1p(((num_df - safe) / safe).clip(lower=-1 + EPS))

    def _safe_log_ratio_2d(num, denom):
        safe = np.where(denom == 0, np.nan, denom)
        ratio = (num - safe) / safe
        return np.log1p(np.clip(ratio, -1 + EPS, None))


    # ── 1. RSI (5, 14) ──────────────────────────────────────────────────────────
    RSI = vbt.IndicatorFactory.from_talib('RSI')
    rsi_5_df  = RSI.run(close_df, timeperiod=5).real
    rsi_14_df = RSI.run(close_df, timeperiod=14).real

    # ── 2. MFI (21) ─────────────────────────────────────────────────────────────
    mfi_21_df = vbt.IndicatorFactory.from_talib('MFI').run(
        high_df, low_df, close_df, volume_df, timeperiod=21).real

    # ── 3. OBV ──────────────────────────────────────────────────────────────────
    obv_df = vbt.IndicatorFactory.from_talib('OBV').run(close_df, volume_df).real

    # ── 4. Log return ────────────────────────────────────────────────────────────
    log_return_df = np.log(close_df / close_df.shift(1))

    # ── 5. EMA distances (10, 20, 50, 200) ──────────────────────────────────────
    EMA = vbt.IndicatorFactory.from_talib('EMA')
    ema_10_df  = EMA.run(close_df, timeperiod=10).real
    ema_20_df  = EMA.run(close_df, timeperiod=20).real
    ema_50_df  = EMA.run(close_df, timeperiod=50).real
    ema_200_df = EMA.run(close_df, timeperiod=200).real

    ema_10_dist  = _safe_log_ratio_df(close_df, ema_10_df)
    ema_20_dist  = _safe_log_ratio_df(close_df, ema_20_df)
    ema_50_dist  = _safe_log_ratio_df(close_df, ema_50_df)
    ema_200_dist = _safe_log_ratio_df(close_df, ema_200_df)

    # ── 6. Volume MA ratio (10, 20) ──────────────────────────────────────────────
    SMA = vbt.IndicatorFactory.from_talib('SMA')
    vol_ma_10_dist = _safe_log_ratio_df(volume_df, SMA.run(volume_df, timeperiod=10).real)
    vol_ma_20_dist = _safe_log_ratio_df(volume_df, SMA.run(volume_df, timeperiod=20).real)

    # ── 7. EFI z-score (Elder Force Index = close.diff * volume) ────────────────
    efi_2d = np.nan_to_num((close_df.diff() * volume_df).to_numpy().astype(np.float64))
    efi_zscore_10_2d = zscore_nb(efi_2d, window=10)
    efi_zscore_20_2d = zscore_nb(efi_2d, window=20)

    # ── 8. AVWAP distances (anchored to rolling highest / lowest) ────────────────
    avwap_hi_2d = avwap_func_nb(close_2d, high_2d, low_2d, volume_2d, is_highest=True,  window=200)
    avwap_lo_2d = avwap_func_nb(close_2d, high_2d, low_2d, volume_2d, is_highest=False, window=200)
    vwap_dist_hi_2d = _safe_log_ratio_2d(close_2d, avwap_hi_2d)
    vwap_dist_lo_2d = _safe_log_ratio_2d(close_2d, avwap_lo_2d)

    # ── 9. Relative strength vs benchmark (VNINDEX / VN30 / EW mean close) ─────
    if _BENCH_PX_SYM is not None:
        _bench_close_1d = stocks.close[_BENCH_PX_SYM].values.astype(np.float64)
    else:
        _bench_close_1d = np.nanmean(close_2d, axis=1)
    vnindex_2d = np.tile(_bench_close_1d.reshape(-1, 1), (1, close_2d.shape[1]))
    rs_10_2d, mrs_10_2d = relative_strength_nb(close_2d, vnindex_2d, window=10)
    rs_20_2d, mrs_20_2d = relative_strength_nb(close_2d, vnindex_2d, window=20)

    # ── 10. Cross-sectional MSR rank ─────────────────────────────────────────────
    msr_rank_10_2d = _df(mrs_10_2d).rank(axis=1, pct=True).to_numpy()
    msr_rank_20_2d = _df(mrs_20_2d).rank(axis=1, pct=True).to_numpy()

    # ── 11. Z-score of log return (10, 20) ───────────────────────────────────────
    lr_2d = np.nan_to_num(log_return_df.to_numpy().astype(np.float64))
    zscore_lr_10_2d = zscore_nb(lr_2d, window=10)
    zscore_lr_20_2d = zscore_nb(lr_2d, window=20)

    # ── 12. Yang-Zhang volatility (10, 20) ───────────────────────────────────────
    # signature: yang_zhang_volatility_nb(close, open, high, low, window, periods)
    yz_vol_10_2d = yang_zhang_volatility_nb(close_2d, open_2d, high_2d, low_2d, window=10, periods=252).astype(np.float64)
    yz_vol_20_2d = yang_zhang_volatility_nb(close_2d, open_2d, high_2d, low_2d, window=20, periods=252).astype(np.float64)

    # ── 13. DC TMV — TTM Squeeze momentum value ──────────────────────────────────
    # squeeze_ttm uses vbt.IndicatorFactory.from_talib internally → handles 2-D DataFrames
    _diff, dc_tmv_raw, _ = squeeze_ttm(
        close_df.to_numpy(), high_df.to_numpy(), low_df.to_numpy(),
        bb_period=10, bb_mult=1.2, bb_matype=3,
        kc_period=10, kc_mult=1.2,
        donichan_period=10, osc_smoothing_period=10,
    )
    # squeeze_ttm receives ndarray → vbt wraps columns per param combo → result may be 3-D
    # If 2-D pass-through, take as-is; if 1 param-combo dim, squeeze it out
    if hasattr(dc_tmv_raw, 'to_numpy'):
        dc_tmv_2d = dc_tmv_raw.to_numpy()
    else:
        dc_tmv_2d = np.asarray(dc_tmv_raw)
    if dc_tmv_2d.ndim == 3:
        dc_tmv_2d = dc_tmv_2d[:, :, 0]  # drop single param-combo axis

    # ── 14. Kalman-filter smoothed price → distance + z-scores ───────────────────
    @nb.njit(parallel=True)
    def _kalman_2d(prices, obs_cov=1.0, trans_cov=0.01):
        """Scalar Kalman filter applied independently to each column."""
        T, S = prices.shape
        out = np.empty_like(prices)
        for j in nb.prange(S):
            x = prices[0, j]
            p = 1.0
            for i in range(T):
                p_pred = p + trans_cov
                gain   = p_pred / (p_pred + obs_cov)
                x      = x + gain * (prices[i, j] - x)
                p      = (1.0 - gain) * p_pred
                out[i, j] = x
        return out

    kf_2d          = _kalman_2d(close_2d)
    kf_dist_2d     = _safe_log_ratio_2d(close_2d, kf_2d)
    zscore_kf_10_2d = zscore_nb(kf_2d, window=10)
    zscore_kf_20_2d = zscore_nb(kf_2d, window=20)


    # ── Assemble features_df (date × symbol long form) ──────────────────────────
    _feat_arrays = {
        'rsi_window_5':           rsi_5_df.to_numpy(),
        'rsi_window_14':          rsi_14_df.to_numpy(),
        'mfi_21':                 mfi_21_df.to_numpy(),
        'obv':                    obv_df.to_numpy(),
        'log_return':             log_return_df.to_numpy(),
        'volume_threshold_ma_10': vol_ma_10_dist.to_numpy(),
        'volume_threshold_ma_20': vol_ma_20_dist.to_numpy(),
        'ema_10_distance':        ema_10_dist.to_numpy(),
        'ema_20_distance':        ema_20_dist.to_numpy(),
        'ema_50_distance':        ema_50_dist.to_numpy(),
        'ema_200_distance':       ema_200_dist.to_numpy(),
        'vwap_distance_highest':  vwap_dist_hi_2d,
        'vwap_distance_lowest':   vwap_dist_lo_2d,
        'efi_zscore_10':          efi_zscore_10_2d,
        'efi_zscore_20':          efi_zscore_20_2d,
        'rs_10':                  rs_10_2d,
        'rs_20':                  rs_20_2d,
        'mrs_10':                 mrs_10_2d,
        'mrs_20':                 mrs_20_2d,
        'msr_rank_10':            msr_rank_10_2d,
        'msr_rank_20':            msr_rank_20_2d,
        'zscore_10_log_return':   zscore_lr_10_2d,
        'zscore_20_log_return':   zscore_lr_20_2d,
        'yz_vol_10':              yz_vol_10_2d,
        'yz_vol_20':              yz_vol_20_2d,
        'dc_tmv':                 dc_tmv_2d,
        'kf_distance':            kf_dist_2d,
        'zscore_kf_10':           zscore_kf_10_2d,
        'zscore_kf_20':           zscore_kf_20_2d,
        # keep close for downstream kf_distance sanity checks (excluded from SAFE_FEATURE_COLS)
        'close':                  close_2d,
    }

    features_df = pd.concat(
        [_df(arr).stack().rename(feat) for feat, arr in _feat_arrays.items()],
        axis=1,
    )
    features_df.index.names = ['date', 'symbol']
    # ── Autopsy-Driven Feature Additions ────────────────────────────────────────
    # Source: loser_autopsy_20260501_1302.json
    #
    # Catastrophic loss drivers (Cohen's d in parentheses):
    #   yz_vol_10/20 (0.74) → vol z-score & acceleration
    #   mrs_10 (0.49), msr_rank_10 (0.39) → overextension ratio
    #   ema_10_distance (0.28) → near/far EMA ratio
    #   vwap_distance_highest (0.27) → already in model
    #   log_return on entry day (0.24) → breakout-day return
    # General discriminators (all |d|<0.2 but statistically significant):
    #   msr_rank_10, mrs_10/20, kf_distance, ema_20/50_distance
    # Market regime: 8.1% win-rate gap bull vs bear; vol regime spans 28.5–50.0%

    # 15. YZ vol z-score — how elevated vs its own 63-day history?
    #     Direct catastrophic predictor (d~0.74)
    yz_vol_zscore_10_2d = zscore_nb(yz_vol_10_2d, window=63)
    yz_vol_zscore_20_2d = zscore_nb(yz_vol_20_2d, window=63)

    # 16. Vol acceleration: yz_vol_10 / yz_vol_20
    #     Rising ratio = volatility regime accelerating → higher crash risk
    yz_vol_accel_2d = np.where(yz_vol_20_2d > 1e-9,
                                yz_vol_10_2d / (yz_vol_20_2d + 1e-9), np.nan)

    # 17. MRS overextension ratio: mrs_10 / |mrs_20|
    #     Catastrophic losers: mrs_10=3.9 vs mrs_20=1.8 — momentum bubble at entry
    mrs_overext_ratio_2d = np.where(np.abs(mrs_20_2d) > 1e-9,
                                     mrs_10_2d / (np.abs(mrs_20_2d) + 1e-9), np.nan)

    # 18. EMA near/far extension ratio: ema_10_dist / ema_50_dist
    #     High ratio → price extended short-term but not long-term → false breakout
    _ema10_arr = ema_10_dist.to_numpy()
    _ema50_arr = ema_50_dist.to_numpy()
    ema_near_far_ratio_2d = np.where(np.abs(_ema50_arr) > 1e-9,
                                      _ema10_arr / (np.abs(_ema50_arr) + 1e-9), np.nan)

    # 19. RSI cross-sectional rank: are we entering the most overbought stock today?
    rsi_rank_14_2d = _df(rsi_14_df.to_numpy()).rank(axis=1, pct=True).to_numpy()

    # 20. Volume spike vs 5-day average — breakout quality confirmation
    _vol_sma5 = SMA.run(volume_df, timeperiod=5).real.to_numpy()
    vol_spike_2d = _safe_log_ratio_2d(volume_2d, _vol_sma5)

    # 21. Close position in day's high-low range
    #     0 = closed at low, 1 = closed at high; weak close on breakout day is bearish
    _day_range = high_2d - low_2d
    close_position_2d = np.where(_day_range > 1e-9, (close_2d - low_2d) / _day_range, 0.5)

    # 22. Distance from 20d / 60d rolling HIGH close (false breakout indicator)
    #     log1p((close - rolling_high) / rolling_high) ≤ 0
    #     ≈ 0 → fresh breakout to new high; very negative → chasing extended move
    dist_20d_high_2d = _safe_log_ratio_2d(close_2d, close_df.rolling(20).max().to_numpy())
    dist_60d_high_2d = _safe_log_ratio_2d(close_2d, close_df.rolling(60).max().to_numpy())

    # ── VNINDEX Market Regime Features (VNINDEX → VN30 → equal-weight proxy) ───
    # Autopsy: Bull=35.7% vs Bear=43.8% win rate (+8.1%); vol regime spans 28.5–50.0%
    if _BENCH_PX_SYM is not None:
        _vn_px = stocks['close'][_BENCH_PX_SYM]
    else:
        _vn_px = pd.Series(np.nanmean(close_2d, axis=1), index=close_df.index)
    _vn_px = _vn_px.reindex(close_df.index, method='ffill')

    _vn_ema50  = _vn_px.ewm(span=50,  adjust=False).mean()
    _vn_ema200 = _vn_px.ewm(span=200, adjust=False).mean()
    _vn_vol20  = _vn_px.pct_change().rolling(20).std()

    _vn_df = pd.DataFrame({
        'vnindex_above_ema50':  (_vn_px > _vn_ema50).astype(float),
        'vnindex_above_ema200': (_vn_px > _vn_ema200).astype(float),
        'vnindex_ret_5d':       _vn_px.pct_change(5),
        'vnindex_ret_20d':      _vn_px.pct_change(20),
        'vnindex_vol_20d':      _vn_vol20,
        'vnindex_vol_zscore':   ((_vn_vol20 - _vn_vol20.rolling(252).mean())
                                 / (_vn_vol20.rolling(252).std() + 1e-9)),
        'vnindex_drawdown':     _vn_px / _vn_px.rolling(252).max() - 1,
    }, index=_vn_px.index)

    # ── Assemble and append to features_df ─────────────────────────────────────
    _extra_feat_arrays = {
        'yz_vol_zscore_10':   yz_vol_zscore_10_2d,
        'yz_vol_zscore_20':   yz_vol_zscore_20_2d,
        'yz_vol_accel':       yz_vol_accel_2d,
        'mrs_overext_ratio':  mrs_overext_ratio_2d,
        'ema_near_far_ratio': ema_near_far_ratio_2d,
        'rsi_rank_14':        rsi_rank_14_2d,
        'vol_spike':          vol_spike_2d,
        'close_position':     close_position_2d,
        'dist_20d_high':      dist_20d_high_2d,
        'dist_60d_high':      dist_60d_high_2d,
    }
    _extra_features = pd.concat(
        [_df(arr).stack().rename(feat) for feat, arr in _extra_feat_arrays.items()],
        axis=1,
    )
    _extra_features.index.names = ['date', 'symbol']

    # Broadcast VNINDEX (date-level) to all (date, symbol) rows
    _vn_long = _vn_df.reindex(features_df.index.get_level_values('date'))
    _vn_long.index = features_df.index

    features_df = pd.concat([features_df, _extra_features, _vn_long], axis=1)

    # Fill NaN in ratio/zscore features with neutral values to avoid dropna() data loss
    _ratio_fills = {
        'yz_vol_accel':       1.0,   # no acceleration = ratio of 1
        'mrs_overext_ratio':  1.0,   # no overextension
        'ema_near_far_ratio': 1.0,   # no near/far divergence
        'yz_vol_zscore_10':   0.0,   # neutral z-score
        'yz_vol_zscore_20':   0.0,
    }
    for col, fill in _ratio_fills.items():
        if col in features_df.columns:
            features_df[col] = features_df[col].fillna(fill)
    # ── VN Priority Features ─────────────────────────────────────────────────────
    # Volatility: rv_20, vol_pctile, atr_pct
    # Position:   range_pos_60, dist_from_high_60
    # Trend:      trend_20, sma_alignment
    # Volume:     vol_zscore, dollar_vol_pctile
    # Cross-sect: rs_vnindex, corr_vnindex, beta_60d

    # 1. Realized volatility 20d (annualised std of log returns)
    rv_20_2d = (log_return_df.rolling(20).std() * np.sqrt(252)).to_numpy()

    # 2. Volatility percentile — fraction of past 252d where yz_vol_10 was lower
    _yz10 = yz_vol_10_2d
    vol_pctile_2d = np.full_like(_yz10, np.nan)
    _W_vol = 252
    for _t in range(_W_vol, _yz10.shape[0]):
        vol_pctile_2d[_t] = (_yz10[_t - _W_vol:_t] < _yz10[_t]).mean(axis=0)

    # 3. ATR % — ATR(14) / close  (bounded ~0–0.10 for most VN stocks)
    ATR = vbt.IndicatorFactory.from_talib('ATR')
    atr_pct_2d = (ATR.run(high_df, low_df, close_df, timeperiod=14).real
                  / close_df.replace(0, np.nan)).to_numpy()

    # 4. Range position in 60d HIGH–LOW band  (0=at 60d low, 1=at 60d high)
    _h60 = high_df.rolling(60).max().to_numpy()
    _l60 = low_df.rolling(60).min().to_numpy()
    _r60 = _h60 - _l60
    range_pos_60_2d = np.where(_r60 > 1e-9, (close_2d - _l60) / _r60, 0.5)

    # 5. Distance from 60d intraday HIGH (log ratio, always ≤ 0)
    dist_from_high_60_2d = _safe_log_ratio_2d(close_2d, _h60)

    # 6. Trend 20d — log return over 20 bars
    trend_20_2d = np.log(close_df / close_df.shift(20)).to_numpy()

    # 7. SMA alignment — fraction of [SMA10,20,50,200] that price is above (0–1)
    _sma10  = SMA.run(close_df, timeperiod=10).real.to_numpy()
    _sma20  = SMA.run(close_df, timeperiod=20).real.to_numpy()
    _sma50  = SMA.run(close_df, timeperiod=50).real.to_numpy()
    _sma200 = SMA.run(close_df, timeperiod=200).real.to_numpy()
    sma_alignment_2d = (
        (close_2d > _sma10).astype(float) + (close_2d > _sma20).astype(float) +
        (close_2d > _sma50).astype(float) + (close_2d > _sma200).astype(float)
    ) / 4.0

    # 8. Volume z-score — (vol - vol_ma20) / vol_std20
    _vol_std20 = volume_df.rolling(20).std().to_numpy()
    vol_zscore_2d = np.where(
        _vol_std20 > 1e-9,
        (volume_2d - volume_df.rolling(20).mean().to_numpy()) / _vol_std20,
        0.0,
    )

    # 9. Dollar-volume percentile — cross-sectional rank of 20d avg (close × volume)
    dollar_vol_pctile_2d = (
        (close_df * volume_df).rolling(20).mean()
        .rank(axis=1, pct=True)
        .to_numpy()
    )

    # 10–12. RS / Corr / Beta vs VNINDEX (60d rolling)
    _vn_r1 = _vn_px.pct_change().reindex(close_df.index, method='ffill').values
    _stk_r1 = close_df.pct_change().to_numpy()
    _stk_r20 = close_df.pct_change(20).to_numpy()
    _vn_r20  = _vn_px.pct_change(20).reindex(close_df.index, method='ffill').values

    rs_vnindex_2d    = _stk_r20 - _vn_r20.reshape(-1, 1)
    corr_vnindex_2d  = np.full_like(close_2d, np.nan)
    beta_60d_2d      = np.full_like(close_2d, np.nan)

    _Wc = 60
    for _t in range(_Wc, close_2d.shape[0]):
        _vn_w  = _vn_r1[_t - _Wc:_t]
        _stk_w = _stk_r1[_t - _Wc:_t, :]
        _vn_m  = _vn_w.mean()
        _stk_m = _stk_w.mean(axis=0)
        _vn_d  = _vn_w - _vn_m
        _cov   = (_vn_d.reshape(-1, 1) * (_stk_w - _stk_m)).mean(axis=0)
        _var   = float((_vn_d ** 2).mean()) + 1e-9
        corr_vnindex_2d[_t] = _cov / (np.sqrt(_var) * (_stk_w.std(axis=0) + 1e-9))
        beta_60d_2d[_t]     = _cov / _var

    # ── Append to features_df ────────────────────────────────────────────────────
    _vn_priority_arrays = {
        'rv_20':             rv_20_2d,
        'vol_pctile':        vol_pctile_2d,
        'atr_pct':           atr_pct_2d,
        'range_pos_60':      range_pos_60_2d,
        'dist_from_high_60': dist_from_high_60_2d,
        'trend_20':          trend_20_2d,
        'sma_alignment':     sma_alignment_2d,
        'vol_zscore':        vol_zscore_2d,
        'dollar_vol_pctile': dollar_vol_pctile_2d,
        'rs_vnindex':        rs_vnindex_2d,
        'corr_vnindex':      corr_vnindex_2d,
        'beta_60d':          beta_60d_2d,
    }
    _vn_prio_df = pd.concat(
        [_df(arr).stack().rename(feat) for feat, arr in _vn_priority_arrays.items()],
        axis=1,
    )
    _vn_prio_df.index.names = ['date', 'symbol']
    features_df = pd.concat([features_df, _vn_prio_df], axis=1)

    # Fill NaN neutral values
    _fills = {'range_pos_60': 0.5, 'sma_alignment': 0.5,
               'vol_zscore': 0.0, 'corr_vnindex': 0.0, 'beta_60d': 1.0}
    for _c, _v in _fills.items():
        features_df[_c] = features_df[_c].fillna(_v)
    from app.services.indicators import calculate_gkyz_volatility

    # ── GKYZ for symbols (multiple windows) ─────────────────────────────────────
    _gkyz_windows = [10, 20]
    _gkyz_sym_arrays = {}

    for _w in _gkyz_windows:
        _gkyz_sym_2d = np.full_like(close_2d, np.nan)
        for _col in range(close_2d.shape[1]):
            try:
                _gkyz_sym_2d[:, _col] = calculate_gkyz_volatility(
                    open_2d[:, _col],
                    high_2d[:, _col],
                    low_2d[:, _col],
                    close_2d[:, _col],
                    window=_w,
                    normalize=True,  # min-max normalize to [0,1]
                )
            except Exception:
                pass
        _gkyz_sym_arrays[f'gkyz_vol_{_w}'] = _gkyz_sym_2d

    # ── GKYZ for benchmark index (uses _BENCH_PX_SYM from pipeline setup cell) ───
    # Column names stay vnindex_gkyz_* for downstream manifests / training lists.
    _gkyz_vn_dict = {}
    _vn_ohlc = None
    if _BENCH_PX_SYM is not None:
        for _panel in (stocks, df):
            for _lev in ('symbol', -1):
                try:
                    _vn_ohlc = _panel.xs(_BENCH_PX_SYM, axis=1, level=_lev)[['open', 'high', 'low', 'close']]
                    break
                except (KeyError, ValueError, TypeError, IndexError):
                    continue
            if _vn_ohlc is not None:
                break
        if _vn_ohlc is None:
            pass

    if _vn_ohlc is not None:
        _vn_open_1d  = _vn_ohlc['open'].values.astype(np.float64)
        _vn_high_1d  = _vn_ohlc['high'].values.astype(np.float64)
        _vn_low_1d   = _vn_ohlc['low'].values.astype(np.float64)
        _vn_close_1d = _vn_ohlc['close'].values.astype(np.float64)
        for _w in _gkyz_windows:
            try:
                _gkyz_vn_1d = calculate_gkyz_volatility(
                    _vn_open_1d, _vn_high_1d, _vn_low_1d, _vn_close_1d,
                    window=_w,
                    normalize=True,
                )
                _gkyz_vn_dict[f'vnindex_gkyz_vol_{_w}'] = _gkyz_vn_1d
            except Exception:
                pass
    else:
        _vn_ohlc = pd.DataFrame(index=df.index)
        for _w in _gkyz_windows:
            _gkyz_vn_dict[f'vnindex_gkyz_vol_{_w}'] = np.full(len(df.index), 0.5, dtype=np.float64)

    # ── Assemble and append to features_df ─────────────────────────────────────
    _gkyz_sym_df = pd.concat(
        [_df(arr).stack().rename(feat) for feat, arr in _gkyz_sym_arrays.items()],
        axis=1,
    )
    _gkyz_sym_df.index.names = ['date', 'symbol']
    features_df = pd.concat([features_df, _gkyz_sym_df], axis=1)

    # Broadcast VNINDEX GKYZ to (date, symbol) rows
    _vn_gkyz_df = pd.DataFrame(_gkyz_vn_dict, index=_vn_ohlc.index)
    _vn_gkyz_long = _vn_gkyz_df.reindex(features_df.index.get_level_values('date'))
    _vn_gkyz_long.index = features_df.index
    features_df = pd.concat([features_df, _vn_gkyz_long], axis=1)

    # Fill NaN values (first window-1 bars) with backward-fill, then neutral default
    for _sym in symbols:
        for _col in _gkyz_sym_arrays.keys():
            sym_mask = features_df.index.get_level_values('symbol') == _sym
            features_df.loc[sym_mask, _col] = (
                features_df.loc[sym_mask, _col]
                .bfill()
                .fillna(0.5)  # neutral volatility if still NaN
            )

    for _col in _gkyz_vn_dict.keys():
        features_df[_col] = features_df[_col].bfill().fillna(0.5)

    feature_dates = pd.to_datetime(features_df.index.get_level_values('date'))
    features_df['dow_sin'] = np.sin(2 * np.pi * feature_dates.dayofweek / 5)
    features_df['dow_cos'] = np.cos(2 * np.pi * feature_dates.dayofweek / 5)
    features_df['month_sin'] = np.sin(2 * np.pi * feature_dates.month / 12)
    features_df['month_cos'] = np.cos(2 * np.pi * feature_dates.month / 12)
    features_df['is_quarter_end'] = (
        (feature_dates.month % 3 == 0) &
        (feature_dates.day > 20)
    ).astype(int)

    return {
        "features_df": features_df,
        "stocks": stocks,
        "stocks_exclude_vnindex": stocks_exclude_vnindex,
        "benchmark_symbol": _BENCH_PX_SYM,
    }


def build_features_from_ohlc_panel(
    df: Any,
    watchlist_symbols: list[str] | None = None,
) -> Any:
    """Build inference-ready features from an already-loaded OHLC panel."""
    return _build_feature_context_from_ohlc_panel(df, watchlist_symbols)["features_df"]


def _run_feature_pipeline_impl(refresh_stock_cache: bool = False) -> dict[str, Any]:
    """Build features, trades, and training labels in memory."""
    import json
    import numpy as np
    import pandas as pd

    df = load_ohlc_panel(output_dir=Path.cwd(), refresh_cache=refresh_stock_cache)
    feature_context = _build_feature_context_from_ohlc_panel(df)
    features_df = feature_context["features_df"]
    stocks_exclude_vnindex = feature_context["stocks_exclude_vnindex"]
    from app.services.strategies import BreakoutTTMV1, FIXED_TTM_PARAMS

    # Same defaults as the 005c backtest raw leg (no MS position sizing).
    MS_POSITION_BUDGET = 100.0

    total_trades = pd.DataFrame()
    total_open_trades = pd.DataFrame()

    for ver in FIXED_TTM_PARAMS:
        if ver == 'v3':
            continue

        strategy = BreakoutTTMV1(
            stocks_exclude_vnindex,
            ver,
            init_cash=MS_POSITION_BUDGET,
        )
        param_dict = strategy.param_dict
        portfolio = strategy.get_portfolio()

        trades = pd.DataFrame(portfolio.trades.records)
        trades['metadata'] = json.dumps(param_dict)
        open_trade = pd.DataFrame(portfolio.trades.open.records)
        open_trade['metadata'] = json.dumps(param_dict)

        total_trades = pd.concat([total_trades, trades])
        total_open_trades = pd.concat([total_open_trades, open_trade])

    total_trades['type'] = 'closed_trades'
    total_open_trades['type'] = 'open_trades'

    # Filter: keep closed trades that are not also in open trades
    open_trade_keys = pd.MultiIndex.from_frame(total_open_trades[['col', 'entry_idx']])
    total_trade_keys = pd.MultiIndex.from_frame(total_trades[['col', 'entry_idx']])
    mask = ~total_trade_keys.isin(open_trade_keys)
    filtered_total_trades = total_trades[mask]

    # Combine closed + open
    all_trades_df = pd.concat([filtered_total_trades, total_open_trades])
    all_trades_df = (
        all_trades_df
        .drop_duplicates(subset=['col', 'entry_idx'], keep='first')
        .reset_index(drop=True)
    )
    # col maps to stocks_exclude_vnindex (not stocks — VNINDEX was removed)
    all_trades_df['symbol'] = all_trades_df.apply(
        lambda x: stocks_exclude_vnindex.close.columns[x['col']], axis=1)
    all_trades_df['entry_date'] = all_trades_df.apply(
        lambda x: stocks_exclude_vnindex.index[x['entry_idx']], axis=1)
    all_trades_df['exit_date'] = all_trades_df.apply(
        lambda x: stocks_exclude_vnindex.index[min(int(x['exit_idx']), len(stocks_exclude_vnindex) - 1)]
                  if pd.notna(x['exit_idx']) else stocks_exclude_vnindex.index[-1],
        axis=1,
    )
    all_trades_df['date'] = all_trades_df['entry_date']  # alias for merging
    all_trades_df.sort_values(by='entry_date', ascending=False, inplace=True)

    # === Label Configuration ===
    ROUND_TRIP_COST   = 0.003   # 30 bps round-trip (entry + exit fees)
    MIN_RETURN_EDGE   = 0.005   # require at least 50 bps net edge
    LABEL_THRESHOLD   = ROUND_TRIP_COST + MIN_RETURN_EDGE  # ~0.8%

    # Filter out open trades (no realized return) for training
    closed_trades = all_trades_df[all_trades_df['type'] == 'closed_trades'].copy()

    # Net return after costs
    closed_trades['net_return'] = closed_trades['return'] - ROUND_TRIP_COST

    # Binary label: 1 if net return > threshold, else 0
    closed_trades['Y'] = (closed_trades['net_return'] > MIN_RETURN_EDGE).astype(int)

    # Sort by entry_date — CRITICAL for time-series ordering
    closed_trades = closed_trades.sort_values('entry_date').reset_index(drop=True)
    features_df_reset = features_df.reset_index()

    # 🚨 Exclude any look-ahead columns by name pattern
    SAFE_FEATURE_COLS = [
        c for c in features_df_reset.columns
        if not c.startswith('next_') and c not in ['open', 'high', 'low', 'close', 'volume']
    ]


    # Merge
    training_df = pd.merge(
        closed_trades[['symbol', 'date', 'entry_date', 'exit_date',
                       'return', 'net_return', 'Y']],
        features_df_reset[SAFE_FEATURE_COLS],
        left_on=['date', 'symbol'],
        right_on=['date', 'symbol'],
        how='inner',
    )

    # Sort by entry date AGAIN (post-merge)
    training_df = training_df.sort_values('entry_date').reset_index(drop=True)
    training_df = training_df.dropna()
    training_feature_columns = [
        # ── Original features ──────────────────────────────────────────────────
        'rsi_window_5', 'rsi_window_14', 'mfi_21',
        'obv', 'volume_threshold_ma_10', 'volume_threshold_ma_20',
        'efi_zscore_10', 'efi_zscore_20',
        'ema_10_distance', 'ema_20_distance', 'ema_50_distance', 'ema_200_distance',
        'kf_distance', 'zscore_kf_10', 'zscore_kf_20',
        'vwap_distance_highest', 'vwap_distance_lowest',
        'mrs_10', 'mrs_20', 'rs_10', 'rs_20',
        'msr_rank_10', 'msr_rank_20',
        'zscore_10_log_return', 'zscore_20_log_return',
        'yz_vol_10', 'yz_vol_20',
        'dc_tmv',

        # ── Autopsy-driven: volatility regime (catastrophic loss, d~0.74) ──────
        'yz_vol_zscore_10', 'yz_vol_zscore_20',
        'yz_vol_accel',

        # ── Autopsy-driven: overextension at entry ─────────────────────────────
        'mrs_overext_ratio',
        'ema_near_far_ratio',

        # ── Autopsy-driven: breakout quality ──────────────────────────────────
        'rsi_rank_14',
        'vol_spike',
        'close_position',
        'dist_20d_high',
        'dist_60d_high',

        # ── Autopsy-driven: VNINDEX market regime ─────────────────────────────
        # 'vnindex_above_ema50', 'vnindex_above_ema200',
        # 'vnindex_ret_5d', 'vnindex_ret_20d',
        # 'vnindex_vol_20d', 'vnindex_vol_zscore',
        # 'vnindex_drawdown',

        # ── VN Priority features ──────────────────────────────────────────────
        'rv_20', 'vol_pctile', 'atr_pct',
        'range_pos_60', 'dist_from_high_60',
        'trend_20', 'sma_alignment',
        'vol_zscore', 'dollar_vol_pctile',
        'rs_vnindex', 'corr_vnindex', 'beta_60d',

        # ── GKYZ Volatility (intraday-informed, short-term periods only) ───────
        'gkyz_vol_10', 'gkyz_vol_20',
        'vnindex_gkyz_vol_10', 'vnindex_gkyz_vol_20',

        # ── Seasonality (no NaN, safe) ─────────────────────────────────────────
        'dow_sin', 'dow_cos', 'month_sin', 'month_cos', 'is_quarter_end',
    ]

    # Filter to columns that actually exist in training_df
    training_feature_columns = [c for c in training_feature_columns if c in training_df.columns]

    return locals()
    
def _run_training_pipeline_impl(
    training_df: Any,
    training_feature_columns: list[str],
) -> dict[str, Any]:
    """Train base models, ensembles, and evaluation artifacts from in-memory features."""
    import json
    import warnings
    import joblib
    import numpy as np
    import pandas as pd
    from datetime import datetime

    warnings.filterwarnings('ignore')
    training_df = training_df.copy()
    training_df['entry_date'] = pd.to_datetime(training_df['entry_date'])
    training_df['exit_date']  = pd.to_datetime(training_df['exit_date'])
    training_df = training_df.sort_values('entry_date').reset_index(drop=True)

    # Filter to columns that actually exist (guard against schema drift)
    training_feature_columns = [c for c in training_feature_columns if c in training_df.columns]
    # Dynamic chronological split: latest window is holdout; older rows tune/evaluate models.
    TEST_MONTHS = int(os.getenv("META_LABEL_TEST_MONTHS", "12"))
    EMBARGO_DAYS = int(os.getenv("META_LABEL_EMBARGO_DAYS", "10"))

    max_entry_date = training_df['entry_date'].max()
    train_end_date = max_entry_date - pd.DateOffset(months=TEST_MONTHS)
    train_mask = training_df['entry_date'] <= train_end_date
    embargo_start = train_end_date
    embargo_end = embargo_start + pd.Timedelta(days=EMBARGO_DAYS)
    test_mask = training_df['entry_date'] > embargo_end
    if not train_mask.any() or not test_mask.any():
        raise ValueError(
            "Dynamic train/test split produced an empty partition. "
            "Adjust META_LABEL_TEST_MONTHS or META_LABEL_EMBARGO_DAYS."
        )

    split_metadata = {
        "max_entry_date": str(max_entry_date.date()),
        "train_end_date": str(train_end_date.date()),
        "embargo_end": str(embargo_end.date()),
        "test_months": TEST_MONTHS,
        "embargo_days": EMBARGO_DAYS,
    }

    X_train = training_df.loc[train_mask, training_feature_columns].copy()
    y_train = training_df.loc[train_mask, 'Y'].copy()
    X_test  = training_df.loc[test_mask,  training_feature_columns].copy()
    y_test  = training_df.loc[test_mask,  'Y'].copy()
    returns_test = training_df.loc[test_mask, 'net_return'].copy()
    dates_train  = training_df.loc[train_mask, 'entry_date'].copy()
    dates_test   = training_df.loc[test_mask,  'entry_date'].copy()
    exit_dates_train = training_df.loc[train_mask, 'exit_date'].copy()
    X_production = training_df[training_feature_columns].copy()
    y_production = training_df['Y'].copy()
    dates_production = training_df['entry_date'].copy()
    exit_dates_production = training_df['exit_date'].copy()
    # === Scaling — fit on train ONLY, transform test ===
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    X_train_scaled = pd.DataFrame(
        scaler.fit_transform(X_train),
        columns=X_train.columns,
        index=X_train.index,
    )
    X_test_scaled = pd.DataFrame(
        scaler.transform(X_test),
        columns=X_test.columns,
        index=X_test.index,
    )
    production_scaler = StandardScaler()
    X_production_scaled = pd.DataFrame(
        production_scaler.fit_transform(X_production),
        columns=X_production.columns,
        index=X_production.index,
    )

    # 🚨 NOTE: We do NOT use SMOTE.
    # SMOTE creates synthetic samples by interpolating between neighbors —
    # this doesn't make sense for time-series financial data and can leak info.
    # Instead, we use class_weight='balanced' or scale_pos_weight in models.
    def compute_sample_uniqueness(entry_dates: pd.Series, exit_dates: pd.Series) -> np.ndarray:
        """
        Compute López de Prado's sample uniqueness:
        For each sample, count how many other samples' [entry, exit] intervals overlap with it.
        Weight = 1 / overlap_count (then normalized).
        """
        n = len(entry_dates)
        entries = entry_dates.values
        exits   = exit_dates.values
        overlaps = np.zeros(n)

        for i in range(n):
            # Count concurrent samples (including self)
            overlaps[i] = ((entries <= exits[i]) & (exits >= entries[i])).sum()

        weights = 1.0 / np.maximum(overlaps, 1)
        return weights / weights.mean()  # normalize to mean=1


    train_weights = compute_sample_uniqueness(
        dates_train.reset_index(drop=True),
        exit_dates_train.reset_index(drop=True),
    )
    production_weights = compute_sample_uniqueness(
        dates_production.reset_index(drop=True),
        exit_dates_production.reset_index(drop=True),
    )
    class PurgedKFold:
        """
        Purged & Embargoed K-Fold for time-series with overlapping labels.

        Parameters
        ----------
        n_splits : int
        entry_dates : pd.Series of trade entry timestamps
        exit_dates  : pd.Series of trade exit timestamps
        embargo_pct : float, fraction of dataset to embargo after each test fold
        """
        def __init__(self, n_splits=5, entry_dates=None, exit_dates=None, embargo_pct=0.01):
            self.n_splits    = n_splits
            self.entry_dates = entry_dates.reset_index(drop=True)
            self.exit_dates  = exit_dates.reset_index(drop=True)
            self.embargo_pct = embargo_pct

        def split(self, X, y=None, groups=None):
            n = len(X)
            indices = np.arange(n)
            embargo_n = int(n * self.embargo_pct)

            # Test folds are contiguous chunks ordered in time
            test_ranges = np.array_split(indices, self.n_splits)

            for test_idx in test_ranges:
                test_start_t = self.entry_dates.iloc[test_idx[0]]
                test_end_t   = self.exit_dates.iloc[test_idx[-1]]

                train_mask = np.ones(n, dtype=bool)
                train_mask[test_idx] = False

                # Purge: remove training samples whose [entry, exit] overlaps test period
                for i in indices[train_mask]:
                    if (self.entry_dates.iloc[i] <= test_end_t) and \
                       (self.exit_dates.iloc[i]  >= test_start_t):
                        train_mask[i] = False

                # Embargo: remove samples right after test fold
                embargo_end = min(test_idx[-1] + 1 + embargo_n, n)
                train_mask[test_idx[-1]+1 : embargo_end] = False

                train_idx = indices[train_mask]
                yield train_idx, test_idx

        def get_n_splits(self, X=None, y=None, groups=None):
            return self.n_splits


    # === Build CV splitter for training set ===
    purged_cv = PurgedKFold(
        n_splits=5,
        entry_dates=dates_train,
        exit_dates=exit_dates_train,
        embargo_pct=0.01,
    )
    from sklearn.metrics import (
        roc_auc_score, precision_score, recall_score, f1_score,
        brier_score_loss,
    )


    def evaluate_meta_model(
        y_true: pd.Series,
        y_proba: np.ndarray,
        returns: pd.Series,
        threshold: float = 0.55,
    ) -> dict:
        """Compute trading-relevant evaluation metrics."""
        y_pred = (y_proba >= threshold).astype(int)

        # Filter strategy returns: only take trades where model says "win"
        take_mask = y_pred == 1
        base_returns     = returns
        filtered_returns = returns[take_mask]

        def sharpe(r):
            return r.mean() / (r.std() + 1e-9) * np.sqrt(252) if len(r) > 1 else 0

        metrics = {
            'auc':              roc_auc_score(y_true, y_proba),
            'brier_score':      brier_score_loss(y_true, y_proba),
            'precision':        precision_score(y_true, y_pred, zero_division=0),
            'recall':           recall_score(y_true, y_pred, zero_division=0),
            'f1':               f1_score(y_true, y_pred, zero_division=0),
            'trades_taken':     int(take_mask.sum()),
            'trades_filtered':  int((~take_mask).sum()),
            'pct_filtered':     float((~take_mask).mean()),
            'base_mean_ret':    float(base_returns.mean()),
            'filt_mean_ret':    float(filtered_returns.mean()) if len(filtered_returns) else 0,
            'base_sharpe':      sharpe(base_returns),
            'filt_sharpe':      sharpe(filtered_returns) if len(filtered_returns) > 1 else 0,
        }
        metrics['sharpe_lift'] = metrics['filt_sharpe'] - metrics['base_sharpe']
        return metrics
        
    import optuna
    from xgboost import XGBClassifier
    from sklearn.metrics import roc_auc_score

    n_pos = (y_train == 1).sum()
    n_neg = (y_train == 0).sum()
    scale_pos_weight = n_neg / max(n_pos, 1)

    ES_ROUNDS = 50   # shared early-stopping patience across all models

    def _rows(arr, idx):
        """Index rows whether arr is pandas (iloc) or numpy."""
        return arr.iloc[idx] if hasattr(arr, 'iloc') else np.asarray(arr)[idx]

    def objective_xgb(trial):
        params = {
            'objective':            'binary:logistic',
            'eval_metric':          'auc',
            'n_estimators':         1000,        # ES decides actual count
            'early_stopping_rounds': ES_ROUNDS,
            'learning_rate':        trial.suggest_float('learning_rate', 0.01, 0.2, log=True),
            'max_depth':            trial.suggest_int('max_depth', 3, 7),
            'min_child_weight':     trial.suggest_int('min_child_weight', 1, 10),
            'subsample':            trial.suggest_float('subsample', 0.6, 1.0),
            'colsample_bytree':     trial.suggest_float('colsample_bytree', 0.6, 1.0),
            'gamma':                trial.suggest_float('gamma', 0, 5),
            'reg_alpha':            trial.suggest_float('reg_alpha', 1e-8, 1.0, log=True),
            'reg_lambda':           trial.suggest_float('reg_lambda', 1e-8, 1.0, log=True),
            'scale_pos_weight':     scale_pos_weight,
            'random_state':         42,
            'verbosity':            0,
        }
        scores = []
        for train_idx, val_idx in purged_cv.split(X_train_scaled):
            m = XGBClassifier(**params)
            m.fit(
                _rows(X_train_scaled, train_idx), _rows(y_train, train_idx),
                sample_weight=_rows(train_weights, train_idx),
                eval_set=[(_rows(X_train_scaled, val_idx), _rows(y_train, val_idx))],
                verbose=False,
            )
            proba = m.predict_proba(_rows(X_train_scaled, val_idx))[:, 1]
            scores.append(roc_auc_score(_rows(y_train, val_idx), proba))
        return np.mean(scores)

    sampler = optuna.samplers.TPESampler(seed=42)
    study_xgb = optuna.create_study(direction='maximize', sampler=sampler)
    study_xgb.optimize(objective_xgb, n_trials=80, show_progress_bar=False)
    # === Train final XGBoost ===
    _es_cut = int(len(X_train_scaled) * 0.80)
    X_es,  y_es  = X_train_scaled.iloc[:_es_cut], y_train.iloc[:_es_cut]
    X_cal, y_cal = X_train_scaled.iloc[_es_cut:], y_train.iloc[_es_cut:]
    w_es = train_weights[:_es_cut]

    final_xgb = XGBClassifier(
        **study_xgb.best_params,
        objective='binary:logistic',
        eval_metric='auc',
        n_estimators=1000,
        early_stopping_rounds=ES_ROUNDS,
        scale_pos_weight=scale_pos_weight,
        random_state=42,
        verbosity=0,
    )
    final_xgb.fit(
        X_es, y_es,
        sample_weight=w_es,
        eval_set=[(X_cal, y_cal)],
        verbose=False,
    )

    # Use raw probabilities (no calibration)
    # XGBoost with scale_pos_weight outputs probabilities from a reweighted distribution.
    # Re-calibrate to the true class prior using Bayes' theorem (Platt-style).
    _raw_xgb = final_xgb.predict_proba(X_test_scaled)[:, 1]
    _prior_pos = y_train.mean()  # true class frequency (~0.354)
    _spw = scale_pos_weight  # n_neg / n_pos
    # Undo the scale_pos_weight shift: p_true = p_raw / (p_raw + (1 - p_raw) / _spw)
    y_proba_xgb = _raw_xgb / (_raw_xgb + (1.0 - _raw_xgb) / _spw)

    metrics_xgb = evaluate_meta_model(y_test, y_proba_xgb, returns_test, threshold=0.50)
    os.makedirs(MODELS_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d')
    from lightgbm import LGBMClassifier
    from lightgbm import early_stopping as lgb_es, log_evaluation as lgb_log

    def objective_lgbm(trial):
        params = {
            'objective':         'binary',
            'metric':            'auc',
            'n_estimators':      1000,
            'learning_rate':     trial.suggest_float('learning_rate', 0.01, 0.2, log=True),
            'num_leaves':        trial.suggest_int('num_leaves', 15, 63),
            'max_depth':         trial.suggest_int('max_depth', 3, 7),
            'min_child_samples': trial.suggest_int('min_child_samples', 10, 80),
            'subsample':         trial.suggest_float('subsample', 0.6, 1.0),
            'colsample_bytree':  trial.suggest_float('colsample_bytree', 0.6, 1.0),
            'reg_alpha':         trial.suggest_float('reg_alpha', 1e-8, 1.0, log=True),
            'reg_lambda':        trial.suggest_float('reg_lambda', 1e-8, 1.0, log=True),
            'class_weight':      'balanced',
            'random_state':      42,
            'verbosity':         -1,
        }
        scores = []
        for train_idx, val_idx in purged_cv.split(X_train_scaled):
            m = LGBMClassifier(**params)
            m.fit(
                _rows(X_train_scaled, train_idx), _rows(y_train, train_idx),
                sample_weight=_rows(train_weights, train_idx),
                eval_set=[(_rows(X_train_scaled, val_idx), _rows(y_train, val_idx))],
                callbacks=[lgb_es(ES_ROUNDS, verbose=False), lgb_log(period=-1)],
            )
            proba = m.predict_proba(_rows(X_train_scaled, val_idx))[:, 1]
            scores.append(roc_auc_score(_rows(y_train, val_idx), proba))
        return np.mean(scores)

    study_lgbm = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=42))
    study_lgbm.optimize(objective_lgbm, n_trials=80, show_progress_bar=False)
    final_lgbm = LGBMClassifier(
        **study_lgbm.best_params,
        objective='binary',
        n_estimators=1000,
        class_weight='balanced',
        random_state=42,
        verbosity=-1,
    )
    final_lgbm.fit(
        X_es, y_es,
        sample_weight=w_es,
        eval_set=[(X_cal, y_cal)],
        callbacks=[lgb_es(ES_ROUNDS, verbose=False), lgb_log(period=-1)],
    )

    # Use raw probabilities (no calibration)
    # LightGBM with class_weight='balanced' outputs probabilities from a balanced distribution.
    # Re-calibrate to the true class prior.
    _raw_lgbm = final_lgbm.predict_proba(X_test_scaled)[:, 1]
    _prior_pos = y_train.mean()  # true class frequency (~0.354)
    _class_ratio = (1.0 - _prior_pos) / _prior_pos  # n_neg / n_pos
    y_proba_lgbm = _raw_lgbm / (_raw_lgbm + (1.0 - _raw_lgbm) / _class_ratio)

    metrics_lgbm = evaluate_meta_model(y_test, y_proba_lgbm, returns_test, threshold=0.50)

    from catboost import CatBoostClassifier

    def objective_cat(trial):
        params = {
            'objective':            'Logloss',
            'eval_metric':          'AUC',
            'iterations':           1000,
            'early_stopping_rounds': ES_ROUNDS,
            'learning_rate':        trial.suggest_float('learning_rate', 0.01, 0.2, log=True),
            'depth':                trial.suggest_int('depth', 3, 7),
            'l2_leaf_reg':          trial.suggest_float('l2_leaf_reg', 1e-3, 10.0, log=True),
            'random_strength':      trial.suggest_float('random_strength', 1e-3, 10.0, log=True),
            'bagging_temperature':  trial.suggest_float('bagging_temperature', 0, 5),
            'auto_class_weights':   'Balanced',
            'random_seed':          42,
            'verbose':              False,
        }
        scores = []
        for train_idx, val_idx in purged_cv.split(X_train_scaled):
            m = CatBoostClassifier(**params)
            m.fit(
                _rows(X_train_scaled, train_idx), _rows(y_train, train_idx),
                sample_weight=_rows(train_weights, train_idx),
                eval_set=(_rows(X_train_scaled, val_idx), _rows(y_train, val_idx)),
            )
            proba = m.predict_proba(_rows(X_train_scaled, val_idx))[:, 1]
            scores.append(roc_auc_score(_rows(y_train, val_idx), proba))
        return float(np.mean(scores))

    study_cat = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=42))
    study_cat.optimize(objective_cat, n_trials=60, show_progress_bar=False)
    final_cat = CatBoostClassifier(
        **study_cat.best_params,
        objective='Logloss',
        iterations=1000,
        early_stopping_rounds=ES_ROUNDS,
        auto_class_weights='Balanced',
        random_seed=42,
        verbose=False,
    )
    final_cat.fit(
        X_es, y_es,
        sample_weight=w_es,
        eval_set=(X_cal, y_cal),
    )

    # Use raw probabilities (no calibration)
    y_proba_cat = final_cat.predict_proba(X_test_scaled)[:, 1]

    metrics_cat = evaluate_meta_model(y_test, y_proba_cat, returns_test, threshold=0.50)

    # Preserve fallback aliases used by the calibration-collapse guard.
    y_proba_xgb_raw = y_proba_xgb
    y_proba_lgbm_raw = y_proba_lgbm
    y_proba_cat_raw = y_proba_cat


    # Build ensemble probabilities for downstream inference/evaluation.
    from sklearn.linear_model import LogisticRegression

    def _rebalance_weighted_proba(raw_proba, class_ratio):
        raw_proba = np.clip(np.asarray(raw_proba, dtype=float), 1e-6, 1 - 1e-6)
        return raw_proba / (raw_proba + (1.0 - raw_proba) / class_ratio)

    base_model_probas = {
        'XGBoost': y_proba_xgb,
        'LightGBM': y_proba_lgbm,
        'CatBoost': y_proba_cat,
    }

    y_proba_xgb_cal = _rebalance_weighted_proba(
        final_xgb.predict_proba(X_cal)[:, 1], scale_pos_weight
    )
    y_proba_lgbm_cal = _rebalance_weighted_proba(
        final_lgbm.predict_proba(X_cal)[:, 1], (1.0 - y_train.mean()) / y_train.mean()
    )
    y_proba_cat_cal = final_cat.predict_proba(X_cal)[:, 1]

    base_model_probas_cal = {
        'XGBoost': y_proba_xgb_cal,
        'LightGBM': y_proba_lgbm_cal,
        'CatBoost': y_proba_cat_cal,
    }

    val_auc_by_model = {
        name: roc_auc_score(y_cal, proba)
        for name, proba in base_model_probas_cal.items()
    }
    auc_edge_weights = np.array([
        max(val_auc_by_model[name] - 0.5, 0.0)
        for name in base_model_probas
    ], dtype=float)
    if auc_edge_weights.sum() == 0:
        auc_edge_weights = np.ones(len(base_model_probas), dtype=float)
    auc_edge_weights = auc_edge_weights / auc_edge_weights.sum()

    _test_matrix = np.column_stack([base_model_probas[name] for name in base_model_probas])
    _cal_matrix = np.column_stack([base_model_probas_cal[name] for name in base_model_probas_cal])

    ensemble_probas = {
        'Ensemble: Soft Voting': _test_matrix.mean(axis=1),
        'Ensemble: Val-AUC Weighted': np.average(_test_matrix, axis=1, weights=auc_edge_weights),
        'Ensemble: Rank Average': np.column_stack([
            pd.Series(base_model_probas[name]).rank(pct=True).to_numpy()
            for name in base_model_probas
        ]).mean(axis=1),
    }
    ensemble_probas_cal = {
        'Ensemble: Soft Voting': _cal_matrix.mean(axis=1),
        'Ensemble: Val-AUC Weighted': np.average(_cal_matrix, axis=1, weights=auc_edge_weights),
        'Ensemble: Rank Average': np.column_stack([
            pd.Series(base_model_probas_cal[name]).rank(pct=True).to_numpy()
            for name in base_model_probas_cal
        ]).mean(axis=1),
    }

    stacker = LogisticRegression(max_iter=1000, class_weight='balanced', random_state=42)
    stacker.fit(_cal_matrix, y_cal, sample_weight=train_weights[_es_cut:])
    ensemble_probas['Ensemble: Stacked Logistic'] = stacker.predict_proba(_test_matrix)[:, 1]
    ensemble_probas_cal['Ensemble: Stacked Logistic'] = stacker.predict_proba(_cal_matrix)[:, 1]

    model_probas = {**base_model_probas, **ensemble_probas}
    model_metrics = {
        name: evaluate_meta_model(y_test, proba, returns_test, threshold=0.50)
        for name, proba in model_probas.items()
    }

    _selectable_ensembles = [name for name in ensemble_probas if name != 'Ensemble: Stacked Logistic']
    best_ensemble_name = max(
        _selectable_ensembles,
        key=lambda name: roc_auc_score(y_cal, ensemble_probas_cal[name]),
    )
    best_ensemble_proba = ensemble_probas[best_ensemble_name]

    # Refit production artifacts on all labeled rows after holdout evaluation is complete.
    prod_n_pos = int((y_production == 1).sum())
    prod_n_neg = int((y_production == 0).sum())
    prod_scale_pos_weight = prod_n_neg / max(prod_n_pos, 1)
    prod_class_ratio = (1.0 - y_production.mean()) / max(float(y_production.mean()), 1e-9)

    _prod_cut = int(len(X_production_scaled) * 0.80)
    _prod_cut = min(max(_prod_cut, 1), len(X_production_scaled) - 1)
    X_prod_es = X_production_scaled.iloc[:_prod_cut]
    y_prod_es = y_production.iloc[:_prod_cut]
    X_prod_cal = X_production_scaled.iloc[_prod_cut:]
    y_prod_cal = y_production.iloc[_prod_cut:]
    w_prod_es = production_weights[:_prod_cut]

    _xgb_bp = {k: v for k, v in study_xgb.best_params.items() if k not in ('early_stopping_rounds', 'callbacks')}
    production_xgb_es = XGBClassifier(
        **_xgb_bp,
        objective='binary:logistic',
        eval_metric='auc',
        n_estimators=1000,
        early_stopping_rounds=ES_ROUNDS,
        scale_pos_weight=prod_scale_pos_weight,
        random_state=42,
        verbosity=0,
    )
    production_xgb_es.fit(
        X_prod_es, y_prod_es,
        sample_weight=w_prod_es,
        eval_set=[(X_prod_cal, y_prod_cal)],
        verbose=False,
    )
    _prod_xgb_rounds = getattr(production_xgb_es, 'best_iteration', None)
    _prod_xgb_rounds = int(_prod_xgb_rounds + 1) if _prod_xgb_rounds is not None else 1000
    production_xgb = XGBClassifier(
        **_xgb_bp,
        objective='binary:logistic',
        eval_metric='auc',
        n_estimators=_prod_xgb_rounds,
        scale_pos_weight=prod_scale_pos_weight,
        random_state=42,
        verbosity=0,
    )
    production_xgb.fit(X_production_scaled, y_production, sample_weight=production_weights)

    production_lgbm_es = LGBMClassifier(
        **study_lgbm.best_params,
        objective='binary',
        n_estimators=1000,
        class_weight='balanced',
        random_state=42,
        verbosity=-1,
    )
    production_lgbm_es.fit(
        X_prod_es, y_prod_es,
        sample_weight=w_prod_es,
        eval_set=[(X_prod_cal, y_prod_cal)],
        callbacks=[lgb_es(ES_ROUNDS, verbose=False), lgb_log(period=-1)],
    )
    _prod_lgbm_rounds = int(getattr(production_lgbm_es, 'best_iteration_', None) or 1000)
    production_lgbm = LGBMClassifier(
        **study_lgbm.best_params,
        objective='binary',
        n_estimators=_prod_lgbm_rounds,
        class_weight='balanced',
        random_state=42,
        verbosity=-1,
    )
    production_lgbm.fit(X_production_scaled, y_production, sample_weight=production_weights)

    production_cat_es = CatBoostClassifier(
        **study_cat.best_params,
        objective='Logloss',
        iterations=1000,
        early_stopping_rounds=ES_ROUNDS,
        auto_class_weights='Balanced',
        random_seed=42,
        verbose=False,
    )
    production_cat_es.fit(
        X_prod_es, y_prod_es,
        sample_weight=w_prod_es,
        eval_set=(X_prod_cal, y_prod_cal),
    )
    _prod_cat_rounds = production_cat_es.get_best_iteration()
    _prod_cat_rounds = int(_prod_cat_rounds + 1) if _prod_cat_rounds is not None else int(production_cat_es.tree_count_)
    production_cat = CatBoostClassifier(
        **study_cat.best_params,
        objective='Logloss',
        iterations=max(_prod_cat_rounds, 1),
        auto_class_weights='Balanced',
        random_seed=42,
        verbose=False,
    )
    production_cat.fit(X_production_scaled, y_production, sample_weight=production_weights)

    production_xgb.save_model(str(MODELS_DIR / f'xgboost_meta_{ts}.ubj'))
    production_lgbm.booster_.save_model(str(MODELS_DIR / f'lightgbm_meta_{ts}.txt'))
    production_cat.save_model(str(MODELS_DIR / f'catboost_meta_{ts}.cbm'))
    joblib.dump(production_scaler, MODELS_DIR / f'meta_label_scaler_{ts}.joblib')

    ensemble_weights = {
        name: float(weight)
        for name, weight in zip(base_model_probas.keys(), auc_edge_weights)
    }
    training_metadata = {
        "split": split_metadata,
        "production_rows": int(len(training_df)),
        "feature_columns": training_feature_columns,
        "best_ensemble_name": best_ensemble_name,
        "ensemble_weights": ensemble_weights,
        "holdout_metrics": {
            name: {metric: float(value) for metric, value in metrics.items()}
            for name, metrics in model_metrics.items()
        },
        "production_class_ratio": float(prod_class_ratio),
        "production_scale_pos_weight": float(prod_scale_pos_weight),
    }
    with (MODELS_DIR / f'meta_label_training_metadata_{ts}.json').open('w', encoding='utf-8') as f:
        json.dump(training_metadata, f, indent=2)

    return locals()


@task
def build_meta_label_features(
    output_dir: str = str(PIPELINE_OUTPUT_DIR),
    refresh_stock_cache: bool | None = None,
) -> dict[str, Any]:
    """Build trade labels/features and return training data in memory."""
    _configure_runtime()
    output_path = Path(output_dir).resolve()
    if refresh_stock_cache is None:
        refresh_stock_cache = _get_env_bool("META_LABEL_REFRESH_STOCK_CACHE", False)

    try:
        with _working_directory(output_path):
            namespace = _run_feature_pipeline_impl(refresh_stock_cache=refresh_stock_cache)
    finally:
        _collect_garbage()

    training_df = namespace["training_df"]
    feature_columns = namespace["training_feature_columns"]
    return {
        "training_df": training_df,
        "feature_columns": feature_columns,
        "feature_count": len(feature_columns),
        "training_rows": int(len(training_df)),
    }


@task(log_prints=True)
def train_meta_label_models(
    training_df: Any,
    training_feature_columns: list[str],
    output_dir: str = str(PIPELINE_OUTPUT_DIR),
    models_dir: str = str(MODELS_DIR),
) -> dict[str, Any]:
    """Train meta-label models from in-memory training data."""
    global MODELS_DIR

    _configure_runtime()
    output_path = Path(output_dir).resolve()
    model_path = Path(models_dir).resolve()
    model_path.mkdir(parents=True, exist_ok=True)
    before_mtimes = {p.resolve(): p.stat().st_mtime for p in model_path.glob("*") if p.is_file()}

    previous_models_dir = MODELS_DIR
    MODELS_DIR = model_path
    try:
        with _working_directory(output_path):
            namespace = _run_training_pipeline_impl(training_df, training_feature_columns)
    finally:
        MODELS_DIR = previous_models_dir
        _collect_garbage()

    after = [p.resolve() for p in model_path.glob("*") if p.is_file()]
    created_or_updated = [
        p for p in after
        if p not in before_mtimes or p.stat().st_mtime > before_mtimes[p]
    ]
    created_or_updated = sorted(created_or_updated, key=lambda p: p.stat().st_mtime)

    artifacts = {
        "models_dir": str(model_path),
        "artifacts": [_artifact_info(path) for path in created_or_updated],
        "best_ensemble_name": namespace.get("best_ensemble_name"),
        "model_count": len(namespace.get("model_probas", {})),
        "model_metrics": _serialize_model_metrics(namespace.get("model_metrics", {})),
    }
    print(_format_metric_comparison(namespace.get("model_metrics", {}), artifacts["best_ensemble_name"]))
    return artifacts


@flow
def train_meta_label_models_pipeline(
    output_dir: str = str(PIPELINE_OUTPUT_DIR),
    models_dir: str = str(MODELS_DIR),
    refresh_stock_cache: bool = False,
) -> dict[str, Any]:
    """Flow: build trade features and train meta-label models from this Python file."""
    result: dict[str, Any] = {}

    feature_payload = build_meta_label_features(
        output_dir=output_dir,
        refresh_stock_cache=refresh_stock_cache,
    )
    result["features"] = {
        "feature_count": feature_payload["feature_count"],
        "training_rows": feature_payload["training_rows"],
    }

    result["training"] = train_meta_label_models(
        training_df=feature_payload["training_df"],
        training_feature_columns=feature_payload["feature_columns"],
        output_dir=output_dir,
        models_dir=models_dir,
    )
    return result


if __name__ == "__main__":
    train_meta_label_models_pipeline.from_source(
        source=str(PROJECT_ROOT),
        entrypoint="backend/tasks/train_meta_label_models.py:train_meta_label_models_pipeline",
    ).deploy(
        name="train-meta-label-models",
        work_pool_name="my-worker",
        # Run after the OHLC sync window on weekdays; tune in Prefect UI if needed.
        cron="30 9 * * 1-5",
    )
