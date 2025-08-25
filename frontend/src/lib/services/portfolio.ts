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

export async function optimizePortfolio(request: OptimizationRequest): Promise<OptimizationResult> {
  return apiPost<OptimizationResult>('/portfolio/optimize', request);
}
