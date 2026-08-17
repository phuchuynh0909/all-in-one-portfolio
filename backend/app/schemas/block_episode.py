"""Schemas for block episodes ("large-execution footprints").

These describe the rows produced by the worker's ``core.large_execution``
detector and stored in the ClickHouse ``block_episodes`` table (see
``worker/model.py``). One episode is a stitched run of same-direction candidate
1-second bins — a *footprint* of sustained/one-sided execution or an outlier
large print, NOT proof of an institution or a parent order.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel

# candidate_type values emitted by the detector.
FLOW_CLUSTER = "FLOW_CLUSTER"
LARGE_PRINT = "LARGE_PRINT"
FLOW_CLUSTER_AND_LARGE_PRINT = "FLOW_CLUSTER_AND_LARGE_PRINT"
CANDIDATE_TYPES = (FLOW_CLUSTER, LARGE_PRINT, FLOW_CLUSTER_AND_LARGE_PRINT)


class BlockEpisode(BaseModel):
    symbol: str
    # Bounds as ISO strings (UTC) and as unix seconds for chart alignment.
    start_time: str
    end_time: str
    start_epoch: int          # unix seconds, UTC — chart x of the first bin
    end_epoch: int            # unix seconds, UTC — chart x of the last bin
    duration_seconds: int     # end_epoch - start_epoch
    side: int                 # aggressor: 1=BUY, 2=SELL, 0=unknown
    side_label: str           # "BUY" / "SELL" / "NA"
    candidate_type: str       # FLOW_CLUSTER / LARGE_PRINT / FLOW_CLUSTER_AND_LARGE_PRINT
    signed_notional: float    # buy(+) / sell(-) notional summed over the episode
    abs_notional: float       # gross notional magnitude (drives size tiers)
    num_trades: int
    num_bins: int             # candidate bins stitched
    large_print_count: int
    max_abs_z: float          # peak signed-notional surprise
    max_abs_imbalance: float  # peak one-sidedness (0..1)


class BlockEpisodesResponse(BaseModel):
    symbol: str
    episodes: List[BlockEpisode]
