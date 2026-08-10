from typing import List, Optional
from pydantic import BaseModel


class MarkovKamaData(BaseModel):
    regime_code: List[int]                  # -2 to 2
    low_var_prob: List[Optional[float]]
    high_var_prob: List[Optional[float]]
    kama: List[Optional[float]]


class MSRegimeData(BaseModel):
    regime: List[int]                       # 0=low-stress, 1=high-stress
    regime_prob: List[Optional[float]]      # P(high-stress)


class YZPercentileData(BaseModel):
    yz_vol: List[Optional[float]]
    pct_rank: List[Optional[float]]         # 0–100


class TicaHmmData(BaseModel):
    regime_code:  List[int]   # 0..k-1 (Viterbi)
    regime_label: List[str]   # "Risk-On" | "Caution" | "Risk-Off"


class RegimeResponse(BaseModel):
    symbol: str
    timestamps: List[str]
    open: List[float]
    high: List[float]
    low: List[float]
    close: List[float]
    markov_kama: MarkovKamaData
    ms_regime: MSRegimeData
    yz_percentile: YZPercentileData
    tica_hmm: TicaHmmData


class RegimeRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
