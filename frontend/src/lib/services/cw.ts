import { apiGet } from '../api';


export interface CoveredWarrantDetail {
  symbol: string;
  stock_name: string | null;
  base_stock_code: string | null;
  base_stock_name: string | null;
  cw_stock_type: string | null;
  exercise_price: number | null;
  conversion_rate: number | null;
  trading_date: string | null;
  listing_date: string | null;
  first_trading_date: string | null;
  last_trading_date: string | null;
  period: string | null;
  issuer_name: string | null;
  last_price: number | null;
  close_price: number | null;
  basic_price: number | null;
  offering_price: number | null;
  total_vol: number | null;
  total_val: number | null;
  raw_base_stock_price: number | null;
  source_url: string | null;
}

export interface CoveredWarrantAssumptions {
  stock_price: number | null;
  warrant_price: number | null;
  annual_volatility: number | null;
  risk_free_rate: number;
  days_to_expiry: number;
  time_to_expiry_years: number;
  underlying_price_source: string;
  warrant_price_source: string;
  volatility_source: string;
}

export interface CoveredWarrantGreeks {
  option_style: 'call' | 'put';
  theoretical_price: number | null;
  intrinsic_value: number | null;
  time_value: number | null;
  delta: number | null;
  gamma: number | null;
  theta_per_day: number | null;
  vega_per_1pct_vol: number | null;
  rho_per_1pct_rate: number | null;
  d1: number | null;
  d2: number | null;
}

export interface CoveredWarrantAnalysis {
  moneyness_pct: number | null;
  break_even_stock_price: number | null;
  premium_to_break_even_pct: number | null;
  leverage: number | null;
  effective_gearing: number | null;
  theoretical_edge_pct: number | null;
  parity_price_ratio: number | null;
  in_the_money: boolean | null;
  summary: string;
}

export interface CoveredWarrantResponse {
  detail: CoveredWarrantDetail;
  assumptions: CoveredWarrantAssumptions;
  greeks: CoveredWarrantGreeks;
  analysis: CoveredWarrantAnalysis;
}

export async function fetchCoveredWarrant(symbol: string): Promise<CoveredWarrantResponse> {
  const normalized = symbol.trim().toUpperCase();
  return apiGet<CoveredWarrantResponse>(`/cw/${encodeURIComponent(normalized)}`);
}
