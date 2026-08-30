import outcomeBucketsSql from './sql/outcome_buckets.sql?raw';
import featureDiscriminationSql from './sql/feature_discrimination.sql?raw';
import { getConnection, parquetList, registerRunFiles } from './db';
import type {
  DiscriminationRow, EquityRow, RunMeta, SymbolStatRow, TradeRow,
} from './types';

export const DEFAULT_QUANTILES = [0.1, 0.3, 0.7, 0.9];

async function run<T>(sql: string, runs: RunMeta[], params?: unknown[]): Promise<T[]> {
  await registerRunFiles(runs);
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
  const sql = outcomeBucketsSql.replace(/trades_src/g, parquetList(runs, 'trades'));
  return run<TradeRow>(sql, runs, [quantiles]);
}

export function getFeatureDiscrimination(
  runs: RunMeta[], quantiles: number[] = DEFAULT_QUANTILES,
): Promise<DiscriminationRow[]> {
  const sql = featureDiscriminationSql.replace(/trades_src/g, parquetList(runs, 'trades'));
  return run<DiscriminationRow>(sql, runs, [quantiles]);
}
