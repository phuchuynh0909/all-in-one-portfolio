import pandas as pd
import numpy as np
import json
import time
import tempfile
from pathlib import Path
import pandas as pd
import talib
from ta.volatility import KeltnerChannel
from sklearn.preprocessing import StandardScaler
from typing import List, Dict, Tuple
from datetime import datetime
from loguru import logger
from app.services.stock_service import _load_delta_stocks, _load_feature_store
from app.core.settings import settings
from app.services.indicators import avwap, trailing_sl
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
from backtesting.test import SMA
from app.services.backtest_strategies import BreakoutDeMarkerStrategyBT, BreakoutTTMStrategyBT, WilliamsVixStrategyBT

# List of features used for ML predictions
FEATURES_LIST = [
    'rsi_window_5', 'rsi_window_14', 'obv', 'mfi_21', 'log_return',
    'volume_threshold_ma_10', 'volume_threshold_ma_20',
    'ema_10_distance', 'ema_20_distance', 'ema_50_distance', 'ema_200_distance',
    'vwap_distance_highest', 'vwap_distance_lowest',
    'efi_zscore_10', 'efi_zscore_20',
    'mrs_10', 'mrs_20', 'rs_10', 'rs_20',
    'msr_rank_10', 'msr_rank_20',
    'zscore_10_log_return', 'zscore_20_log_return',
    'yz_vol_10', 'yz_vol_20', 'dc_tmv',
    'kf_distance', 'zscore_kf_10', 'zscore_kf_20'
]

def get_strategy_params(strategy_name: str) -> Tuple[List[tuple], type, List[str]]:
    """Get strategy parameters based on strategy name."""
    if strategy_name == "Squeeze Breakout":
        from app.services.strategies.squeeze_breakout import SqueezeBreakoutStrategy
        strategy_params = [
            (10, 1.0, 34, 1.3),
            (10, 1.3, 30, 1.2),
            (14, 1.1, 12, 2.0),
        ]
        param_names = ['bb_window', 'bb_multiplier', 'kc_window', 'kc_multiplier']
        return strategy_params, SqueezeBreakoutStrategy, param_names
    
    elif strategy_name == "Breakout TTM Version 2":
        from app.services.strategies.breakout_ttm import BreakoutTTMVersion2
        strategy_params = [
            (14, 1.4, 40, 1.2, 12, 12, 12, 'v2'),
            (16, 1.0, 40, 1.2, 14, 12, 12, 'v1'),
            (10, 1.2, 13, 1.0, 10, 12, 10, 'v3', 10, 10, 3, 20, 7),
        ]

        param_names = ['bb_window', 'bb_multiplier', 'kc_window', 'kc_multiplier', 
                    'atr_window', 'momentum_window', 'donichan_window', 'entry_version',
                    'kc_atr_period', 'osc_smoothing_period', 'matype', 'william_vix_period', 'consecutive_neg_threshold']
        return strategy_params, BreakoutTTMVersion2, param_names
    elif strategy_name == "Dual RSI":
        from app.services.strategies.dual_rsi import DualRSI
        strategy_params = [
            (14, 5, 200, 0.05),
        ]
        param_names = ['rsi_window_high', 'rsi_window_low', 'vwap_window', 'sl_stop']
        return strategy_params, DualRSI, param_names
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")

