export interface FutureOhlcResponse {
  symbol: string;
  timestamps: string[];
  ohlc: { open: number[]; high: number[]; low: number[]; close: number[] };
  volume: { total: number[]; buy: number[]; sell: number[] };
  indicators: {
    bsi: (number | null)[];
    bsi_rf: (number | null)[];
    bsi_norm: (number | null)[];
    kama_21: (number | null)[];
    kama_200: (number | null)[];
  };
}

export async function fetchFutureOhlc(
  symbol: string,
  params?: { start_date?: string; end_date?: string; kappa?: number; hp_period?: number; lp_period?: number }
): Promise<FutureOhlcResponse> {
  const query = new URLSearchParams();
  if (params?.start_date) query.set('start_date', params.start_date);
  if (params?.end_date) query.set('end_date', params.end_date);
  if (params?.kappa !== undefined) query.set('kappa', String(params.kappa));
  if (params?.hp_period !== undefined) query.set('hp_period', String(params.hp_period));
  if (params?.lp_period !== undefined) query.set('lp_period', String(params.lp_period));
  const url = `${import.meta.env.VITE_API_BASE_URL}/future/ohlc-5m/${symbol}?${query}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
