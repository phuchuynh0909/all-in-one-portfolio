import { useQuery } from '@tanstack/react-query';

// ============================================================================
// Strategy Backtest Types (original)
// ============================================================================

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
  y_pred_ensemble?: number;
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
  apply_ml?: boolean;
}

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
    staleTime: 5 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
  });
};


// ============================================================================
// H5 Backtest Results Types (pre-computed from notebook)
// ============================================================================

export interface H5Trade {
  id: number;
  symbol: string;
  size: number;
  entry_timestamp: string;
  avg_entry_price: number;
  entry_fees: number;
  exit_timestamp: string;
  avg_exit_price: number;
  exit_fees: number;
  pnl: number;
  return_pct: number;
  direction: 'Long' | 'Short';
  status: 'Closed' | 'Open';
}

export interface H5Stats {
  symbol: string;
  start?: string;
  end?: string;
  period?: string;
  start_value?: number;
  end_value?: number;
  total_return_pct?: number;
  benchmark_return_pct?: number;
  max_gross_exposure_pct?: number;
  total_fees_paid?: number;
  max_drawdown_pct?: number;
  max_drawdown_duration?: string;
  total_trades?: number;
  total_closed_trades?: number;
  total_open_trades?: number;
  open_trade_pnl?: number;
  win_rate_pct?: number;
  best_trade_pct?: number;
  worst_trade_pct?: number;
  avg_winning_trade_pct?: number;
  avg_losing_trade_pct?: number;
  avg_winning_trade_duration?: string;
  avg_losing_trade_duration?: string;
  sharpe_ratio?: number;
  sortino_ratio?: number;
  calmar_ratio?: number;
  omega_ratio?: number;
  profit_factor?: number;
  expectancy?: number;
}

export interface H5BacktestResultsResponse {
  symbol: string;
  trades: H5Trade[];
  stats: H5Stats | null;
  total_trades: number;
}

export interface BacktestPlotResponse {
  symbol: string;
  start_date: string;
  strategy: string;
  html: string;
  stats?: Record<string, unknown> | null;
}

// Fetch available symbols from watchlist
export const fetchWatchlistSymbols = async (): Promise<string[]> => {
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}/backtest/watchlist`);

  if (!response.ok) {
    throw new Error(`Error ${response.status}: ${await response.text()}`);
  }

  return response.json();
};

// Fetch H5 backtest results (optionally filtered by symbol)
export const fetchH5BacktestResults = async (symbol?: string): Promise<H5BacktestResultsResponse> => {
  const url = new URL(`${import.meta.env.VITE_API_BASE_URL}/backtest/h5/results`);
  if (symbol) {
    url.searchParams.set('symbol', symbol);
  }

  const response = await fetch(url.toString());

  if (!response.ok) {
    throw new Error(`Error ${response.status}: ${await response.text()}`);
  }

  return response.json();
};

// Hook to get watchlist symbols
export const useWatchlistSymbols = () => {
  return useQuery({
    queryKey: ['watchlist-symbols'],
    queryFn: fetchWatchlistSymbols,
    staleTime: 10 * 60 * 1000, // 10 minutes
    gcTime: 60 * 60 * 1000, // 1 hour
  });
};

// Hook to get H5 backtest results
export const useH5BacktestResults = (symbol?: string) => {
  return useQuery({
    queryKey: ['h5-backtest-results', symbol],
    queryFn: () => fetchH5BacktestResults(symbol),
    staleTime: 5 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
  });
};

export const fetchBacktestPlot = async (
  symbol: string,
  startDate?: string,
  strategy?: string,
): Promise<BacktestPlotResponse> => {
  const url = new URL(`${import.meta.env.VITE_API_BASE_URL}/backtest/plot`);
  url.searchParams.set('symbol', symbol);
  if (startDate) {
    url.searchParams.set('start_date', startDate);
  }
  if (strategy) {
    url.searchParams.set('strategy', strategy);
  }

  const response = await fetch(url.toString());

  if (!response.ok) {
    throw new Error(`Error ${response.status}: ${await response.text()}`);
  }

  return response.json();
};

export const useBacktestPlot = (symbol: string, startDate?: string, strategy?: string) => {
  return useQuery({
    queryKey: ['backtest-plot', symbol, startDate, strategy],
    queryFn: () => fetchBacktestPlot(symbol, startDate, strategy),
    staleTime: 5 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
  });
};
