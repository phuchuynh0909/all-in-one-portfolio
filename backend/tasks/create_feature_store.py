"""
This script implements a Prefect workflow for processing stock market data
and creating features for machine learning models.

Steps:
1. Load stock data 
2. Run indicators for building features
3. Sync features data to delta-lake
"""

import os
import sys
from prefect import flow, task
from pathlib import Path
import vectorbt as vbt
from pykalman import KalmanFilter
import pandas as pd
from deltalake import DeltaTable, write_deltalake
from dotenv import load_dotenv
load_dotenv()

# Set up the Python path first
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tasks.common import get_storage_options
from app.services.indicators import zscore_nb, avwap_func_nb, relative_strength_nb, yang_zhang_volatility_nb, directional_change_nb


class IndicatorConfig:
    """Configuration class for all indicator parameters"""
    
    # Window configurations
    EMA_WINDOWS = [10, 20, 50, 200]
    RSI_WINDOWS = [5, 14]
    ATR_WINDOWS = [10, 14, 252]
    ZSCORE_LOGRETURN_WINDOWS = [10, 20]
    EFI_WINDOWS = [10, 20, 50, 200]
    MFI_WINDOWS = [10, 21]
    RS_WINDOWS = [10, 20, 50, 252]  # 252 for 12-month RS Rating
    ZSCORE_WINDOWS = [20, 50, 200]
    VOLUME_MA_WINDOWS = [10, 20, 50, 200]
    YZ_VOLATILITY_WINDOWS = [10, 20]
    ZSCORE_KF_WINDOWS = [10, 20]
    
    # Other parameters
    VWAP_WINDOW = 200
    DC_THETA = 0.01
    YZ_PERIODS = 252
    
    # Kalman Filter parameters
    KF_TRANSITION_MATRICES = [1]
    KF_OBSERVATION_MATRICES = [1]
    KF_INITIAL_STATE_MEAN = 0
    KF_INITIAL_STATE_COVARIANCE = 1
    KF_OBSERVATION_COVARIANCE = 1
    KF_TRANSITION_COVARIANCE = 0.01


def extract_windowed_data(indicator_result, windows, level_name):
    """Helper function to extract data for different windows and clean column names"""
    result = {}
    for window in windows:
        data = indicator_result.xs(window, level=level_name, axis=1)
        data.columns = data.columns.get_level_values(-1)
        result[window] = data
    return result


def create_zscore_indicator():
    """Create and return a zscore indicator factory"""
    return vbt.IndicatorFactory(
        class_name='ZScoreIndicator',
        short_name='zscore_indicator',
        input_names=['data'],
        param_names=['window'],
        output_names=['zscore_result']
    ).from_apply_func(zscore_nb)


def calculate_rs_rating_percentile(rs_data):
    """
    Calculate RS Rating percentile based on relative strength data.
    
    Formula: Percentile = (Number of Stocks Outperformed / Total Number of Stocks) * 100
    RS Rating ranges from 1 to 99, with 99 being the strongest.
    
    Args:
        rs_data (pd.DataFrame): DataFrame with relative strength values for each stock and date
        
    Returns:
        pd.DataFrame: DataFrame with RS Rating percentiles (1-99 scale)
    """
    # For each date (row), calculate percentile rank of each stock
    rs_rating = rs_data.rank(axis=1, method='min', ascending=True, pct=True) * 100
    
    # Clip to 1-99 range and round to integers
    rs_rating = rs_rating.clip(lower=1, upper=99).round()

    # Convert to nullable integer type
    rs_rating = rs_rating.astype('Int64')
    
    return rs_rating


def calculate_moving_averages(stocks: pd.DataFrame, config: IndicatorConfig):
    """Calculate EMA and Volume MA indicators"""
    results = {}
    
    # Calculate EMA
    ema = vbt.MA.run(stocks.close, window=config.EMA_WINDOWS, ewm=True).ma
    results['ema'] = extract_windowed_data(ema, config.EMA_WINDOWS, 'ma_window')
    
    # Calculate Volume MA
    volume_ma = vbt.MA.run(stocks.volume, window=config.VOLUME_MA_WINDOWS, ewm=False).ma
    results['volume_ma'] = extract_windowed_data(volume_ma, config.VOLUME_MA_WINDOWS, 'ma_window')
    
    return results