def build_features(total_trades: pd.DataFrame) -> pd.DataFrame:
    """Build features for ML predictions."""
    # Get unique combinations of date and symbol from trades
    unique_dates = total_trades['date'].unique()
    unique_symbols = total_trades['symbol'].unique()

    # Get feature data from Delta Lake
    feature_store = _load_feature_store(
        symbols=list(unique_symbols),
        start=min(unique_dates),
        end=max(unique_dates),
    )

    # Calculate additional features using safe log (avoid divide by zero)
    eps = 1e-10  # Small epsilon to prevent division by zero and log(0)
    
    def safe_log_ratio(numerator, denominator):
        """Calculate log(1 + (num - denom) / denom) safely, handling zeros."""
        ratio = (numerator - denominator) / denominator.replace(0, np.nan)
        # Clip to avoid log of values <= -1
        clipped = np.clip(ratio, -1 + eps, None)
        return np.log1p(clipped)
    
    feature_store['kf_distance'] = safe_log_ratio(feature_store['close'], feature_store['kf'])
    feature_store['vwap_distance_lowest'] = safe_log_ratio(feature_store['close'], feature_store['vwap_lowest'])
    feature_store['vwap_distance_highest'] = safe_log_ratio(feature_store['close'], feature_store['vwap_highest'])
    feature_store['volume_threshold_ma_10'] = safe_log_ratio(feature_store['volume'], feature_store['volume_ma_10'])
    feature_store['volume_threshold_ma_20'] = safe_log_ratio(feature_store['volume'], feature_store['volume_ma_20'])
    feature_store['ema_10_distance'] = safe_log_ratio(feature_store['close'], feature_store['ema_10'])
    feature_store['ema_20_distance'] = safe_log_ratio(feature_store['close'], feature_store['ema_20'])
    feature_store['ema_50_distance'] = safe_log_ratio(feature_store['close'], feature_store['ema_50'])
    feature_store['ema_200_distance'] = safe_log_ratio(feature_store['close'], feature_store['ema_200'])

    # Merge with trades
    training_feature_df = pd.merge(total_trades, feature_store, on=['date', 'symbol'], how='inner')
    training_feature_df['Y'] = training_feature_df['return'] > 0

    # Handle missing values
    training_feature_df.dropna(inplace=True)
    return training_feature_df

def predict_features(feature_df: pd.DataFrame) -> pd.DataFrame:
    """Make predictions using ML models."""
    from app.services.ml_models import get_models
    
    # Get pre-loaded models
    xgb_model, lgb_model, catboost_model = get_models()

    # Prepare features
    X = feature_df[FEATURES_LIST]
    scaler = StandardScaler()
    X_predict = scaler.fit_transform(X)

    # Make predictions
    feature_df['y_pred_xgb'] = xgb_model.predict_proba(X_predict)[:, 1]
    feature_df['y_pred_lgbm'] = lgb_model.predict(X_predict)
    feature_df['y_pred_catboost'] = catboost_model.predict_proba(X_predict)[:, 1]

    return feature_df

