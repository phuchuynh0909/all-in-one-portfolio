import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';

const bundle = await build({ entryPoints: ['src/lib/portfolio/metrics.ts'], bundle: true, write: false, format: 'esm', platform: 'node' });
const { computePortfolioMetrics } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('realized trading profit uses closing price and subtracts fees', () => {
  const sell = (price, close_price, quantity, fees) => ({ transaction_type: 'sell', price, close_price, quantity, fees });
  assert.equal(computePortfolioMetrics([], [sell(10, 15, 2, 1)]).realizedPl, 9);
  assert.equal(computePortfolioMetrics([], [sell(10, 5, 2, 1)]).realizedPl, -11);
  assert.equal(computePortfolioMetrics([], [sell(10, null, 2, 1)]).realizedPl, -1);
});
