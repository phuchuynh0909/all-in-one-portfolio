import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import { build } from 'esbuild';

async function load(path) {
  const bundle = await build({ entryPoints: [path], bundle: true, write: false, format: 'esm', platform: 'node' });
  return import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
}
const token = await load('src/lib/auth/token.ts');
const { mergeAlerts } = await load('src/lib/alerts.ts');

 test('blocked storage keeps a token in memory and logout clears it', () => {
  globalThis.localStorage = {
    getItem() { return null; },
    setItem() { throw new Error('storage blocked'); },
    removeItem() { throw new Error('storage blocked'); },
  };
  token.setToken('test-token');
  assert.equal(token.getToken(), 'test-token');
  token.clearToken();
  assert.equal(token.getToken(), null);
});

test('alerts with the same timestamp but different symbols are retained', () => {
  const old = [{ symbol: 'AAA', ts: 1000 }];
  const next = [{ symbol: 'AAA', ts: 1000 }, { symbol: 'BBB', ts: 1000 }, { symbol: 'BBB', ts: 1000 }];
  assert.deepEqual(mergeAlerts(old, next).map(({ symbol }) => symbol), ['AAA', 'BBB']);
});

test('worker sends the token and signals unauthorized responses', async () => {
  const script = await readFile('public/alert-worker.js', 'utf8');
  let onMessage;
  let status = 200;
  const requests = [];
  const messages = [];
  const self = { addEventListener(_name, handler) { onMessage = handler; }, postMessage(message) { messages.push(message); } };
  const fetch = async (url, options) => {
    requests.push({ url, options });
    return { status, ok: status === 200, headers: { get: () => 'application/json' }, json: async () => [] };
  };
  runInNewContext(script, { self, fetch, URLSearchParams, console, setInterval: () => 1, clearInterval() {} });
  await onMessage({ data: { type: 'START_POLLING', payload: { apiUrl: '/api/v1', token: 'token-123', interval: 1000, params: { limit: 10 } } } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests[0].options.headers.Authorization, 'Bearer token-123');
  assert.ok(messages.some((message) => message.type === 'ALERTS_FETCHED'));
  status = 401;
  await onMessage({ data: { type: 'FETCH_NOW', payload: { limit: 10 } } });
  assert.ok(messages.some((message) => message.type === 'UNAUTHORIZED'));
});