def calculate_momentum_indicators(stocks: pd.DataFrame, config: IndicatorConfig):
    """Calculate RSI, ATR, and MFI indicators"""
    results = {}
    
    # Calculate RSI
    rsi_indicator = vbt.IndicatorFactory.from_ta("RSIIndicator")
    rsi = rsi_indicator.run(stocks.close, window=config.RSI_WINDOWS).rsi
    results['rsi'] = extract_windowed_data(rsi, config.RSI_WINDOWS, 'rsiindicator_window')
    
    # Calculate ATR
    atr_indicator = vbt.IndicatorFactory.from_ta("AverageTrueRange")
    atr = atr_indicator.run(stocks.high, stocks.low, stocks.close, window=config.ATR_WINDOWS).average_true_range
    results['atr'] = extract_windowed_data(atr, config.ATR_WINDOWS, 'averagetruerange_window')
    
    # Calculate MFI
    mfi = vbt.IndicatorFactory.from_ta("MFIIndicator").run(
        stocks.high, stocks.low, stocks.close, stocks.volume, window=config.MFI_WINDOWS
    ).money_flow_index
    results['mfi'] = extract_windowed_data(mfi, config.MFI_WINDOWS, 'mfiindicator_window')
    
    return results


def calculate_price_indicators(stocks: pd.DataFrame, config: IndicatorConfig):
    """Calculate log return and price-based indicators"""
    results = {}
    zscore_indicator = create_zscore_indicator()
    
    # Calculate Log Return
    log_return = vbt.IndicatorFactory.from_ta('DailyLogReturnIndicator').run(stocks.close).daily_log_return
    results['log_return'] = log_return
    
    # Calculate Z-Score log return
    zscore_logreturn = zscore_indicator.run(log_return, window=config.ZSCORE_LOGRETURN_WINDOWS).zscore_result
    results['zscore_logreturn'] = extract_windowed_data(zscore_logreturn, config.ZSCORE_LOGRETURN_WINDOWS, 'zscore_indicator_window')
    
    # Calculate Price Z-Score
    price_zscore = zscore_indicator.run(stocks.close, window=config.ZSCORE_WINDOWS).zscore_result
    results['price_zscore'] = extract_windowed_data(price_zscore, config.ZSCORE_WINDOWS, 'zscore_indicator_window')
    
    return results


def calculate_volume_indicators(stocks: pd.DataFrame, config: IndicatorConfig):
    """Calculate volume-based indicators"""
    results = {}
    zscore_indicator = create_zscore_indicator()
    
    # Calculate VWAP
    vwap_indicator = vbt.IndicatorFactory(
        class_name='AVWAP',
        short_name='avwap',
        input_names=['close', 'high', 'low', 'volume'],
        param_names=['is_highest', 'window'],
        output_names=['avwap']
    ).from_apply_func(avwap_func_nb)
    
    vwap = vwap_indicator.run(
        stocks.close, stocks.high, stocks.low, stocks.volume,
        is_highest=[True, False], window=config.VWAP_WINDOW
    ).avwap
    
    vwap_highest = vwap.xs(True, level='avwap_is_highest', axis=1)
    vwap_lowest = vwap.xs(False, level='avwap_is_highest', axis=1)
    vwap_highest.columns = vwap_highest.columns.get_level_values(-1)
    vwap_lowest.columns = vwap_lowest.columns.get_level_values(-1)
    
    results['vwap_highest'] = vwap_highest
    results['vwap_lowest'] = vwap_lowest
    
    # Calculate EFI
    efi = vbt.IndicatorFactory.from_pandas_ta("efi").run(
        stocks.close, stocks.volume, length=config.EFI_WINDOWS
    ).efi
    results['efi'] = extract_windowed_data(efi, config.EFI_WINDOWS, 'efi_length')
    
    # EFI Z-Score
    results['efi_zscore_10'] = zscore_indicator.run(results['efi'][10], window=10).zscore_result
    results['efi_zscore_20'] = zscore_indicator.run(results['efi'][20], window=20).zscore_result
    
    # Calculate Volume Z-Score
    volume_zscore = zscore_indicator.run(stocks.volume, window=config.ZSCORE_WINDOWS).zscore_result
    results['volume_zscore'] = extract_windowed_data(volume_zscore, config.ZSCORE_WINDOWS, 'zscore_indicator_window')
    
    # Calculate OBV
    results['obv'] = vbt.IndicatorFactory.from_ta("OnBalanceVolumeIndicator").run(
        stocks.close, stocks.volume
    ).on_balance_volume
    
    return results


