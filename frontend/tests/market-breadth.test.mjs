import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';

const bundle = await build({
  entryPoints: ['src/lib/tv/breadth.ts'], bundle: true, write: false,
  format: 'esm', platform: 'node', define: { 'import.meta.env': '{}' },
});
const { MarketBreadthStore, marketBreadthIndicatorsGetter, BREADTH_STUDIES } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('Net New Highs renders aligned positive, negative, zero and unavailable histogram values', async () => {
  const timestamps = ['2026-11-16', '2026-11-17', '2026-11-18', '2026-11-19'];
  const breadth = {
    timestamps, ad_line: [0, 1, 2, 3], mcclellan_summation: [0, 1, 2, 3],
    rsi_below_30: [0, 0, 0, 0], rsi_between_30_70: [1, 1, 1, 1], rsi_above_70: [0, 0, 0, 0], rsi_period: 14,
    new_highs: [null, 7, 2, 3], new_lows: [null, 2, 7, 3], net_new_highs: [null, 5, -5, 0],
    pct_above_ma_20: [null, 50, 0, 100],
    pct_above_ma_50: [null, null, 25, 75],
    pct_above_ma_200: [null, null, null, 33.333],
  };
  const store = new MarketBreadthStore(breadth, {
    timestamps, timeseries: { open: [10, 10, 10, 10], high: [11, 11, 11, 11], low: [9, 9, 9, 9], close: [10, 10, 10, 10], volume: [1, 1, 1, 1] },
  });
  const studies = await marketBreadthIndicatorsGetter(store)({ Std: { time: (context) => context.time } });
  const definition = studies.find((study) => study.name === BREADTH_STUDIES.netNewHighs);
  assert.ok(definition);
  assert.equal(definition.metainfo.defaults.styles.net.plottype, 1);
  assert.equal(definition.metainfo.defaults.bands[0].value, 0);
  const study = new definition.constructor();
  const values = store.bars.map((bar) => study.main(bar, () => 0));
  assert.ok(Number.isNaN(values[0][0]));
  assert.deepEqual(values.slice(1), [[5, 0], [-5, 1], [0, 0]]);
  assert.ok(study.main({ time: Date.UTC(2025, 0, 1) }, () => 0).every(Number.isNaN));
  for (const color of Object.values(definition.metainfo.defaults.palettes.palette0.colors)) {
    assert.equal(typeof color.width, 'number');
    assert.equal(typeof color.style, 'number');
  }
  const ma = studies.find((study) => study.name === BREADTH_STUDIES.aboveMovingAverages);
  assert.ok(ma);
  assert.ok(!studies.some((study) => study.name === 'RSI Distribution'));
  assert.equal(ma.metainfo.plots.length, 3);
  assert.deepEqual(Object.values(ma.metainfo.styles).map((style) => style.title),
    ['% Above SMA 20', '% Above SMA 50', '% Above SMA 200']);
  assert.deepEqual(ma.metainfo.defaults.bands.map((band) => band.value), [0, 50, 100]);
  const maStudy = new ma.constructor();
  assert.ok(maStudy.main(store.bars[0]).every(Number.isNaN));
  const second = maStudy.main(store.bars[1]);
  assert.equal(second[0], 50);
  assert.ok(Number.isNaN(second[1]) && Number.isNaN(second[2]));
  assert.deepEqual(maStudy.main(store.bars[3]), [100, 75, 33.333]);
});
