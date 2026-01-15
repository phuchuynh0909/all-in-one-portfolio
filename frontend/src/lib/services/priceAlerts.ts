/**
 * Price Alerts Service
 * Handles API calls for price alert management
 */

import { apiGet, apiPost, API_BASE_URL } from '../api';

export type AlertCondition = 'gt' | 'gte' | 'lt' | 'lte' | 'eq';

export interface PriceAlert {
  id: number;
  symbol: string;
  condition: AlertCondition;
  target_price: number;
  is_active: boolean;
  is_triggered: boolean;
  triggered_at: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface PriceAlertWithPrice extends PriceAlert {
  current_price: number | null;
  price_diff: number | null;
  price_diff_pct: number | null;
}

export interface PriceAlertsResponse {
  alerts: PriceAlertWithPrice[];
  total: number;
}

export interface CreateAlertRequest {
  symbol: string;
  condition: AlertCondition;
  target_price: number;
  notes?: string;
}

export interface UpdateAlertRequest {
  symbol?: string;
  condition?: AlertCondition;
  target_price?: number;
  is_active?: boolean;
  notes?: string;
}

export interface GetAlertsParams {
  symbol?: string;
  is_active?: boolean;
  is_triggered?: boolean;
  offset?: number;
  limit?: number;
}

/**
 * Get price alerts with optional filtering
 */
export async function getPriceAlerts(params: GetAlertsParams = {}): Promise<PriceAlertsResponse> {
  const searchParams = new URLSearchParams();
  
  if (params.symbol) searchParams.append('symbol', params.symbol);
  if (params.is_active !== undefined) searchParams.append('is_active', params.is_active.toString());
  if (params.is_triggered !== undefined) searchParams.append('is_triggered', params.is_triggered.toString());
  if (params.offset !== undefined) searchParams.append('offset', params.offset.toString());
  if (params.limit !== undefined) searchParams.append('limit', params.limit.toString());
  
  const queryString = searchParams.toString();
  const url = queryString ? `/price-alerts?${queryString}` : '/price-alerts';
  
  return await apiGet<PriceAlertsResponse>(url);
}

/**
 * Get a single price alert by ID
 */
export async function getPriceAlert(alertId: number): Promise<PriceAlertWithPrice> {
  return await apiGet<PriceAlertWithPrice>(`/price-alerts/${alertId}`);
}

/**
 * Create a new price alert
 */
export async function createPriceAlert(request: CreateAlertRequest): Promise<PriceAlert> {
  return await apiPost<PriceAlert>('/price-alerts', request);
}

/**
 * Update a price alert
 */
export async function updatePriceAlert(alertId: number, request: UpdateAlertRequest): Promise<PriceAlert> {
  const res = await fetch(`${API_BASE_URL}/price-alerts/${alertId}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });
  if (!res.ok) throw new Error(`PUT /price-alerts/${alertId} failed: ${res.status}`);
  return (await res.json()) as PriceAlert;
}

/**
 * Delete a price alert
 */
export async function deletePriceAlert(alertId: number): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API_BASE_URL}/price-alerts/${alertId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`DELETE /price-alerts/${alertId} failed: ${res.status}`);
  return await res.json();
}

/**
 * Toggle the active status of a price alert
 */
export async function togglePriceAlert(alertId: number): Promise<PriceAlert> {
  return await apiPost<PriceAlert>(`/price-alerts/${alertId}/toggle`, {});
}

/**
 * Reset a triggered alert to active state
 */
export async function resetPriceAlert(alertId: number): Promise<PriceAlert> {
  return await apiPost<PriceAlert>(`/price-alerts/${alertId}/reset`, {});
}

/**
 * Get condition label for display
 */
export function getConditionLabel(condition: AlertCondition): string {
  const labels: Record<AlertCondition, string> = {
    gt: '>',
    gte: '≥',
    lt: '<',
    lte: '≤',
    eq: '=',
  };
  return labels[condition] || condition;
}

/**
 * Get condition description for display
 */
export function getConditionDescription(condition: AlertCondition): string {
  const descriptions: Record<AlertCondition, string> = {
    gt: 'Greater than',
    gte: 'Greater than or equal',
    lt: 'Less than',
    lte: 'Less than or equal',
    eq: 'Equal to',
  };
  return descriptions[condition] || condition;
}

