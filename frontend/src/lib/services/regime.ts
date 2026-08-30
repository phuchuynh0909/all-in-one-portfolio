import { API_BASE_URL } from '../api';
export interface MarkovKamaData {
  regime_code: number[];
  low_var_prob: (number | null)[];
  high_var_prob: (number | null)[];
  kama: (number | null)[];
}

export interface MSRegimeData {
  regime: number[];
  regime_prob: (number | null)[];
}

export interface YZPercentileData {
  yz_vol: (number | null)[];
  pct_rank: (number | null)[];
}

export interface TicaHmmData {
  regime_code:  number[];  // 0..k-1 Viterbi
  regime_label: string[];  // "Risk-On" | "Caution" | "Risk-Off"
}

export interface RegimeResponse {
  symbol: string;
  timestamps: string[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  markov_kama: MarkovKamaData;
  ms_regime: MSRegimeData;
  yz_percentile: YZPercentileData;
  tica_hmm: TicaHmmData;
}

export interface RegimeRequest {
  start_date?: string;
  end_date?: string;
}

export const fetchRegime = async (
  symbol: string,
  params: RegimeRequest = {},
): Promise<RegimeResponse> => {
  const response = await fetch(
    `${API_BASE_URL}/regime/${symbol}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    },
  );
  if (!response.ok) {
    throw new Error(`Error ${response.status}: ${await response.text()}`);
  }
  return response.json();
};