def calculate_relative_strength_indicators(stocks: pd.DataFrame, config: IndicatorConfig):
    """Calculate relative strength indicators"""
    rs_indicator = vbt.IndicatorFactory(
        class_name='MansfieldRelativeStrength',
        short_name='mansfield_relative_strength',
        input_names=['close', 'benmark_close'],
        param_names=['window'],
        output_names=['rs', 'mrs']
    ).from_apply_func(relative_strength_nb)
    
    rs_ind = rs_indicator.run(stocks.close, stocks.close.VNINDEX, window=config.RS_WINDOWS)
    
    # Extract and clean columns for rs and mrs
    rs_by_window = {
        w: rs_ind.rs.xs(w, level='mansfield_relative_strength_window', axis=1).rename_axis(None, axis=1)
        for w in config.RS_WINDOWS
    }
    mrs_by_window = {
        w: rs_ind.mrs.xs(w, level='mansfield_relative_strength_window', axis=1).rename_axis(None, axis=1)
        for w in config.RS_WINDOWS
    }
    
    # Rank mrs_by_window for each window
    mrs_rank_by_window = {
        w: df.rank(axis=1, method='min', ascending=False)
        for w, df in mrs_by_window.items()
    }
    
    # Calculate RS Rating percentile for 12-month window (252 days)
    rs_rating_by_window = {
        w: calculate_rs_rating_percentile(rs_by_window[w])
        for w in config.RS_WINDOWS
    }
    
    return {
        'rs': rs_by_window,
        'mrs': mrs_by_window,
        'mrs_rank': mrs_rank_by_window,
        'rs_rating': rs_rating_by_window
    }


def calculate_volatility_indicators(stocks: pd.DataFrame, config: IndicatorConfig):
    """Calculate volatility-based indicators"""
    results = {}
    
    # Directional Change
    dc_indicator = vbt.IndicatorFactory(
        input_names=['close'],
        param_names=['theta'],
        output_names=['TMV', 'T']
    ).from_apply_func(directional_change_nb)
    
    dc = dc_indicator.run(stocks.close, theta=config.DC_THETA)
    dc_tmv = dc.TMV
    dc_tmv.columns = dc_tmv.columns.get_level_values(-1)
    results['dc_tmv'] = dc_tmv
    
    # Yang-Zhang Volatility
    yz_indicator = vbt.IndicatorFactory(
        class_name='YangZhangVolatility',
        short_name='yz_vol',
        input_names=['close', 'open', 'high', 'low'],
        param_names=['window', 'periods'],
        output_names=['yz_volatility']
    ).from_apply_func(yang_zhang_volatility_nb)
    
    yz_vol = yz_indicator.run(
        stocks.close, stocks.open, stocks.high, stocks.low,
        window=config.YZ_VOLATILITY_WINDOWS, periods=config.YZ_PERIODS
    ).yz_volatility
    
    results['yz_vol'] = extract_windowed_data(yz_vol, config.YZ_VOLATILITY_WINDOWS, 'yz_vol_window')
    
    return results


def calculate_kalman_indicators(stocks: pd.DataFrame, config: IndicatorConfig):
    """Calculate Kalman Filter based indicators"""
    # Initialize Kalman Filter
    kf = KalmanFilter(
        transition_matrices=config.KF_TRANSITION_MATRICES,
        observation_matrices=config.KF_OBSERVATION_MATRICES,
        initial_state_mean=config.KF_INITIAL_STATE_MEAN,
        initial_state_covariance=config.KF_INITIAL_STATE_COVARIANCE,
        observation_covariance=config.KF_OBSERVATION_COVARIANCE,
        transition_covariance=config.KF_TRANSITION_COVARIANCE
    )
    
    # Apply Kalman Filter to each stock
    kf_df = pd.DataFrame()
    for col in stocks.close.columns:
        state_means, _ = kf.filter(stocks[('close', col)].values)
        kf_series = pd.DataFrame(state_means.flatten(), columns=[col], index=stocks.index)
        kf_df = pd.concat([kf_df, kf_series], axis=1)
    
    # Calculate Z-Score of Kalman filtered data
    zscore_indicator = create_zscore_indicator()
    zscore_kf = zscore_indicator.run(kf_df, window=config.ZSCORE_KF_WINDOWS).zscore_result
    zscore_kf_by_window = extract_windowed_data(zscore_kf, config.ZSCORE_KF_WINDOWS, 'zscore_indicator_window')
    
    return {
        'kf': kf_df,
        'zscore_kf': zscore_kf_by_window
    }


