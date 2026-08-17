/**
 * Block Episodes ("large-execution footprint") service.
 * Intraday stitched runs of same-direction candidate 1-second bins produced by
 * the worker's large-execution detector and served from ClickHouse.
 * A footprint is evidence of sustained one-sided execution — not proof of an
 * institution or a parent order.
 */

import { apiGet } from '../api';

export interface BlockEpisode {
  symbol: string;
  start_time: string;       // ISO UTC
  end_time: string;         // ISO UTC
  start_epoch: number;      // unix seconds, UTC
  end_epoch: number;        // unix seconds, UTC
  duration_seconds: number;
  side: number;             // 1=BUY, 2=SELL, 0=unknown
  side_label: string;       // "BUY" / "SELL" / "NA"
  candidate_type: string;   // FLOW_CLUSTER / LARGE_PRINT / FLOW_CLUSTER_AND_LARGE_PRINT
  signed_notional: number;
  abs_notional: number;
  num_trades: number;
  num_bins: number;
  large_print_count: number;
  max_abs_z: number;
  max_abs_imbalance: number;
}

export interface BlockEpisodesResponse {
  symbol: string;
  episodes: BlockEpisode[];
}

export type CandidateType =
  | 'FLOW_CLUSTER'
  | 'LARGE_PRINT'
  | 'FLOW_CLUSTER_AND_LARGE_PRINT';

export interface GetBlockEpisodesParams {
  fromDate?: string;        // YYYY-MM-DD
  toDate?: string;          // YYYY-MM-DD
  side?: number;            // 1=BUY, 2=SELL
  candidateType?: CandidateType;
  minAbsNotional?: number;
  limit?: number;
}

export const CANDIDATE_TYPE_SHORT: Record<string, string> = {
  FLOW_CLUSTER: 'Flow',
  LARGE_PRINT: 'Large',
  FLOW_CLUSTER_AND_LARGE_PRINT: 'Flow+Lg',
};

export const fetchBlockEpisodes = async (
  symbol: string,
  params: GetBlockEpisodesParams = {},
): Promise<BlockEpisodesResponse> => {
  const q = new URLSearchParams({ symbol });
  if (params.fromDate) q.set('from_date', params.fromDate);
  if (params.toDate) q.set('to_date', params.toDate);
  if (params.side != null) q.set('side', String(params.side));
  if (params.candidateType) q.set('candidate_type', params.candidateType);
  if (params.minAbsNotional != null) q.set('min_abs_notional', String(params.minAbsNotional));
  if (params.limit != null) q.set('limit', String(params.limit));
  return apiGet<BlockEpisodesResponse>(`/block-episodes?${q.toString()}`);
};
