export interface RunMeta {
  run_id: string;
  name: string;
  created_at: string;
  tags: string[];
  params: Record<string, unknown>;
  notes: string | null;
  data_start: string;
  data_end: string;
  n_symbols: number;
  n_trades: number;
  /** 'mean' = equal-weight composite across independent per-symbol books. */
  equity_agg: 'mean' | 'portfolio';
  metrics: {
    mean_total_return: number | null;
    mean_sharpe: number | null;
    pct_symbols_positive: number | null;
  };
  source: { notebook: string | null; git_sha: string | null; dirty: boolean | null };
  feature_columns: string[];
  files: { trades: string; symbol_stats: string; equity: string };
  schema_version: number;
}

export interface Catalog {
  schema_version: number;
  runs: RunMeta[];
}

export interface TradeRow {
  run_id: string;
  trade_id: number;
  symbol: string;
  /** Epoch milliseconds from Arrow; use lib/experiments/time helpers. */
  entry_dt: number | string;
  entry_price: number;
  exit_dt: number | string | null;
  exit_price: number | null;
  size: number;
  pnl: number;
  ret: number | null;
  net_return: number;
  bars_held: number | null;
  direction: string;
  status: string;
  exit_reason: string | null;
  outcome?: string;
  [feature: string]: unknown;
}

export interface SymbolStatRow {
  run_id: string;
  symbol: string;
  n_trades: number;
  total_return: number | null;
  sharpe: number | null;
  sortino: number | null;
  max_drawdown: number | null;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  exposure: number | null;
}

export interface EquityRow {
  run_id: string;
  /** Epoch milliseconds from Arrow; use lib/experiments/time helpers. */
  dt: number | string;
  value: number;
  returns: number | null;
  drawdown: number | null;
  benchmark_value: number | null;
}

export interface OutcomeRow {
  outcome: string;
  n: number;
  mean_net_return: number | null;
}

export interface DiscriminationRow {
  feature: string;
  n_obs: number;
  coverage: number;
  loser_mean: number | null;
  winner_mean: number | null;
  sd: number | null;
  separation: number | null;
}
