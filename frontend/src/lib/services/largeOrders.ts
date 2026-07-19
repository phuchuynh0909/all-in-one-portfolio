/**
 * Large Orders (Layer 3 block) service.
 * One net large-order bubble per trading day for a symbol over a date range,
 * for plotting on the 1D candlestick chart (Pine volume-cluster style).
 */

import { apiGet } from '../api';

export interface LargeOrderDay {
  date: string;        // ICT trading date YYYY-MM-DD
  time: number;        // unix seconds, UTC-midnight of the VN date (daily candle x)
  side: number;        // net side: 1=BUY if net_delta>=0 else 2=SELL
  net_delta: number;   // buy notional - sell notional (signed)
  buy_value: number;
  sell_value: number;
  total_value: number; // buy + sell notional (drives size tier)
  buy_qty: number;
  sell_qty: number;
  total_qty: number;   // buy + sell shares (volume label)
  num_trades: number;
  block_count: number;
}

export interface LargeOrdersResponse {
  symbol: string;
  blocks: LargeOrderDay[];
}

export interface GetLargeOrdersParams {
  fromDate?: string;   // YYYY-MM-DD
  toDate?: string;     // YYYY-MM-DD
  minValue?: number;   // only blocks with notional >= this
}

export const fetchLargeOrders = async (
  symbol: string,
  params: GetLargeOrdersParams = {},
): Promise<LargeOrdersResponse> => {
  const q = new URLSearchParams({ symbol });
  if (params.fromDate) q.set('from_date', params.fromDate);
  if (params.toDate) q.set('to_date', params.toDate);
  if (params.minValue != null) q.set('min_value', String(params.minValue));
  return apiGet<LargeOrdersResponse>(`/large-orders?${q.toString()}`);
};