def build_features_dataframe(stocks, all_indicators):
    """Build the final features DataFrame from all calculated indicators"""
    ma_results = all_indicators['ma']
    momentum_results = all_indicators['momentum']
    price_results = all_indicators['price']
    volume_results = all_indicators['volume']
    rs_results = all_indicators['rs']
    volatility_results = all_indicators['volatility']
    kalman_results = all_indicators['kalman']
    
    # Create the final DataFrame by combining all indicators
    feature_dict = {
        'close': stocks.close.stack(),
        'volume': stocks.volume.stack(),
        
        # RSI indicators
        'rsi_window_5': momentum_results['rsi'][5].stack(),
        'rsi_window_14': momentum_results['rsi'][14].stack(),
        
        # EMA indicators
        'ema_10': ma_results['ema'][10].stack(),
        'ema_20': ma_results['ema'][20].stack(),
        'ema_50': ma_results['ema'][50].stack(),
        'ema_200': ma_results['ema'][200].stack(),
        
        # ATR indicators
        'atr_10': momentum_results['atr'][10].stack(),
        'atr_14': momentum_results['atr'][14].stack(),
        
        # Price indicators
        'log_return': price_results['log_return'].stack(),
        'zscore_10_log_return': price_results['zscore_logreturn'][10].stack(),
        'zscore_20_log_return': price_results['zscore_logreturn'][20].stack(),
        'price_zscore_20': price_results['price_zscore'][20].stack(),
        'price_zscore_50': price_results['price_zscore'][50].stack(),
        'price_zscore_200': price_results['price_zscore'][200].stack(),
        
        # VWAP indicators
        'vwap_highest': volume_results['vwap_highest'].stack(),
        'vwap_lowest': volume_results['vwap_lowest'].stack(),
        
        # EFI indicators
        'efi_10': volume_results['efi'][10].stack(),
        'efi_20': volume_results['efi'][20].stack(),
        'efi_50': volume_results['efi'][50].stack(),
        'efi_200': volume_results['efi'][200].stack(),
        'efi_zscore_10': volume_results['efi_zscore_10'][10].stack(),
        'efi_zscore_20': volume_results['efi_zscore_20'][20].stack(),
        
        # MFI indicators
        'mfi_10': momentum_results['mfi'][10].stack(),
        'mfi_21': momentum_results['mfi'][21].stack(),
        
        # Relative strength indicators
        'rs_10': rs_results['rs'][10].stack(),
        'rs_20': rs_results['rs'][20].stack(),
        'rs_50': rs_results['rs'][50].stack(),
        'rs_252': rs_results['rs'][252].stack(),
        'mrs_10': rs_results['mrs'][10].stack(),
        'mrs_20': rs_results['mrs'][20].stack(),
        'mrs_50': rs_results['mrs'][50].stack(),
        'mrs_252': rs_results['mrs'][252].stack(),
        'msr_rank_10': rs_results['mrs_rank'][10].stack(),
        'msr_rank_20': rs_results['mrs_rank'][20].stack(),
        'msr_rank_50': rs_results['mrs_rank'][50].stack(),
        'msr_rank_252': rs_results['mrs_rank'][252].stack(),
        
        # RS Rating (12-month percentile)
        'rs_rating_20': rs_results['rs_rating'][20].stack(),
        'rs_rating_50': rs_results['rs_rating'][50].stack(),
        'rs_rating_252': rs_results['rs_rating'][252].stack(),
        
        # Volume indicators
        'volume_ma_10': ma_results['volume_ma'][10].stack(),
        'volume_ma_20': ma_results['volume_ma'][20].stack(),
        'volume_ma_50': ma_results['volume_ma'][50].stack(),
        'volume_ma_200': ma_results['volume_ma'][200].stack(),
        'volume_zscore_20': volume_results['volume_zscore'][20].stack(),
        'volume_zscore_50': volume_results['volume_zscore'][50].stack(),
        'obv': volume_results['obv'].stack(),
        
        # Volatility indicators
        'dc_tmv': volatility_results['dc_tmv'].stack(),
        'yz_vol_10': volatility_results['yz_vol'][10].stack(),
        'yz_vol_20': volatility_results['yz_vol'][20].stack(),
        
        # Kalman Filter indicators
        'kf': kalman_results['kf'].stack(),
        'zscore_kf_10': kalman_results['zscore_kf'][10].stack(),
        'zscore_kf_20': kalman_results['zscore_kf'][20].stack(),
    }
    
    final_df = pd.DataFrame(feature_dict)
    final_df = final_df.reset_index().rename(columns={'level_1': 'symbol'})
    final_df['key'] = final_df['symbol'] + "_" + final_df['date'].astype(str)
    return final_df


