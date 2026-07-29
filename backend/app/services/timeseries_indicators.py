"""Stock OHLCV indicator calculations shared by timeseries and indicators-only API routes."""

from __future__ import annotations

import traceback
from typing import Any

import numpy as np
import pandas as pd
import talib
import vectorbt as vbt
from loguru import logger

from app.schemas.timeseries import IndicatorParams
from app.services.strategies.breakout_ttm_v1 import FIXED_TTM_PARAMS

from .indicators import (
    avwap,
    build_smart_money_flow_kwargs,
    calculate_yz_volatility,
    calculate_gkyz_volatility,
    chandelier_exit,
    gaussian_frama,
    hawkes_BVC,
    hull_butterfly,
    kalman_zscore,
    linreg_channel_2d,
    matrix_series,
    student_t_crit,
    smart_money_flow,
    squeeze_ttm,
    trailing_sl,
    williams_vix_fix_indicator,
)
from .utils import convert_nans


def _resolve_squeeze_ttm_params(params: dict) -> dict:
    """Map API params to squeeze_ttm kwargs; defaults from ``ttm_best_params_005.json``."""
    entry_version = str(params.get("entry_version", "v2"))
    if entry_version not in FIXED_TTM_PARAMS:
        entry_version = "v2"
    d = FIXED_TTM_PARAMS[entry_version]

    def _pick(*keys: str, default_key: str):
        for key in keys:
            if key in params:
                return params[key]
        return d[default_key]

    return {
        "bb_period": int(_pick("bb_period", "bb_window", "period", default_key="bb_window")),
        "bb_mult": float(_pick("bb_mult", "bb_multiplier", "mult", default_key="bb_multiplier")),
        "bb_matype": int(_pick("bb_matype", "matype", default_key="bb_matype")),
        "kc_period": int(_pick("kc_period", "kc_window", default_key="kc_window")),
        "kc_mult": float(_pick("kc_mult", "kc_multiplier", default_key="kc_multiplier")),
        "kc_atr_period": int(_pick("kc_atr_period", default_key="kc_atr_period")),
        "donichan_period": int(_pick("donichan_period", "donichan_window", default_key="donichan_window")),
        "osc_smoothing_period": int(_pick("osc_smoothing_period", default_key="osc_smoothing_period")),
    }


