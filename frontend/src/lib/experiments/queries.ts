import outcomeBucketsSql from './sql/outcome_buckets.sql?raw';
import featureDiscriminationSql from './sql/feature_discrimination.sql?raw';
import { getConnection, parquetList } from './db';
import type {
  DiscriminationRow, EquityRow, RunMeta, SymbolStatRow, TradeRow,
} from './types';

export const DEFAULT_QUANTILES = [0.1, 0.3, 0.7, 0.9];

/**
 * Substitutes the quantile cut points into the shared SQL.
 *
 * The .sql files declare them as a `?::DOUBLE[]` bound parameter, which native
 * DuckDB (and therefore the pytest suite) binds happily. DuckDB-WASM cannot
 * bind a JS array to a LIST parameter — it fails with "Invalid column type
 * encountered for argument 0" — so the browser inlines a literal instead. The
 * values are validated finite numbers, never free text.
 */
function withQuantiles(sql: string, quantiles: number[]): string {
  if (quantiles.length !== 4 || !quantiles.every((q) => Number.isFinite(q))) {
    throw new Error(`Expected 4 finite quantiles, got ${JSON.stringify(quantiles)}`);
  }
  const literal = `[${quantiles.map((q) => Number(q)).join(', ')}]`;
  return sql.replace('?::DOUBLE[]', `${literal}::DOUBLE[]`);
}

/**
 * A single DuckDB-WASM connection cannot serve concurrent queries: two
 * simultaneous prepared statements deadlock and never settle, which shows up
 * as tabs that spin forever with no error. Every query is therefore queued.
 */
let queue: Promise<unknown> = Promise.resolve();

function serialize<T>(task: () => Promise<T>): Promise<T> {
  const next = queue.then(task, task);
  queue = next.catch(() => undefined);
  return next;
}

async function run<T>(sql: string, runs: RunMeta[], params?: unknown[]): Promise<T[]> {
  return serialize(async () => {
    void runs; // file URLs are already baked into `sql` by parquetList
    const conn = await getConnection();
    if (!params?.length) {
      return (await conn.query(sql)).toArray().map((r) => r.toJSON() as T);
    }
    const stmt = await conn.prepare(sql);
    try {
      return (await stmt.query(...params)).toArray().map((r) => r.toJSON() as T);
    } finally {
      await stmt.close();
    }
  });
}

export function getEquity(runMeta: RunMeta): Promise<EquityRow[]> {
  return run<EquityRow>(
    `SELECT * FROM ${parquetList([runMeta], 'equity')} ORDER BY dt`, [runMeta],
  );
}

export function getSymbolStats(runMeta: RunMeta): Promise<SymbolStatRow[]> {
  return run<SymbolStatRow>(
    `SELECT * FROM ${parquetList([runMeta], 'symbol_stats')} ORDER BY total_return DESC NULLS LAST`,
    [runMeta],
  );
}

export function getTrades(runMeta: RunMeta): Promise<TradeRow[]> {
  return run<TradeRow>(
    `SELECT * FROM ${parquetList([runMeta], 'trades')} ORDER BY entry_dt`, [runMeta],
  );
}

/** Trades with an `outcome` column, bucketed by net_return quantiles. */
export function getOutcomeBuckets(
  runs: RunMeta[], quantiles: number[] = DEFAULT_QUANTILES,
): Promise<TradeRow[]> {
  const sql = withQuantiles(
    outcomeBucketsSql.replace(/trades_src/g, parquetList(runs, 'trades')), quantiles,
  );
  return run<TradeRow>(sql, runs);
}

/** True when at least one pooled run logged entry features. */
export function hasFeatures(runs: RunMeta[]): boolean {
  return runs.some((r) => (r.feature_columns ?? []).length > 0);
}

export function getFeatureDiscrimination(
  runs: RunMeta[], quantiles: number[] = DEFAULT_QUANTILES,
): Promise<DiscriminationRow[]> {
  // UNPIVOT ... ON COLUMNS('^feat_') is a hard Binder Error when nothing
  // matches, so a run logged without features must skip the query rather than
  // surface a SQL error. Runs that mix featured and unfeatured pool fine:
  // union_by_name fills NULLs and the coverage column reports the shortfall.
  if (!hasFeatures(runs)) return Promise.resolve([]);
  const sql = withQuantiles(
    featureDiscriminationSql.replace(/trades_src/g, parquetList(runs, 'trades')), quantiles,
  );
  return run<DiscriminationRow>(sql, runs);
}
