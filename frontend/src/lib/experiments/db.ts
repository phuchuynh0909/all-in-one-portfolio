import type { AsyncDuckDB, AsyncDuckDBConnection } from '@duckdb/duckdb-wasm';
import { experimentFileUrl } from './catalog';
import type { RunMeta } from './types';

let dbPromise: Promise<AsyncDuckDB> | null = null;
let connPromise: Promise<AsyncDuckDBConnection> | null = null;
const registered = new Set<string>();

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
 * Registers each run's Parquet by URL so DuckDB fetches byte ranges rather
 * than whole files. HTTP has no directory listing, so DuckDB cannot glob —
 * the file list always comes from the catalog.
 */
export async function registerRunFiles(runs: RunMeta[]): Promise<void> {
  const duckdb = await import('@duckdb/duckdb-wasm');
  if (!dbPromise) dbPromise = createDb();
  const db = await dbPromise;
  for (const run of runs) {
    for (const rel of Object.values(run.files)) {
      if (registered.has(rel)) continue;
      await db.registerFileURL(rel, experimentFileUrl(rel), duckdb.DuckDBDataProtocol.HTTP, false);
      registered.add(rel);
    }
  }
}

export function parquetList(runs: RunMeta[], table: keyof RunMeta['files']): string {
  const files = runs.map((r) => `'${r.files[table]}'`).join(', ');
  return `read_parquet([${files}], union_by_name=true)`;
}
