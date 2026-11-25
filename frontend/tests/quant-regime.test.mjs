import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
const bundle = await build({ entryPoints: ['src/lib/tv/quantRegime.ts'], bundle: true, write: false, format: 'esm', platform: 'node' });
const { calculateRegime, mannKendall, hurstExponent, varianceRatio, buildQuantRegimeStudy, syncRegimeMetricVisibility, addRegimeLegend, REGIME_PARAMS } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const bars = Array.from({ length: 300 }, (_, i) => {
  const close = 100 * Math.exp(0.003 * i + 0.01 * Math.sin(i * 0.7));
  return { time: i + 1, high: close * 1.005, low: close * 0.995, close };
});
const near = (actual, expected, tolerance = 1e-8) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} != ${expected}`);
const context = (bar = {}) => ({ ...bar, new_var: () => ({ get: () => NaN }) });
test('guide is anchored to the indicator pane without becoming a saved user drawing', () => {
  let options;
  const id = addRegimeLegend({ createAnchoredShape: (position, value) => {
    assert.ok(position.x > 0 && position.x < 1 && position.y > 0 && position.y < 1);
    options = value;
    return 'legend';
  } }, 'regime-study');
  assert.equal(id, 'legend');
  assert.equal(options.ownerStudyId, 'regime-study');
  assert.equal(options.disableSave, true);
  assert.equal(options.disableSelection, true);
  for (const label of ['−1  Downtrend', '0  Mixed / unclear', '+1  Uptrend', '2  Range / mean reversion']) assert.ok(options.text.includes(label));
});
test('Mann–Kendall handles increasing, decreasing and tied prices', () => {
  assert.equal(mannKendall([1, 2, 3, 4, 5, 6, 7, 8]).tau, 1);
  assert.ok(mannKendall([8, 7, 6, 5, 4, 3, 2, 1]).p < 0.05);
  near(mannKendall([2, 2, 2, 2]).p, 1);
  assert.equal(mannKendall([2, 2, 2, 2]).tau, 0);
});
test('warmup, flat prices, invalid prices and invalid lag stay unavailable', () => {
  assert.ok(Number.isNaN(calculateRegime(bars.slice(0, 251)).regime));
  assert.ok(Number.isNaN(calculateRegime(bars.map((bar) => ({ ...bar, close: 10, high: 10, low: 10 }))).regime));
  assert.ok(Number.isNaN(hurstExponent(Array(126).fill(0))));
  assert.ok(Number.isNaN(varianceRatio([1, 0, 2], 2).vr));
  assert.ok(Number.isNaN(varianceRatio(bars.map((bar) => bar.close), 299).vr));
});
test('metrics are scale invariant; trends and range get distinct states', () => {
  const result = calculateRegime(bars);
  // Independent NumPy/SciPy reference, using the sample's formulas and hurst scales.
  near(result.hurst, 1.0744068107010842);
  near(result.vr, 0.10857613433692286);
  near(result.vrZ, -4.358201597269716);
  near(result.vrP, 1.3113556419632806e-05, 1e-7);
  const scaled = calculateRegime(bars.map((bar) => ({ ...bar, high: bar.high * 1000, low: bar.low * 1000, close: bar.close * 1000 })));
  for (const key of Object.keys(result)) near(scaled[key], result[key]);
  assert.equal(result.regime, 1);
  const falling = bars.map((bar, i) => {
    const close = 100 * Math.exp(-0.003 * i + 0.01 * Math.sin(i * 0.7));
    return { ...bar, close, high: close * 1.001, low: close * 0.999 };
  });
  assert.equal(calculateRegime(falling).regime, -1);
  assert.equal(calculateRegime(bars.map((bar, i) => ({ ...bar, close: 100 + (i % 2 ? 1 : -1), high: 102, low: 98 }))).regime, 2);
});
test('study replaces live bars, resets history, and matches causal batch calculations', () => {
  const pine = { Std: Object.fromEntries(['time', 'high', 'low', 'close'].map((key) => [key, (bar) => bar[key]])) };
  const study = new (buildQuantRegimeStudy(pine).constructor)();
  const input = (index) => Object.values(REGIME_PARAMS)[index];
  study.init({}, input);
  assert.ok(study.main(context(), input).every(Number.isNaN));
  for (const bar of bars) study.main(context(bar), input);
  const updated = { ...bars.at(-1), close: bars.at(-1).close + 1 };
  const expected = calculateRegime([...bars.slice(0, -1), updated]);
  const actual = study.main(context(updated), input);
  Object.values(expected).forEach((value, i) => near(actual[i], value));
  assert.deepEqual(study.main(context(updated), input), actual);
  assert.ok(study.main(context(bars[0]), input).slice(0, 8).every(Number.isNaN));
  for (const bar of bars.slice(1)) study.main(context(bar), input);
  near(study.main(context(bars.at(-1)), input)[4], calculateRegime(bars).hurst);
});

test('study requests warmup history before TradingView supplies visible bars', () => {
  const pine = { Std: Object.fromEntries(['time', 'high', 'low', 'close'].map((key) => [key, (bar) => bar[key]])) };
  const study = new (buildQuantRegimeStudy(pine).constructor)();
  const input = (index) => Object.values(REGIME_PARAMS)[index];
  let requestedDepth = 0;
  const historyContext = {
    new_var: () => ({ get: (depth) => { requestedDepth = Math.max(requestedDepth, depth); return NaN; } }),
  };
  study.init(historyContext, input);
  // TradingView probes with no OHLC values to discover required history.
  study.main(historyContext, input);
  assert.ok(requestedDepth >= 251, 'regime must request 251 warmup bars during the initial probe');
  const visibleBars = 20;
  const start = Math.max(0, bars.length - visibleBars - requestedDepth);
  let result;
  for (const bar of bars.slice(start)) result = study.main({ ...historyContext, ...bar }, input);
  assert.ok(Number.isFinite(result[0]), 'regime must finish loading even with fewer than 252 visible bars');
});

test('regime palette supplies the stroke fields required by TradingView rendering', () => {
  const definition = buildQuantRegimeStudy({});
  for (const color of Object.values(definition.metainfo.defaults.palettes.regimes.colors)) {
    assert.equal(typeof color.color, 'string');
    assert.equal(typeof color.width, 'number', 'colorer requires width to render and read crosshair values');
    assert.equal(typeof color.style, 'number', 'colorer requires line style');
  }
});

test('only regime displays by default; metric groups toggle together and survive settings restore', () => {
  const definition = buildQuantRegimeStudy({});
  const meta = definition.metainfo;
  const styles = structuredClone(meta.defaults.styles);
  for (const [id, style] of Object.entries(styles)) {
    assert.equal(style.display, id === 'regime' ? 15 : 0);
    assert.equal(style.visible, id === 'regime');
  }
  const settings = { ...meta.defaults.inputs };
  assert.equal(meta.inputs.filter((input) => input.type === 'bool').length, 4);
  let calls = 0;
  const study = {
    getInputValues: () => Object.entries(settings).map(([id, value]) => ({ id, value })),
    getStyleValues: () => ({ styles }),
    applyOverrides: (overrides) => {
      calls++;
      for (const [key, value] of Object.entries(overrides)) {
        const split = key.lastIndexOf('.');
        const title = key.slice(0, split);
        const id = Object.keys(meta.styles).find((id) => meta.styles[id].title === title);
        assert.ok(id, `override must use an existing plot title: ${title}`);
        styles[id][key.slice(split + 1)] = value;
      }
    },
  };
  // Old saved layouts may have every diagnostic plot enabled.
  for (const style of Object.values(styles)) Object.assign(style, { display: 15, visible: true });
  syncRegimeMetricVisibility(study);
  assert.equal(styles.chop.display, 0);
  settings.show_mk = true;
  settings.show_vr = true;
  syncRegimeMetricVisibility(study);
  for (const id of ['tau', 'mkP', 'vr', 'vrZ', 'vrP']) {
    assert.equal(styles[id].display, 15);
    assert.equal(styles[id].visible, true);
  }
  assert.equal(styles.hurst.display, 0);
  const savedCalls = calls;
  syncRegimeMetricVisibility(study);
  assert.equal(calls, savedCalls, 'unchanged properties must not trigger recursive updates');
  settings.show_mk = false;
  syncRegimeMetricVisibility(study);
  assert.equal(styles.tau.display, 0);
  assert.equal(styles.mkP.display, 0);
  assert.equal(styles.vr.display, 15);
  assert.equal(styles.regime.display, 15);
});