def compute_stock_indicators(df: pd.DataFrame, indicators: list[IndicatorParams]) -> dict[str, Any]:
    """
    Build a flat dict of indicator series keyed for ``Indicators`` / API payloads.
    Unknown indicator names are skipped with a debug log.
    """
    if not indicators:
        return {}

    close_prices = df["close"].values
    high_prices = df["high"].values
    low_prices = df["low"].values
    volume_prices = df["volume"].values

    indicator_data: dict[str, Any] = {}

    for ind in indicators:
        try:
            if ind.name == "rsi":
                timeperiod = int(ind.params.get("timeperiod", 14))
                indicator_data["rsi"] = convert_nans(talib.RSI(close_prices, timeperiod=timeperiod))
                indicator_data["rsi_5"] = convert_nans(talib.RSI(close_prices, timeperiod=5))

            elif ind.name == "macd":
                fastperiod = int(ind.params.get("fastperiod", 12))
                slowperiod = int(ind.params.get("slowperiod", 26))
                signalperiod = int(ind.params.get("signalperiod", 9))
                macd_line, signal_line, histogram = talib.MACD(
                    close_prices,
                    fastperiod=fastperiod,
                    slowperiod=slowperiod,
                    signalperiod=signalperiod,
                )
                indicator_data["macd"] = {
                    "macd": convert_nans(macd_line),
                    "signal": convert_nans(signal_line),
                    "histogram": convert_nans(histogram),
                }

            elif ind.name == "bbands":
                timeperiod = int(ind.params.get("timeperiod", 20))
                nbdevup = float(ind.params.get("nbdevup", 2))
                nbdevdn = float(ind.params.get("nbdevdn", 2))
                upper, middle, lower = talib.BBANDS(
                    close_prices,
                    timeperiod=timeperiod,
                    nbdevup=nbdevup,
                    nbdevdn=nbdevdn,
                )
                indicator_data["bbands"] = {
                    "upper": convert_nans(upper),
                    "middle": convert_nans(middle),
                    "lower": convert_nans(lower),
                }

            elif ind.name == "sma":
                timeperiod = int(ind.params.get("timeperiod", 20))
                indicator_data["sma"] = convert_nans(talib.SMA(close_prices, timeperiod=timeperiod))

            elif ind.name == "ema":
                timeperiod = int(ind.params.get("timeperiod", 20))
                indicator_data["ema"] = convert_nans(talib.EMA(close_prices, timeperiod=timeperiod))

            elif ind.name == "atr_trailing":
                timeperiod = int(ind.params.get("timeperiod", 10))
                multiplier = float(ind.params.get("multiplier", 1.8))
                atr = talib.ATR(high_prices, low_prices, close_prices, timeperiod=timeperiod)
                indicator_data["atr_trailing"] = convert_nans(trailing_sl(close_prices, atr, atr_multiplier=multiplier))

            elif ind.name == "vwap":
                window = int(ind.params.get("window", 100))
                vol = df["volume"].values
                indicator_data["vwap_highest"] = convert_nans(
                    avwap(close_prices, high_prices, low_prices, vol, is_highest=True, window=window)
                )
                indicator_data["vwap_lowest"] = convert_nans(
                    avwap(close_prices, high_prices, low_prices, vol, is_highest=False, window=window)
                )

            elif ind.name == "kama":
                timeperiod = int(ind.params.get("timeperiod", 10))
                indicator_data["kama"] = convert_nans(talib.KAMA(close_prices, timeperiod=timeperiod))

            elif ind.name == "bvc":
                window = int(ind.params.get("window", 20))
                kappa = float(ind.params.get("kappa", 0.1))
                bvc_values = hawkes_BVC(close_prices, volume_prices, window=window, kappa=kappa)
                indicator_data["bvc"] = convert_nans(bvc_values)

            elif ind.name == "stoch":
                fastk_period = int(ind.params.get("fastk_period", 14))
                slowk_period = int(ind.params.get("slowk_period", 3))
                slowd_period = int(ind.params.get("slowd_period", 3))
                slowk, slowd = talib.STOCH(
                    high_prices,
                    low_prices,
                    close_prices,
                    fastk_period=fastk_period,
                    slowk_period=slowk_period,
                    slowd_period=slowd_period,
                )
                indicator_data["stoch"] = {
                    "slowk": convert_nans(slowk),
                    "slowd": convert_nans(slowd),
                }

            elif ind.name == "kalman_zscore":
                window = int(ind.params.get("window", 20))
                indicator_data["kalman_zscore"] = kalman_zscore.calculate_kalman_zscore(
                    close_prices, window=window
                )

            elif ind.name == "yz_volatility":
                window = int(ind.params.get("window", 30))
                periods = int(ind.params.get("periods", 252))
                indicator_data["yz_volatility"] = calculate_yz_volatility(
                    df["open"].values,
                    df["high"].values,
                    df["low"].values,
                    df["close"].values,
                    window=window,
                    periods=periods,
                )

            elif ind.name == "gkyz_volatility":
                window = int(ind.params.get("window", 21))
                indicator_data["gkyz_volatility"] = calculate_gkyz_volatility(
                    df["open"].values,
                    df["high"].values,
                    df["low"].values,
                    df["close"].values,
                    window=window,
                    normalize=True,
                ).tolist()

            elif ind.name == "matrix_series":
                price_period = int(ind.params.get("price_period", 16))
                sup_res_period = int(ind.params.get("sup_res_period", 30))
                sup_res_percentage = int(ind.params.get("sup_res_percentage", 100))
                smoother = int(ind.params.get("smoother", 5))

                close_arr = df["close"].to_numpy().reshape(-1, 1)
                high_arr = df["high"].to_numpy().reshape(-1, 1)
                low_arr = df["low"].to_numpy().reshape(-1, 1)

                ms = vbt.IndicatorFactory(
                    class_name="MatrixSeries",
                    short_name="matrix_series",
                    input_names=["close", "high", "low"],
                    param_names=["price_period", "sup_res_period", "sup_res_percentage", "smoother"],
                    output_names=[
                        "hh",
                        "ll",
                        "support_line",
                        "resistance_line",
                        "up_line",
                        "down_line",
                    ],
                ).from_apply_func(matrix_series)

                ms = ms.run(
                    close_arr,
                    high_arr,
                    low_arr,
                    price_period=price_period,
                    sup_res_period=sup_res_period,
                    sup_res_percentage=sup_res_percentage,
                    smoother=smoother,
                )
                indicator_data["matrix_series"] = {
                    "hh": convert_nans(ms.hh.to_numpy().reshape(-1)),
                    "ll": convert_nans(ms.ll.to_numpy().reshape(-1)),
                    "support_line": convert_nans(ms.support_line.to_numpy().reshape(-1)),
                    "resistance_line": convert_nans(ms.resistance_line.to_numpy().reshape(-1)),
                    "up_line": convert_nans(ms.up_line.to_numpy().reshape(-1)),
                    "down_line": convert_nans(ms.down_line.to_numpy().reshape(-1)),
                }

            elif ind.name == "squeeze_ttm":
                stt_params = _resolve_squeeze_ttm_params(ind.params)

                close_arr = df["close"].to_numpy().reshape(-1, 1)
                high_arr = df["high"].to_numpy().reshape(-1, 1)
                low_arr = df["low"].to_numpy().reshape(-1, 1)

                _, ttms, squeeze_state_arr = squeeze_ttm(
                    close_arr,
                    high_arr,
                    low_arr,
                    **stt_params,
                )
                ttms_arr = ttms.to_numpy().reshape(-1)
                squeeze_state = squeeze_state_arr.reshape(-1).astype(int).tolist()

                indicator_data["squeeze_ttm"] = {
                    "histogram": convert_nans(ttms_arr),
                    "squeeze_state": squeeze_state,
                }

            elif ind.name == "smart_money_flow":
                smf_result = smart_money_flow(
                    open_=df["open"].values,
                    high=df["high"].values,
                    low=df["low"].values,
                    close=df["close"].values,
                    volume=df["volume"].values,
                    **build_smart_money_flow_kwargs(ind.params),
                )
                indicator_data["smart_money_flow"] = {
                    "last_signal": [int(v) for v in smf_result["last_signal"].tolist()],
                    "switch_up": smf_result["switch_up"].tolist(),
                    "switch_down": smf_result["switch_down"].tolist(),
                    "upper": convert_nans(smf_result["upper"]),
                    "lower": convert_nans(smf_result["lower"]),
                    "b_close": convert_nans(smf_result["b_close"]),
                    "b_open": convert_nans(smf_result["b_open"]),
                    "mf_smooth": convert_nans(smf_result["mf_smooth"]),
                    "strength": convert_nans(smf_result["strength"]),
                    "bull_dot": smf_result["bull_dot"].tolist(),
                    "bear_dot": smf_result["bear_dot"].tolist(),
                    "strength_signed": convert_nans(smf_result["strength_signed"]),
                }

            elif ind.name == "chandelier_exit":
                length = int(ind.params.get("length", 31))
                multiplier = float(ind.params.get("multiplier", 2.2))
                ce = chandelier_exit(close_prices, high_prices, low_prices, length=length, multiplier=multiplier)
                dir_int = np.where(np.isnan(ce["direction"]), None, ce["direction"].astype(int))
                indicator_data["chandelier_exit"] = {
                    "value":     convert_nans(ce["value"]),
                    "direction": [int(v) if v is not None else None for v in dir_int],
                    "long":      convert_nans(ce["long"]),
                    "short":     convert_nans(ce["short"]),
                }

            elif ind.name == "linreg_channel":
                reg_window = int(ind.params.get("reg_window", 50))
                confidence = float(ind.params.get("confidence", 0.9))
                t_crit = student_t_crit(reg_window, confidence)
                close_arr = df["close"].to_numpy(dtype=np.float64).reshape(-1, 1)
                reg, _slope, ci_u, ci_l, pi_u, pi_l = linreg_channel_2d(
                    close_arr, reg_window, float(t_crit)
                )
                indicator_data["linreg_channel"] = {
                    "reg":      convert_nans(reg.reshape(-1)),
                    "pi_upper": convert_nans(pi_u.reshape(-1)),
                    "pi_lower": convert_nans(pi_l.reshape(-1)),
                    "ci_upper": convert_nans(ci_u.reshape(-1)),
                    "ci_lower": convert_nans(ci_l.reshape(-1)),
                }

            elif ind.name == "gaussian_frama":
                gframa = gaussian_frama(
                    close_prices,
                    high_prices,
                    low_prices,
                    gaussian_length=int(ind.params.get("gaussian_length", 4)),
                    sigma=float(ind.params.get("sigma", 2.0)),
                    fm_len=int(ind.params.get("fm_len", 20)),
                    upper_limit=int(ind.params.get("upper_limit", 8)),
                    lower_limit=int(ind.params.get("lower_limit", 40)),
                    atr_period=int(ind.params.get("atr_period", 14)),
                    atr_mult=float(ind.params.get("atr_mult", 1.9)),
                )
                indicator_data["gaussian_frama"] = {
                    "frama":   convert_nans(gframa["frama"].reshape(-1)),
                    "long_v":  convert_nans(gframa["long_v"].reshape(-1)),
                    "short_v": convert_nans(gframa["short_v"].reshape(-1)),
                    "qb":      convert_nans(gframa["qb"].reshape(-1)),
                }

            elif ind.name == "hull_butterfly":
                hso, os_state = hull_butterfly(
                    close_prices,
                    length=int(ind.params.get("length", 14)),
                    mult=float(ind.params.get("mult", 2.0)),
                )
                indicator_data["hull_butterfly"] = {
                    "hso": convert_nans(hso.reshape(-1)),
                    "os":  convert_nans(os_state.reshape(-1)),
                }

            elif ind.name == "williams_vix_fix":
                close_arr = df["close"].to_numpy().reshape(-1, 1)
                high_arr = df["high"].to_numpy().reshape(-1, 1)
                low_arr = df["low"].to_numpy().reshape(-1, 1)
                wvf, range_high, filtered, cond_fe = williams_vix_fix_indicator(
                    close_arr,
                    high_arr,
                    low_arr,
                    period=int(ind.params.get("period", 20)),
                    mult=float(ind.params.get("mult", 1.2)),
                    bbl=int(ind.params.get("bbl", 10)),
                    lb=20,
                    ph=0.85,
                    ltLB=33,
                    mtLB=14,
                    strength_str=1,
                )

                filtered_list = filtered.reshape(-1)
                cond_fe_list = cond_fe.reshape(-1)
                indicator_data["williams_vix_fix"] = {
                    "wvf": convert_nans(wvf.to_numpy().reshape(-1)),
                    "range_high": convert_nans(range_high.to_numpy().reshape(-1)),
                    "filtered": convert_nans(filtered_list),
                    "cond_fe": convert_nans(cond_fe_list),
                }

            else:
                logger.debug("Unknown indicator requested: {}", ind.name)

        except Exception as e:
            logger.warning("Error calculating indicator {}: {}", ind.name, e)
            logger.debug(traceback.format_exc())

    return indicator_data
