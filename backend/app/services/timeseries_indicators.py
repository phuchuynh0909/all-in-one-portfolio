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

from .indicators import (
    avwap,
    build_smart_money_flow_kwargs,
    calculate_yz_volatility,
    hawkes_BVC,
    kalman_zscore,
    matrix_series,
    smart_money_flow,
    squeeze_ttm,
    trailing_sl,
    williams_vix_fix_indicator,
)
from .utils import convert_nans


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
                atr = talib.ATR(high_prices, low_prices, close_prices, timeperiod=timeperiod)
                indicator_data["atr_trailing"] = convert_nans(trailing_sl(close_prices, atr))

            elif ind.name == "vwap":
                window = int(ind.params.get("window", 100))
                vol = df["volume"].values
                indicator_data["vwap_highest"] = convert_nans(
                    avwap(close_prices, high_prices, low_prices, vol, is_highest=True, window=window)
                )
                indicator_data["vwap_lowest"] = convert_nans(
                    avwap(close_prices, high_prices, low_prices, vol, is_highest=False, window=window)
                )

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
                # Legacy short names (period/mult/matype) map to BBANDS-style params
                bb_period = int(ind.params.get("bb_period", ind.params.get("period", 10)))
                bb_mult = float(ind.params.get("bb_mult", ind.params.get("mult", 1.2)))
                bb_matype = int(ind.params.get("bb_matype", ind.params.get("matype", 3)))
                kc_period = int(ind.params.get("kc_period", 13))
                kc_mult = float(ind.params.get("kc_mult", 1.0))
                donichan_period = int(ind.params.get("donichan_period", 10))
                osc_smoothing_period = int(ind.params.get("osc_smoothing_period", 10))

                close_arr = df["close"].to_numpy().reshape(-1, 1)
                high_arr = df["high"].to_numpy().reshape(-1, 1)
                low_arr = df["low"].to_numpy().reshape(-1, 1)

                squeeze_diff, ttms = squeeze_ttm(
                    close_arr,
                    high_arr,
                    low_arr,
                    bb_period=bb_period,
                    bb_mult=bb_mult,
                    bb_matype=bb_matype,
                    kc_period=kc_period,
                    kc_mult=kc_mult,
                    donichan_period=donichan_period,
                    osc_smoothing_period=osc_smoothing_period,
                )
                diff_arr = squeeze_diff.to_numpy().reshape(-1)
                ttms_arr = ttms.to_numpy().reshape(-1)
                squeeze_on = np.where(np.isnan(diff_arr), False, diff_arr < 0).tolist()

                indicator_data["squeeze_ttm"] = {
                    "histogram": convert_nans(ttms_arr),
                    "squeeze_on": squeeze_on,
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