@task(log_prints=True)
def load_stock_data() -> pd.DataFrame:
    """
    Load stock data from database
    """
    """
    Alternative data loading from delta lake
    """
    try:
        from deltalake import DeltaTable
        
        print("Loading data from delta lake with storage options:", get_storage_options())
        
        dt = DeltaTable("s3://delta-table-storage/stocks", storage_options=get_storage_options())

        ## Get data for last 2 years
        now = pd.Timestamp.now()
        start_date = now - pd.DateOffset(years=2)
        df = dt.to_pandas(
            filters=[("date", ">=", start_date)], 
            columns=["symbol", "date", "close", "open", "high", "low", "volume"]
        )
        
        # Convert to the same format as H5 store
        df = df.set_index(["date", "symbol"])
        df = df[~df.index.duplicated(keep='first')]  # Drop duplicate index entries
        stocks = df.unstack(level=1).bfill().ffill()
        
        print("Successfully loaded data from delta lake")
        return stocks
        
    except Exception as e:
        print(f"Error loading from delta lake: {e}")
        raise

@task(log_prints=True)
def run_indicators(stocks: pd.DataFrame) -> pd.DataFrame:
    """
    Run indicators for building features using a clean, modular approach
    """
    config = IndicatorConfig()
    
    print("Calculating moving average indicators...")
    ma_results = calculate_moving_averages(stocks, config)
    
    print("Calculating momentum indicators...")
    momentum_results = calculate_momentum_indicators(stocks, config)
    
    print("Calculating price indicators...")
    price_results = calculate_price_indicators(stocks, config)
    
    print("Calculating volume indicators...")
    volume_results = calculate_volume_indicators(stocks, config)
    
    print("Calculating relative strength indicators...")
    rs_results = calculate_relative_strength_indicators(stocks, config)
    
    print("Calculating volatility indicators...")
    volatility_results = calculate_volatility_indicators(stocks, config)
    
    print("Calculating Kalman filter indicators...")
    kalman_results = calculate_kalman_indicators(stocks, config)
    
    print("Building final features DataFrame...")
    all_indicators = {
        'ma': ma_results,
        'momentum': momentum_results,
        'price': price_results,
        'volume': volume_results,
        'rs': rs_results,
        'volatility': volatility_results,
        'kalman': kalman_results
    }
    
    final_df = build_features_dataframe(stocks, all_indicators)
    
    print(f"Generated features DataFrame with shape: {final_df.shape}")
    return final_df

def sync_features_to_delta_lake(features_data: pd.DataFrame):
    """
    Sync features data to delta-lake
    """
    table_path = "s3://delta-table-storage/stocks_feature_store"
    storage_options = get_storage_options()
    try:
        from deltalake import DeltaTable, write_deltalake

        # Drop None/NaN values
        features_data = features_data.dropna()

        isExist = DeltaTable.is_deltatable(table_path, storage_options=storage_options)
        if isExist:
            dt = DeltaTable(table_path, storage_options=storage_options)

            # Filter source data to last 30 days only
            now = pd.Timestamp.now()
            cutoff_date = now - pd.Timedelta(days=30)
            recent_data = features_data[features_data['date'] >= cutoff_date]
            
            print(f"Merging {len(recent_data)} records from last 30 days (since {cutoff_date.strftime('%Y-%m-%d')})")
            
            # Build merge operation
            merge_builder = dt.merge(
                source=recent_data,
                predicate="target.key = source.key",
                source_alias="source",
                target_alias="target"
            )
            
            # Insert new records that don't exist in target
            merge_builder = merge_builder.when_not_matched_insert_all()
            
            # Update matched records
            merge_builder = merge_builder.when_matched_update_all()
            
            # Execute the merge
            result = merge_builder.execute()
                
            print(f"Merge completed: {result}")
        else:
            result = write_deltalake(table_path, features_data, storage_options=storage_options, mode="overwrite")
            print("Write features data to delta-lake")

    except Exception as e:
        print(f"Error syncing features to delta-lake: {e}")
        raise


@flow(log_prints=True)
def create_feature_store():
    """
    Create feature store
    """
    stock_data = load_stock_data()
    if stock_data.empty:
        print("Stock data is empty")
        return
    
    features_data = run_indicators(stock_data)
    if features_data.empty:
        print("Features data is empty")
        return
    
    print("Features data:")
    print(features_data.tail())
    
    sync_features_to_delta_lake(features_data)
    print("Features data synced to delta-lake")


if __name__ == "__main__":
    # Comment to run locally
    # create_feature_store()
    # create_feature_store()

    # # Comment to deploy
    create_feature_store.from_source(
        source=str(Path(__file__).parent),  # code stored in local directory
        entrypoint="create_feature_store.py:create_feature_store",
    ).deploy(
        name="create_feature_store",
        work_pool_name="my-worker",
        # Run at 3:00 AM from Monday to Friday
        cron="0 8 * * 1-5", ## UTC+0
    )