import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import test, { after } from 'node:test';
import { build } from 'esbuild';

const result = await build({ entryPoints: ['src/lib/services/sectorStrength.ts'], bundle: true, write: false, format: 'esm', platform: 'node', define: { 'import.meta.env': '{}' } });
const directory = await mkdtemp(join(tmpdir(), 'sector-strength-tests-'));
after(() => rm(directory, { recursive: true, force: true }));
const path = join(directory, 'strength.mjs');
await writeFile(path, result.outputFiles[0].text);
const { fetchSectorSymbolStrength, fetchSymbolRelativeStrength } = await import(pathToFileURL(path).href);

const row = (symbol, ratio = 105, momentum = 102, unavailable = null) => ({ symbol, ratio: [ratio - 1, ratio], momentum: [momentum - 1, momentum], rs: [ratio - 101, ratio - 100], unavailable });
const batch = (rows) => ({ benchmark: 'VNINDEX', window: 20, momentum_window: 5, timeframe: 'daily', dates: ['2026-11-12', '2026-11-13'], rows });

async function withApi(responses, run) {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    const path = new URL(url).pathname.replace('/api/v1', '');
    calls.push({ path, options });
    assert.ok(Object.hasOwn(responses, path), `Unexpected API call: ${path}`);
    const body = responses[path];
    return body instanceof Error
      ? { ok: false, status: 422, json: async () => ({ detail: body.message }) }
      : { ok: true, json: async () => body };
  };
  try { await run(calls); } finally { globalThis.fetch = originalFetch; }
}

test('one batch request sends every selected-sector symbol and the requested windows', async () => {
  await withApi({
    '/sector/symbols/3/1': [{ symbol: 'UP' }, { symbol: 'DOWN' }, { symbol: 'MISSING' }],
    '/timeseries/symbols/relative-strength': batch([row('UP'), row('DOWN', 95, 98), row('MISSING', null, null, 'No price history')]),
  }, async (calls) => {
    const result = await fetchSectorSymbolStrength(3, 1, { window: 20, momentum_window: 5, tail: 8, timeframe: 'daily' });
    assert.equal(calls.length, 2);
    assert.deepEqual(JSON.parse(calls[1].options.body), { symbols: ['UP', 'DOWN', 'MISSING'], window: 20, momentum_window: 5, tail: 8, timeframe: 'daily' });
    assert.deepEqual(result.rows.map((row) => row.name), ['UP', 'DOWN']);
    assert.deepEqual(result.rows[0].ratio, [104, 105]);
    assert.deepEqual(result.rows[0].momentum, [101, 102]);
    assert.deepEqual(result.unavailable, [{ symbol: 'MISSING', reason: 'No price history' }]);
    assert.deepEqual(result.dates, ['2026-11-12', '2026-11-13']);
  });
});

test('switching sectors requests the new symbols, and an empty sector skips the batch', async () => {
  await withApi({
    '/sector/symbols/4/2': [{ symbol: 'OTHER' }],
    '/sector/symbols/4/3': [],
    '/timeseries/symbols/relative-strength': batch([row('OTHER')]),
  }, async (calls) => {
    assert.equal((await fetchSectorSymbolStrength(4, 2)).rows[0].name, 'OTHER');
    assert.deepEqual(JSON.parse(calls[1].options.body), { symbols: ['OTHER'] });
    assert.deepEqual((await fetchSectorSymbolStrength(4, 3)).rows, []);
    assert.equal(calls.length, 3);
  });
});

test('batch API can be used independently of a sector and preserves server errors', async () => {
  await withApi({ '/timeseries/symbols/relative-strength': batch([row('FPT')]) }, async (calls) => {
    assert.equal((await fetchSymbolRelativeStrength(['FPT'], { benchmark: 'VN30', window: 50 })).rows[0].symbol, 'FPT');
    assert.equal(JSON.parse(calls[0].options.body).benchmark, 'VN30');
  });
  await withApi({ '/timeseries/symbols/relative-strength': new Error('No price history for benchmark UNKNOWN') }, async () => {
    await assert.rejects(fetchSymbolRelativeStrength(['FPT'], { benchmark: 'UNKNOWN' }), /No price history for benchmark UNKNOWN/);
  });
});
