import tempfile
from pathlib import Path
from typing import Dict
from datetime import datetime

import numpy as np
import pandas as pd
from backtesting import Backtest
from loguru import logger

from app.services.stock_service import _load_delta_stocks
from app.services.backtest_strategies import (
    BreakoutDeMarkerStrategyBT,
    BreakoutTTMStrategyBT,
    BreakoutTTMV1StrategyBT,
    BreakoutTTMV1bStrategyBT,
    BreakoutTTMV1cStrategyBT,
    BreakoutTTMV2StrategyBT,
    BreakoutTTMV3StrategyBT,
    EpisodicPivotStrategyBT,
    WilliamsVixStrategyBT,
)


def _get_plot_strategy(strategy_name: str):
    if strategy_name == "Breakout DeMarker":
        return BreakoutDeMarkerStrategyBT, {
            "demarker_period": 10,
            "keltner_period": 16,
            "bb_period": 15,
            "bb_deviation": 2.5,
            "keltner_factor": 2.2,
            "keltner_atr_period": 20,
            "atr_multiplier": 1.9,
            "sl_stop": 0.06,
            "entry_version": "v2",
        }
    elif strategy_name == "Breakout TTM":
        args = {'bb_period': 10, 'bb_multiplier': 1.2, 'kc_period': 13, 'kc_atr_period': 10, 'kc_multiplier': 1.0, 'donichan_period': 10, 'osc_smoothing_period': 5, 'matype': 3, 'william_vix_period': 25}
        return BreakoutTTMStrategyBT, args
    elif strategy_name == "Breakout TTM V1":
        return BreakoutTTMV1StrategyBT, {}
    elif strategy_name == "Breakout TTM V1b":
        return BreakoutTTMV1bStrategyBT, {}
    elif strategy_name == "Breakout TTM V1c":
        return BreakoutTTMV1cStrategyBT, {}
    elif strategy_name == "Breakout TTM V2":
        return BreakoutTTMV2StrategyBT, {}
    elif strategy_name == "Breakout TTM V3":
        return BreakoutTTMV3StrategyBT, {}
    elif strategy_name == "Williams Vix Fix":
        args = {
            'bb_period': 10,
            'bb_multiplier': 1.2,
            'william_vix_period': 20,
            'lb': 50,
            'ph': 0.85,
            'ltLB': 33,
            'mtLB': 14,
            'strength_str': 1,
            'donichan_period': 10,
            'atr_period': 10,
            'atr_multiplier': 1.9,
            'sl_stop': 0.1,
        }
        return WilliamsVixStrategyBT, args
    elif strategy_name == "Episodic Pivot":
        args = {
            'gap_threshold': 0.01,
            'vol_mult': 1.2,
            'vol_period': 10,
            'wait_days': 2,
            'breakout_lookahead': 1,
            'hold_days': 3,
            'atr_period': 10,
            'atr_multiplier': 1.8,
        }
        return EpisodicPivotStrategyBT, args
    return BreakoutDeMarkerStrategyBT, {}


def _compute_mfe_mae(stats, data: pd.DataFrame) -> dict:
    """
    MFE = max favorable excursion (best unrealized profit during trade, % of entry).
    MAE = max adverse excursion  (worst unrealized drawdown during trade, % of entry).
    Uses High/Low bars between EntryBar and ExitBar (inclusive).
    """
    if stats is None or not hasattr(stats, '_trades') or stats._trades.empty:
        return {}

    highs = data['High'].values
    lows  = data['Low'].values
    n     = len(highs)

    mfe_list: list[float] = []
    mae_list: list[float] = []

    for _, t in stats._trades.iterrows():
        entry_bar   = int(t['EntryBar'])
        exit_bar    = min(int(t['ExitBar']), n - 1)
        entry_price = float(t['EntryPrice'])
        direction   = 1 if float(t['Size']) > 0 else -1

        trade_highs = highs[entry_bar: exit_bar + 1]
        trade_lows  = lows[entry_bar:  exit_bar + 1]
        if len(trade_highs) == 0:
            continue

        if direction == 1:  # long
            mfe = (np.max(trade_highs) - entry_price) / entry_price * 100
            mae = (entry_price - np.min(trade_lows))  / entry_price * 100
        else:               # short
            mfe = (entry_price - np.min(trade_lows))  / entry_price * 100
            mae = (np.max(trade_highs) - entry_price) / entry_price * 100

        mfe_list.append(mfe)
        mae_list.append(mae)

    if not mfe_list:
        return {}

    def _r(v: float) -> float:
        return round(float(v), 4)

    return {
        'MFE Avg [%]':    _r(np.mean(mfe_list)),
        'MAE Avg [%]':    _r(np.mean(mae_list)),
        'MFE Median [%]': _r(np.median(mfe_list)),
        'MAE Median [%]': _r(np.median(mae_list)),
        'MFE Max [%]':    _r(np.max(mfe_list)),
        'MAE Max [%]':    _r(np.max(mae_list)),
        'MFE P75 [%]':    _r(np.percentile(mfe_list, 75)),
    }


async def run_backtest_plot(symbol: str, start_date: str, strategy_name: str) -> Dict:
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    stock_df = _load_delta_stocks(
        symbols=[symbol],
        start=start_dt,
        columns=["date", "open", "high", "low", "close", "volume", "symbol"],
    )

    if stock_df.empty:
        raise ValueError(f"No data found for symbol {symbol} from {start_date}")

    stock_df = stock_df[stock_df["symbol"] == symbol].drop(columns=["symbol"]).copy()
    stock_df = stock_df.sort_values("date")
    stock_df = stock_df.rename(columns={
        "open": "Open", "high": "High",
        "low": "Low", "close": "Close", "volume": "Volume",
    })
    stock_df = stock_df.set_index("date")
    stock_df = stock_df.dropna(subset=["Open", "High", "Low", "Close"])

    strategy_class, strategy_params = _get_plot_strategy(strategy_name)
    bt = Backtest(stock_df, strategy_class, cash=10000, commission=0.002,
                  exclusive_orders=True, finalize_trades=True)
    try:
        stats = bt.run(**strategy_params)
    except Exception as exc:
        logger.exception(
            "Backtest run failed",
            extra={"symbol": symbol, "strategy": strategy_name, "start_date": start_date},
        )
        raise RuntimeError(f"{exc.__class__.__name__}: {exc}") from exc

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as temp_file:
        html_path = Path(temp_file.name)

    try:
        bt.plot(filename=str(html_path), plot_volume=True, plot_equity=False, open_browser=False)
        html = html_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.exception(
            "Backtest plot failed",
            extra={"symbol": symbol, "strategy": strategy_name, "start_date": start_date},
        )
        raise RuntimeError(f"{exc.__class__.__name__}: {exc}") from exc
    finally:
        if html_path.exists():
            html_path.unlink()

    stats_dict = {}
    if stats is not None:
        raw_stats = stats.filter(regex="^[^_]").to_dict()

        def _normalize_value(value):
            if isinstance(value, (np.integer, np.floating)):
                return value.item()
            if isinstance(value, np.bool_):
                return bool(value)
            if isinstance(value, (pd.Timestamp, datetime)):
                return value.isoformat()
            if isinstance(value, pd.Timedelta):
                return str(value)
            return value

        stats_dict = {str(k): _normalize_value(v) for k, v in raw_stats.items()}
        stats_dict.update(_compute_mfe_mae(stats, stock_df))

    return {
        "symbol": symbol,
        "start_date": start_date,
        "strategy": strategy_name,
        "html": html,
        "stats": stats_dict,
    }
