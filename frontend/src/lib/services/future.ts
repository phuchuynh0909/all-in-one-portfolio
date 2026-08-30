import { API_BASE_URL } from '../api';
export interface FutureOhlcResponse {
  symbol: string;
  timestamps: string[];
  ohlc: { open: number[]; high: number[]; low: number[]; close: number[] };
  volume: { total: number[]; buy: number[]; sell: number[] };
  indicators: {
    bsi: (number | null)[];
    q_lo: (number | null)[];
    q_hi: (number | null)[];
    kama: (number | null)[];
  };
}

export interface FetchFutureParams {
  start_date?: string;
  end_date?: string;
  timeframe?: string;
  kappa?: number;
  quantile_lookback?: number;
  q_lo_pct?: number;
  q_hi_pct?: number;
  kama_period?: number;
}

export async function fetchFutureOhlc(
  symbol: string,
  params?: FetchFutureParams
): Promise<FutureOhlcResponse> {
  const query = new URLSearchParams();
  if (params?.start_date) query.set('start_date', params.start_date);
  if (params?.end_date) query.set('end_date', params.end_date);
  if (params?.timeframe) query.set('timeframe', params.timeframe);
  if (params?.kappa !== undefined) query.set('kappa', String(params.kappa));
  if (params?.quantile_lookback !== undefined) query.set('quantile_lookback', String(params.quantile_lookback));
  if (params?.q_lo_pct !== undefined) query.set('q_lo_pct', String(params.q_lo_pct));
  if (params?.q_hi_pct !== undefined) query.set('q_hi_pct', String(params.q_hi_pct));
  if (params?.kama_period !== undefined) query.set('kama_period', String(params.kama_period));
  const url = `${API_BASE_URL}/future/ohlc-5m/${symbol}?${query}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── RL exits ──────────────────────────────────────────────────────────────

export interface RlTrade {
  entry_bar: number;
  entry_time: string;
  direction: number;       // +1 long, -1 short
  entry_price: number;
  rl_exit_bar: number;
  rl_exit_time: string;
  rl_exit_price: number;
  rl_pnl_pct: number;
  rl_exit_type: string;    // 'agent' | 'sl' | 'max_hold'
  rule_exit_bar: number;
  rule_exit_time: string;
  rule_exit_price: number;
  rule_pnl_pct: number;
  rule_exit_type: string;  // 'bsi' | 'sl' | 'max_hold'
}

export interface RlExitsResponse {
  symbol: string;
  trades: RlTrade[];
}

export interface FetchRlParams extends FetchFutureParams {
  sl_bars?: number;
  max_hold?: number;
  avwap_short?: number;
  avwap_long?: number;
}

export async function fetchRlExits(
  symbol: string,
  params?: FetchRlParams
): Promise<RlExitsResponse> {
  const query = new URLSearchParams();
  if (params?.kappa !== undefined) query.set('kappa', String(params.kappa));
  if (params?.quantile_lookback !== undefined) query.set('quantile_lookback', String(params.quantile_lookback));
  if (params?.q_lo_pct !== undefined) query.set('q_lo_pct', String(params.q_lo_pct));
  if (params?.q_hi_pct !== undefined) query.set('q_hi_pct', String(params.q_hi_pct));
  if (params?.kama_period !== undefined) query.set('kama_period', String(params.kama_period));
  if (params?.sl_bars !== undefined) query.set('sl_bars', String(params.sl_bars));
  if (params?.max_hold !== undefined) query.set('max_hold', String(params.max_hold));
  if (params?.avwap_short !== undefined) query.set('avwap_short', String(params.avwap_short));
  if (params?.avwap_long !== undefined) query.set('avwap_long', String(params.avwap_long));
  const url = `${API_BASE_URL}/future/rl-exits/${symbol}?${query}`;
  const res = await fetch(url);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail?.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}
