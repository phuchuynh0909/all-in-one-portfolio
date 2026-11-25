import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';

const bundle = await build({ entryPoints: ['src/lib/services/report.ts'], bundle: true, write: false,
  format: 'esm', platform: 'node', define: { 'import.meta.env.VITE_API_BASE_URL': '"/api/v1"' } });
const { triggerReportRag, RAG_IN_PROGRESS } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('all nonterminal report states continue polling', () => {
  for (const status of ['PENDING', 'PARSING', 'PARSED', 'SUMMARIZING', 'EMBEDDING']) {
    assert.ok(RAG_IN_PROGRESS.includes(status));
  }
  assert.ok(!RAG_IN_PROGRESS.includes('EMBEDDED'));
  assert.ok(!RAG_IN_PROGRESS.includes('FAILED'));
});

test('an uncertain request reuses its key even when storage is blocked', async () => {
  globalThis.sessionStorage = { getItem() { throw Error('blocked'); } };
  const keys = [];
  globalThis.fetch = async (_url, options) => {
    keys.push(options.headers['Idempotency-Key']);
    if (keys.length === 1) throw Error('connection lost');
    return { ok: true, json: async () => ({ status: 'PENDING' }) };
  };
  await assert.rejects(triggerReportRag(12));
  await triggerReportRag(12);
  assert.equal(keys[0], keys[1]);
  assert.match(keys[0], /^[0-9a-f-]{36}$/);
});

test('a lost success response body preserves the retry key', async () => {
  const storage = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  };
  const keys = [];
  globalThis.fetch = async (_url, options) => {
    keys.push(options.headers['Idempotency-Key']);
    return { ok: true, json: async () => {
      if (keys.length === 1) throw Error('body interrupted');
      return { status: 'PENDING' };
    } };
  };
  await assert.rejects(triggerReportRag(23));
  await triggerReportRag(23);
  assert.equal(keys[0], keys[1]);
  assert.equal(storage.size, 0);
});
