import { apiGet } from '../api';

export interface DailyPickComponents {
  model_probability: number;
  relative_strength_rank: number;
  trend_rank: number;
  liquidity_rank: number;
  volatility_safety_rank: number;
}

export interface DailyPick {
  rank: number;
  symbol: string;
  score: number;
  model_probability: number;
  signal_versions: string[];
  components: DailyPickComponents;
  risk_flags: string[];
}

export interface DailyPickSnapshot {
  as_of: string;
  generated_at: string;
  model_version: string;
  universe_size: number;
  eligible_size: number;
  candidate_size: number;
  picks: DailyPick[];
}

export interface DailyPickHistory {
  snapshots: DailyPickSnapshot[];
}

export async function getDailyPickHistory(limit = 60): Promise<DailyPickHistory> {
  return apiGet<DailyPickHistory>(`/daily-picks/history?limit=${limit}`);
}
