import { apiPost, apiGet } from '../api';

export type OptimizationMethod = 'hrp' | 'ef' | 'max_sharpe' | 'min_volatility' | 'max_quadratic_utility' | 'efficient_risk' | 'efficient_return' | 'black_litterman' | 'cvar' | 'cla';

export type ReturnPredictionMethod = 'historical_mean' | 'bvar';

export type RiskModel = 
  | 'sample_cov'
  | 'semicovariance' 
  | 'exp_cov'
  | 'ledoit_wolf'
  | 'ledoit_wolf_constant_variance'
  | 'ledoit_wolf_single_factor'
  | 'ledoit_wolf_constant_correlation'
  | 'oracle_approximating';

export interface StockSymbol {
  id: number;
  symbol: string;
  name: string | null;
  id_sector_level_3: number | null;
  id_sector_level_4: number | null;
  vonhoa_d: number | null;
  created_at: string;
  updated_at: string;
}

export interface OptimizationRequest {
  tickers: string[];
  start_date?: string;
  end_date?: string;
  method: OptimizationMethod;
  risk_model?: RiskModel;
  risk_free_rate?: number;
  constraints?: {
    min_weight?: number;
    max_weight?: number;
  };
  // Additional parameters for specific optimization methods
  risk_aversion?: number;  // For max_quadratic_utility and black_litterman
  target_risk?: number;    // For efficient_risk
  target_return?: number;  // For efficient_return
  
  // Black-Litterman specific parameters
  market_caps?: Record<string, number>;  // Market cap weights for equilibrium portfolio
  views?: Record<string, number>;        // Investor views on expected returns
  view_confidences?: Record<string, number>;  // Confidence in views (lower = more confident)
  
  // Return prediction method
  return_prediction_method?: ReturnPredictionMethod;  // Method for predicting expected returns (default: historical_mean)
  bvar_forecast_periods?: number;  // Number of periods to forecast for BVAR (only used if return_prediction_method is bvar)
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

export interface Position {
  id: number;
  ticker: string;
  quantity: number;
  purchase_price: number;
  purchase_date: string;
  notes?: string | null;
  current_price?: number | null;
  created_at: string;
}

export interface Transaction {
  id: number;
  ticker: string;
  // Dividend rows share the transactions ledger. Narrowing this to buy|sell was
  // false and made every dividend read as a sell.
  transaction_type: 'buy' | 'sell' | 'dividend_cash' | 'dividend_stock';
  quantity: number;
  price: number;
  close_price?: number | null;
  transaction_date: string;
  fees?: number | null;
  notes?: string | null;
  created_at: string;
}

export async function optimizePortfolio(request: OptimizationRequest): Promise<OptimizationResult> {
  return apiPost<OptimizationResult>('/portfolio/optimize', request);
}

export async function closePosition(request: ClosePositionRequest): Promise<ClosePositionResponse> {
  return apiPost<ClosePositionResponse>('/portfolio/positions/close', request);
}

export async function getPositions(): Promise<Position[]> {
  return apiGet<Position[]>('/portfolio/positions');
}

export async function getTransactions(): Promise<Transaction[]> {
  return apiGet<Transaction[]>('/portfolio/transactions');
}

export async function getAllStockSymbols(limit?: number): Promise<StockSymbol[]> {
  const params = limit ? `?limit=${limit}` : '';
  return apiGet<StockSymbol[]>(`/sector/symbols${params}`);
}
