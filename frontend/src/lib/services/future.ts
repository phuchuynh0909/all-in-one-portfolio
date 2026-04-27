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
  if (params?.kappa !== undefined) query.set('kappa', String(params.kappa));
  if (params?.quantile_lookback !== undefined) query.set('quantile_lookback', String(params.quantile_lookback));
  if (params?.q_lo_pct !== undefined) query.set('q_lo_pct', String(params.q_lo_pct));
  if (params?.q_hi_pct !== undefined) query.set('q_hi_pct', String(params.q_hi_pct));
  if (params?.kama_period !== undefined) query.set('kama_period', String(params.kama_period));
  const url = `${import.meta.env.VITE_API_BASE_URL}/future/ohlc-5m/${symbol}?${query}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
