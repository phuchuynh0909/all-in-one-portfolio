import os
import numpy as np
import pandas as pd


MINIO_HOST = "https://minio.phuchuynh.xyz"

_STORAGE_OPTIONS = {
    "AWS_ACCESS_KEY_ID":         "CzOwnLkEDXQy951AOqes",
    "AWS_SECRET_ACCESS_KEY":     "fdRe91TOtqTl0icUkZLsUnWvZa90aZ5qG5rVEf7S",
    "AWS_ENDPOINT_URL":          MINIO_HOST,
    "AWS_ALLOW_HTTP":            "true",
    "AWS_EC2_METADATA_DISABLED": "true",
    "AWS_REGION":                "us-east-1",
    "aws_conditional_put":       "etag",
}

_DEFAULT_WATCHLIST = os.path.join(
    os.path.dirname(__file__), "../../models/watchlist.csv"
)
_DEFAULT_H5   = "stocks_data_latest.h5"
_DEFAULT_KEY  = "stocks"


def _read_watchlist(path: str) -> np.ndarray:
    return pd.read_csv(path).iloc[:, 0].values


def _fetch_from_delta(symbols, years: int = 10) -> pd.DataFrame:
    from deltalake import DeltaTable

    start = pd.Timestamp.now() - pd.DateOffset(years=years)
    dt    = DeltaTable("s3://delta-table-storage/stocks", storage_options=_STORAGE_OPTIONS)
    raw   = dt.to_pandas(
        filters=[("date", ">=", start), ("symbol", "in", symbols)],
        columns=["symbol", "date", "close", "open", "high", "low", "volume"],
    )
    raw = raw.drop_duplicates(subset=["date", "symbol"], keep="last")
    raw = raw.set_index(["date", "symbol"])
    df  = raw.unstack(level=1).bfill().ffill()
    print(f"Loaded {df.shape[1]} symbols × {len(df)} bars from DeltaLake")
    return df


def load_stocks(
    watchlist_path: str = _DEFAULT_WATCHLIST,
    local_file: str     = _DEFAULT_H5,
    store_key: str      = _DEFAULT_KEY,
    years: int          = 10,
    refresh: bool       = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Load OHLCV data, cache to local HDF5.

    Parameters
    ----------
    watchlist_path : CSV with symbol list in the first column
    local_file     : HDF5 cache path (relative to CWD, i.e. the notebook dir)
    store_key      : key inside the HDF5 store
    years          : how many years of history to pull from DeltaLake
    refresh        : force re-download even if the cache exists

    Returns
    -------
    df_raw, open_, high, low, close, volume
      df_raw  — MultiIndex DataFrame (dates × (field, symbol))
      rest    — each a plain DataFrame (dates × symbols)
    """
    symbols = _read_watchlist(watchlist_path)

    if not refresh and os.path.exists(local_file):
        print(f"Loading from HDF5 cache: {local_file}")
        with pd.HDFStore(local_file, mode="r") as store:
            df_raw = store[store_key]
    else:
        print("HDF5 not found — fetching from DeltaLake …")
        df_raw = _fetch_from_delta(symbols, years=years)
        with pd.HDFStore(local_file, mode="w") as store:
            store.put(store_key, df_raw)
        print(f"Saved to {local_file}")

    # filter to watchlist (handles stale cache with extra symbols)
    df_raw = df_raw.loc[
        :, df_raw.columns.get_level_values("symbol").isin(symbols)
    ]

    print(f"Shape: {df_raw.shape}  |  {df_raw.index[0].date()} → {df_raw.index[-1].date()}")
    return df_raw
