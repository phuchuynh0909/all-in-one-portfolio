from datetime import datetime

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi_cache.decorator import cache

from app.schemas.regime import (
    MarkovKamaData,
    MSRegimeData,
    RegimeRequest,
    RegimeResponse,
    TicaHmmData,
    YZPercentileData,
)
from app.services.indicators import (
    calculate_yz_volatility,
    markov_kama_regime,
    ms_regime_multifeature,
    tica_hmm_regime,
)
from app.services.stock_service import _load_delta_stocks
from app.services.utils import convert_nans

router = APIRouter(prefix="/regime", tags=["regime"])

_YZ_LOOKBACK = 252  # rolling window for percentile rank


@router.post("/{symbol}", response_model=RegimeResponse)
@cache(expire=3600)
async def get_regime(symbol: str, request: RegimeRequest) -> RegimeResponse:
    start = datetime.strptime(request.start_date, "%Y-%m-%d") if request.start_date else None
    end   = datetime.strptime(request.end_date,   "%Y-%m-%d") if request.end_date   else None

    df = _load_delta_stocks(
        symbols=[symbol],
        start=start,
        end=end,
        columns=["date", "open", "high", "low", "close", "volume", "symbol"],
    )
    df = df[df["symbol"] == symbol].drop(columns=["symbol"])
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

    close  = df["close"].values.astype(float)
    open_  = df["open"].values.astype(float)
    high   = df["high"].values.astype(float)
    low    = df["low"].values.astype(float)
    volume = df["volume"].values.astype(float)
    index  = pd.DatetimeIndex(df["date"])

    # 1. Markov-KAMA (HMM-style: 5-state: Bearish_High_Var … Bullish_High_Var)
    regime_code, low_var_prob, high_var_prob, _trend, kama = markov_kama_regime(
        close, index=index
    )

    # 2. Multi-feature Markov-Switching (006 — 2-state: low / high stress)
    ms_regime, ms_regime_prob, _ = ms_regime_multifeature(
        close, open_, high, low, volume
    )

    # 3. TICA + HMM (Risk-On / Caution / Risk-Off)
    th_code, th_label, _, _ = tica_hmm_regime(close, index=index)

    # 4. Yang-Zhang percentile (007 — rolling 252-day pct-rank)
    yz_vol = np.asarray(
        calculate_yz_volatility(open_, high, low, close, window=21, periods=252),
        dtype=float,
    )
    yz_series = pd.Series(yz_vol)
    pct_rank = yz_series.rolling(_YZ_LOOKBACK).apply(
        lambda x: float(np.nansum(x[:-1] <= x[-1])) / max(int(np.sum(~np.isnan(x[:-1]))), 1) * 100,
        raw=True,
    ).values

    return RegimeResponse(
        symbol=symbol,
        timestamps=df["date"].dt.strftime("%Y-%m-%d").tolist(),
        open=open_.tolist(),
        high=high.tolist(),
        low=low.tolist(),
        close=close.tolist(),
        markov_kama=MarkovKamaData(
            regime_code=regime_code.tolist(),
            low_var_prob=convert_nans(low_var_prob),
            high_var_prob=convert_nans(high_var_prob),
            kama=convert_nans(kama),
        ),
        ms_regime=MSRegimeData(
            regime=ms_regime.tolist(),
            regime_prob=convert_nans(ms_regime_prob),
        ),
        yz_percentile=YZPercentileData(
            yz_vol=convert_nans(yz_vol),
            pct_rank=convert_nans(pct_rank),
        ),
        tica_hmm=TicaHmmData(
            regime_code=th_code.tolist(),
            regime_label=th_label,
        ),
    )
