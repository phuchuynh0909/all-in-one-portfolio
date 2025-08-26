import { apiPost } from '../api';

export type OptimizationMethod = 'hrp' | 'ef' | 'cvar' | 'cla';

export interface OptimizationRequest {
  tickers: string[];
  start_date?: string;
  end_date?: string;
  method: OptimizationMethod;
  risk_free_rate?: number;
  constraints?: {
    min_weight?: number;
    max_weight?: number;
  };
}

export interface OptimizationResult {
  method: OptimizationMethod;
  weights: Record<string, number>;
  expected_return: number | null;
  volatility: number | null;
  sharpe_ratio: number | null;
}

export interface ClosePositionRequest {
  position_id: number;
  quantity_to_close: number;
  closing_price: number;
  closing_date: string;
  fees?: number;
  notes?: string;
}

export interface ClosePositionResponse {
  success: boolean;
  message: string;
  position_updated: boolean;
  remaining_quantity?: number;
  transaction_id: number;
  realized_pl: number;
  realized_pl_pct: number;
}

export async function optimizePortfolio(request: OptimizationRequest): Promise<OptimizationResult> {
  return apiPost<OptimizationResult>('/portfolio/optimize', request);
}

export async function closePosition(request: ClosePositionRequest): Promise<ClosePositionResponse> {
  return apiPost<ClosePositionResponse>('/portfolio/positions/close', request);
}
