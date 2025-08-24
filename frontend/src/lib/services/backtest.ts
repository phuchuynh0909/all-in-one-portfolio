export interface Trade {
  symbol: string;
  date: string;
  entry_price: number;
  pnl: number;
  type: 'open_trades' | 'closed_trades';
  entry_idx: number;
  exit_idx?: number;
  close_date?: string;
  trading_days?: number;
  metadata: Record<string, any>;
  y_pred_xgb?: number;
  y_pred_lgbm?: number;
  y_pred_catboost?: number;
  msr_rank_10?: number;
}

export interface ExecutionTime {
  total_seconds: number;
  data_loading_seconds: number;
  strategy_seconds: number;
  feature_building_seconds: number;
  prediction_seconds: number;
}

export interface BacktestResponse {
  open_trades: Trade[];
  closed_trades: Trade[];
  execution_time: ExecutionTime;
}

export interface BacktestRequest {
  strategy: string;
  start_date: string;
  symbols?: string[];
}

import { useQuery } from '@tanstack/react-query';

export const fetchBacktest = async (params: BacktestRequest): Promise<BacktestResponse> => {
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}/backtest`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(params),
  });

  if (!response.ok) {
    throw new Error(`Error ${response.status}: ${await response.text()}`);
  }

  return response.json();
};

export const useBacktest = (params: BacktestRequest) => {
  return useQuery({
    queryKey: ['backtest', params],
    queryFn: () => fetchBacktest(params),
    staleTime: 5 * 60 * 1000, // Data considered fresh for 5 minutes
    cacheTime: 30 * 60 * 1000, // Cache kept for 30 minutes
  });
};
