/**
 * Trade-flow anomaly service.
 *
 * Windowed trade-flow features are maintained by a ClickHouse materialized view
 * over `ticks`; the backend normalizes them per symbol and time-of-day and
 * scores each window with Isolation Forest — a point-in-time verdict on whether
 * the window is unusual.
 *
 * The tape has no order book, so a flagged window is evidence of unusual
 * *executed* flow — not proof of an institution, and "absorption" cannot say
 * which side absorbed which.
 */

import { apiGet } from '../api';

export interface TradeFlowWindow {
  symbol: string;
  window_start: string;             // ISO UTC
  time: number;                     // unix seconds (chart x)
  trade_count: number;
  volume: number;
  vwap: number | null;
  ret: number | null;
  realized_vol: number | null;
  trade_imbalance: number | null;   // (buy-sell)/(buy+sell), real aggressor side
  max_trade_size: number;
  size_hhi: number | null;          // Herfindahl on trade sizes: concentration
  top_trade_share: number | null;   // largest trade / window volume
  burstiness: number | null;        // max trades/sec vs the window's own mean
  median_interarrival_ms: number | null;
  same_ms_share: number | null;     // trades landing in the same ms as the previous
  impact: number | null;
  absorption: number | null;        // much volume, little price move
  anomaly_score: number;            // higher = more unusual
  is_anomaly: boolean;
  side: number;                     // 1=BUY-leaning, 2=SELL-leaning, 0=neutral
  fwd_ret_1m: number | null;
  fwd_ret_5m: number | null;
  fwd_ret_15m: number | null;
}

export interface TradeFlowResponse {
  symbol: string;
  window_seconds: number;
  windows_scanned: number;
  anomalies_found: number;
  note: string | null;
  windows: TradeFlowWindow[];
}

export interface GetTradeFlowParams {
  fromDate?: string;   // YYYY-MM-DD
  toDate?: string;     // YYYY-MM-DD
  limit?: number;
  onlyFlagged?: boolean;
}

export const fetchTradeFlowAnomalies = async (
  symbol: string,
  params: GetTradeFlowParams = {},
): Promise<TradeFlowResponse> => {
  const q = new URLSearchParams({ symbol });
  if (params.fromDate) q.set('from_date', params.fromDate);
  if (params.toDate) q.set('to_date', params.toDate);
  if (params.limit != null) q.set('limit', String(params.limit));
  if (params.onlyFlagged != null) q.set('only_flagged', String(params.onlyFlagged));
  return apiGet<TradeFlowResponse>(`/trade-flow/anomalies?${q.toString()}`);
};

/**
 * The single feature that most stands out for this window, used as a one-word
 * "why". Purely presentational — the model scores the whole vector, not this.
 */
export function dominantTrait(w: TradeFlowWindow): string {
  if ((w.same_ms_share ?? 0) >= 0.4) return 'same-ms fills';
  if ((w.top_trade_share ?? 0) >= 0.5) return 'single block';
  if ((w.burstiness ?? 0) >= 8) return 'burst';
  if (Math.abs(w.trade_imbalance ?? 0) >= 0.8) return 'one-sided';
  if ((w.size_hhi ?? 0) >= 0.25) return 'concentrated';
  if ((w.absorption ?? 0) >= 5) return 'absorption';
  return 'volume';
}
