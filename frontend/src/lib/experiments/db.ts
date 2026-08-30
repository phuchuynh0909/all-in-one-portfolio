import type { AsyncDuckDB, AsyncDuckDBConnection } from '@duckdb/duckdb-wasm';
import { experimentFileUrl } from './catalog';
import type { RunMeta } from './types';

let dbPromise: Promise<AsyncDuckDB> | null = null;
let connPromise: Promise<AsyncDuckDBConnection> | null = null;

/**
 * The WASM bundle is ~3 MB, so it is imported dynamically: pages other than
 * Experiments never pay for it.
 */
async function createDb(): Promise<AsyncDuckDB> {
  const duckdb = await import('@duckdb/duckdb-wasm');
  const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
  const workerUrl = URL.createObjectURL(
    new Blob([`importScripts("${bundle.mainWorker!}");`], { type: 'text/javascript' }),
  );
  const worker = new Worker(workerUrl);
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(workerUrl);
  return db;
}

export async function getConnection(): Promise<AsyncDuckDBConnection> {
  if (!dbPromise) dbPromise = createDb();
  if (!connPromise) connPromise = dbPromise.then((db) => db.connect());
  return connPromise;
}

/**
 * Absolute URL for a run's Parquet table.
 *
 * DuckDB's httpfs reads an http(s) URL directly and issues range requests for
 * the column chunks a query touches. Registering files with registerFileURL
 * was tried and does not work here: DuckDB treats the registered name as a
 * filesystem path and globs it, failing with "No files found that match the
 * pattern". Passing the URL straight to read_parquet is both simpler and the
 * only form that resolves.
 */
export function tableUrl(run: RunMeta, table: keyof RunMeta['files']): string {
  return new URL(experimentFileUrl(run.files[table]), window.location.origin).href;
}

/**
 * A read_parquet(...) expression over one or more runs. HTTP has no directory
 * listing, so DuckDB cannot glob — the file list always comes from the catalog.
 */
export function parquetList(runs: RunMeta[], table: keyof RunMeta['files']): string {
  const files = runs.map((r) => `'${tableUrl(r, table)}'`).join(', ');
  return `read_parquet([${files}], union_by_name=true)`;
}