async def run_backtest(strategy_name: str, start_date: str, symbols: List[str] | None = None, apply_ml: bool = True) -> Dict:
    """Run backtest for given strategy and parameters."""
    total_start_time = time.time()
    
    # Load stock data
    logger.info(f"Starting backtest for {strategy_name} from {start_date}")
    data_load_start = time.time()
    stocks = _load_delta_stocks(
        symbols=symbols,
        columns=["date", "symbol", "open", "high", "low", "close", "volume"],
        start=datetime.strptime(start_date, "%Y-%m-%d"),
    )
    stocks = stocks.set_index(["date", "symbol"]).sort_index()
    stocks = stocks.unstack(level=1).bfill().ffill()
    data_loading_duration = time.time() - data_load_start
    logger.info(f"Data loading took {data_loading_duration:.2f} seconds")
    
    # Get strategy configuration
    strategy_start_time = time.time()
    strategy_params, strategy_class, param_names = get_strategy_params(strategy_name)

    total_trades = pd.DataFrame()
    total_open_trades = pd.DataFrame()

    # Run strategy with different parameters
    for params in strategy_params:
        param_dict = dict(zip(param_names, params))
        if 'entry_version' in param_dict:
            entry_version = param_dict.pop('entry_version')
            param_dict = {'entry_version': entry_version, **param_dict}
            
        strategy = strategy_class(stocks, **param_dict)
        portfolio = strategy.get_portfolio()
        
        trades = pd.DataFrame(portfolio.trades.records)
        trades['metadata'] = json.dumps(param_dict)
        open_trade = pd.DataFrame(portfolio.trades.open.records)
        open_trade['metadata'] = json.dumps(param_dict)

        total_trades = pd.concat([total_trades, trades])
        total_open_trades = pd.concat([total_open_trades, open_trade])

    # Mark trade types
    total_trades['type'] = 'closed_trades'
    total_open_trades['type'] = 'open_trades'

    # Filter out duplicate trades
    open_trade_keys = pd.MultiIndex.from_frame(total_open_trades[['col', 'entry_idx']])
    total_trade_keys = pd.MultiIndex.from_frame(total_trades[['col', 'entry_idx']])
    mask = ~total_trade_keys.isin(open_trade_keys)
    filtered_total_trades = total_trades[mask]

    # Combine all trades
    all_trades_df = pd.concat([filtered_total_trades, total_open_trades])
    all_trades_df = all_trades_df.drop_duplicates(subset=['col', 'entry_idx'], keep='first').reset_index(drop=True)
    
    # Add symbol and date information
    all_trades_df['symbol'] = all_trades_df.apply(lambda x: stocks.close.columns[x['col']], axis=1)
    all_trades_df['date'] = all_trades_df.apply(lambda x: stocks.index[x['entry_idx']], axis=1)
    
    # Keep a copy of original trades before feature building
    original_trades_df = all_trades_df.copy()
    
    strategy_duration = time.time() - strategy_start_time
    logger.info(f"Strategy execution took {strategy_duration:.2f} seconds")
    
    # Build features and make predictions (conditional on apply_ml)
    feature_start_time = time.time()
    feature_building_duration = 0
    prediction_duration = 0
    
    if apply_ml:
        feature_df = build_features(all_trades_df)
        feature_building_duration = time.time() - feature_start_time
        logger.info(f"Feature building took {feature_building_duration:.2f} seconds")
        
        prediction_start_time = time.time()
        feature_df = predict_features(feature_df)
        prediction_duration = time.time() - prediction_start_time
        logger.info(f"ML predictions took {prediction_duration:.2f} seconds")

        # Merge original trades with feature/prediction results
        merge_cols = ['col', 'entry_idx', 'type']
        
        # Merge predictions back to original trades (left join to keep all original trades)
        prediction_cols = ['y_pred_xgb', 'y_pred_lgbm', 'y_pred_catboost'] + \
                         [col for col in feature_df.columns if col.startswith('msr_rank')]
        
        complete_trades_df = pd.merge(
            original_trades_df, 
            feature_df[merge_cols + prediction_cols],
            on=merge_cols, 
            how='left'
        )
    else:
        logger.info("Skipping ML predictions (apply_ml=False)")
        complete_trades_df = original_trades_df.copy()
        # Add empty prediction columns
        complete_trades_df['y_pred_xgb'] = None
        complete_trades_df['y_pred_lgbm'] = None
        complete_trades_df['y_pred_catboost'] = None
        complete_trades_df['msr_rank_10'] = None
    
    # Prepare response with proper copies
    formatting_start_time = time.time()
    
    open_trades_df = complete_trades_df[complete_trades_df['type'] == 'open_trades'].copy()
    closed_trades_df = complete_trades_df[complete_trades_df['type'] == 'closed_trades'].copy()

    # Format open trades
    # Parse JSON metadata before converting to records
    def _parse_metadata(value):
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return {}
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return {}
        return {}

    if 'metadata' in open_trades_df.columns:
        open_trades_df['metadata'] = open_trades_df['metadata'].astype('object')
        open_trades_df.loc[:, 'metadata'] = open_trades_df['metadata'].apply(_parse_metadata)

    # Handle NaN and infinity values before JSON serialization
    numeric_cols = ['entry_price', 'pnl', 'y_pred_xgb', 'y_pred_lgbm', 'y_pred_catboost', 'msr_rank_10']
    for col in numeric_cols:
        if col in open_trades_df.columns:
            # Replace NaN and infinity with None for JSON compliance
            open_trades_df[col] = open_trades_df[col].replace([np.nan, np.inf, -np.inf], None)

    # Convert to records - select only available columns
    open_trades_columns = ['symbol', 'date', 'entry_price', 'pnl', 'y_pred_xgb', 'y_pred_lgbm', 
                          'y_pred_catboost', 'msr_rank_10', 'metadata', 'type', 'entry_idx']
    available_open_cols = [col for col in open_trades_columns if col in open_trades_df.columns]
    
    open_trades = open_trades_df[available_open_cols].to_dict('records')

    # Format closed trades
    if 'metadata' in closed_trades_df.columns:
        closed_trades_df['metadata'] = closed_trades_df['metadata'].astype('object')
        closed_trades_df.loc[:, 'metadata'] = closed_trades_df['metadata'].apply(_parse_metadata)
    closed_trades_df.loc[:, 'trading_days'] = closed_trades_df['exit_idx'] - closed_trades_df['entry_idx']
    closed_trades_df.loc[:, 'close_date'] = closed_trades_df.apply(lambda x: stocks.index[x['exit_idx']], axis=1)
    
    # Handle NaN and infinity values before JSON serialization
    numeric_cols = ['entry_price', 'pnl', 'trading_days', 'y_pred_xgb', 'y_pred_lgbm', 'y_pred_catboost', 'msr_rank_10']
    for col in numeric_cols:
        if col in closed_trades_df.columns:
            # Replace NaN and infinity with None for JSON compliance
            closed_trades_df[col] = closed_trades_df[col].replace([np.nan, np.inf, -np.inf], None)
    
    # Convert to records - select only available columns 
    closed_trades_columns = ['symbol', 'date', 'close_date', 'entry_price', 'pnl', 'trading_days',
                             'y_pred_xgb', 'y_pred_lgbm', 'y_pred_catboost', 'msr_rank_10', 'metadata',
                             'type', 'entry_idx', 'exit_idx']
    available_closed_cols = [col for col in closed_trades_columns if col in closed_trades_df.columns]
    
    closed_trades = closed_trades_df[available_closed_cols].to_dict('records')

    formatting_duration = time.time() - formatting_start_time
    logger.info(f"Trade formatting took {formatting_duration:.2f} seconds")

    total_time = time.time() - total_start_time
    logger.info(f"Total backtest execution took {total_time:.2f} seconds")
    
    return {
        'open_trades': open_trades,
        'closed_trades': closed_trades,
        'execution_time': {
            'total_seconds': round(total_time, 2),
            'data_loading_seconds': round(data_loading_duration, 2),
            'strategy_seconds': round(strategy_duration, 2),
            'feature_building_seconds': round(feature_building_duration, 2),
            'prediction_seconds': round(prediction_duration, 2),
            'formatting_seconds': round(formatting_duration, 2)
        }
    }


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
        # args = {'bb_period': 15, 'bb_multiplier': 2.5, 'kc_period': 15, 'kc_atr_period': 10, 'kc_multiplier': 2.5, 'donichan_period': 20}
        # args = {'bb_period': 10, 'bb_multiplier': 1.8, 'kc_period': 14, 'kc_atr_period': 10, 'kc_multiplier': 1.1, 'donichan_period': 10, 'osc_smoothing_period': 10}
        args = {'bb_period': 10, 'bb_multiplier': 1.2, 'kc_period': 13, 'kc_atr_period': 10, 'kc_multiplier': 1.0, 'donichan_period': 10, 'osc_smoothing_period': 5, 'matype': 3, 'william_vix_period': 25}
        return BreakoutTTMStrategyBT, args
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
    return BreakoutDeMarkerStrategyBT, {}


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
    stock_df = stock_df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    stock_df = stock_df.set_index("date")
    stock_df = stock_df.dropna(subset=["Open", "High", "Low", "Close"])

    strategy_class, strategy_params = _get_plot_strategy(strategy_name)
    bt = Backtest(stock_df, strategy_class, cash=10000, commission=0.002, exclusive_orders=True, finalize_trades=True)
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
        bt.plot(filename=str(html_path), plot_volume=False, plot_equity=False, open_browser=False)
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

    return {
        "symbol": symbol,
        "start_date": start_date,
        "strategy": strategy_name,
        "html": html,
        "stats": stats_dict,
    }
