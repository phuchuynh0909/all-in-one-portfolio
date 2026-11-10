import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';

const bundle = await build({ entryPoints: ['src/lib/format.ts'], bundle: true, write: false, format: 'esm', platform: 'node' });
const { formatLocalDate } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('formats the selected calendar day in the local timezone', () => {
  for (const timezone of ['Asia/Ho_Chi_Minh', 'America/Los_Angeles']) {
    process.env.TZ = timezone;
    assert.equal(formatLocalDate(new Date(2026, 10, 9)), '2026-11-09');
  }
});
