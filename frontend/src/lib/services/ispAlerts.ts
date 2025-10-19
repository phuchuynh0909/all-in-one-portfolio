/**
 * ISP Alerts Service
 * Handles API calls for ISP alert data
 */

import { apiGet } from '../api';

export interface ISPAlert {
  symbol: string;
  ts: number; // Unix timestamp in milliseconds
  abnormality_ratio_5m: number;
  abnormality_ratio_15m: number;
  abnormality_ratio_30m: number;
  abnormality_ratio_60m: number;
}

export interface ISPAlertsResponse {
  alerts: ISPAlert[];
  total: number;
  offset: number;
  limit: number;
}

export interface GetAlertsParams {
  offset?: number;
  limit?: number;
  symbol?: string;
  min_abnormality?: number;
  since?: string;
}

export interface GetLatestAlertsParams {
  limit?: number;
  since?: number; // Unix timestamp in milliseconds
}

/**
 * Get ISP alerts with pagination and filtering
 */
export async function getISPAlerts(params: GetAlertsParams = {}): Promise<ISPAlertsResponse> {
  const searchParams = new URLSearchParams();
  
  if (params.offset !== undefined) searchParams.append('offset', params.offset.toString());
  if (params.limit !== undefined) searchParams.append('limit', params.limit.toString());
  if (params.symbol) searchParams.append('symbol', params.symbol);
  if (params.min_abnormality !== undefined) searchParams.append('min_abnormality', params.min_abnormality.toString());
  if (params.since) searchParams.append('since', params.since);
  
  const queryString = searchParams.toString();
  const url = queryString ? `/isp/alerts?${queryString}` : '/isp/alerts';
  
  return await apiGet<ISPAlertsResponse>(url);
}

/**
 * Get latest alerts since a specific timestamp (optimized for real-time display)
 */
export async function getLatestAlerts(params: GetLatestAlertsParams = {}): Promise<ISPAlert[]> {
  const searchParams = new URLSearchParams();
  
  if (params.limit !== undefined) searchParams.append('limit', params.limit.toString());
  if (params.since !== undefined) searchParams.append('since', params.since.toString());
  
  const queryString = searchParams.toString();
  const url = queryString ? `/isp/alerts/latest?${queryString}` : '/isp/alerts/latest';
  
  return await apiGet<ISPAlert[]>(url);
}

/**
 * Get list of symbols with recent alerts
 */
export async function getActiveSymbols(seconds: number = 300): Promise<string[]> {
  return await apiGet<string[]>(`/isp/alerts/symbols?seconds=${seconds}`);
}

