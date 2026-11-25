import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
const bundle = await build({ entryPoints: ['src/lib/tv/breadthToolkit.ts'], bundle: true, write: false, format: 'esm', platform: 'node', define: { 'import.meta.env': '{}' } });
const { TOOLKIT_DEFAULTS, TOOLKIT_METHODS, calculateBreadthToolkit, buildBreadthToolkit, drawToolkitDivergences, toolkitSettings, toolkitInterpretation } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const data = (length = 25) => ({ advances: Array(length).fill(3), declines: Array(length).fill(1), advancing_volume: Array(length).fill(100), declining_volume: Array(length).fill(50) });
const settings = (method, overrides = {}) => ({ ...TOOLKIT_DEFAULTS, method: TOOLKIT_METHODS[method], ...overrides });
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test('method tooltips explain units and use the same active thresholds as calculations', () => {
  TOOLKIT_METHODS.forEach((_, i) => {
    const config = settings(i);
    const { top, bottom } = calculateBreadthToolkit(data(), config);
    const text = toolkitInterpretation(config);
    assert.ok(text.includes(`Default levels: upper ${top}, lower ${bottom}.`));
    assert.ok(text.includes('Unchanged stocks are excluded'));
    const custom = { ...config, fixed_levels: true, top: 88, bottom: 12 };
    assert.ok(toolkitInterpretation(custom).includes('Custom levels: upper 88, lower 12.'));
  });
  assert.match(toolkitInterpretation(settings(0)), /10-day average.*ratio/);
  assert.match(toolkitInterpretation(settings(1)), /20-day average.*ratio/);
  assert.match(toolkitInterpretation(settings(2)), /last 10 trading intervals/);
  assert.match(toolkitInterpretation(settings(3)), /raw score, not a percentage/);
  assert.match(toolkitInterpretation(settings(3)), /does not generate Thrust labels/);
  assert.match(toolkitInterpretation(settings(4)), /directional issues/);
  assert.match(toolkitInterpretation(settings(5)), /directional volume/);
});

test('all six formulas and automatic thresholds match the supplied Pine definitions', () => {
  const expected = [3, 3, 75, 250, 75, 100 * 100 / 150];
  const tops = [1.97, 1.72, 61.5, 0, 73.66, 77.88];
  const bottoms = [0.5, 0.5, 40, 0, 19.05, 16.41];
  TOOLKIT_METHODS.forEach((_, i) => {
    const result = calculateBreadthToolkit(data(), settings(i));
    near(result.values.at(-1), expected[i]);
    assert.equal(result.top, tops[i]); assert.equal(result.bottom, bottoms[i]);
    assert.equal(result.base, i < 2 ? 1 : i === 3 ? 0 : 50);
    assert.ok(result.values.slice(0, [9, 19, 0, 0, 4, 4][i]).every((value) => value === null));
  });
});
test('Zweig thrust needs a recent sub-40 reading and upper-level crossover', () => {
  const input = data(18);
  input.advances = [30, 30, 30, ...Array(15).fill(100)];
  input.declines = input.advances.map((value) => 100 - value);
  const result = calculateBreadthToolkit(input);
  result.values.forEach((value, i) => near(value, i < 3 ? 30 : 100 - 70 * (9 / 11) ** (i - 2)));
  assert.equal(result.thrusts.filter(Boolean).length, 1);
  const signal = result.thrusts.indexOf(true);
  assert.ok(result.values[signal] > 61.5 && result.values[signal - 1] <= 61.5);
  assert.ok(calculateBreadthToolkit(data()).thrusts.every((value) => !value));
  assert.ok(calculateBreadthToolkit(data(), settings(3)).thrusts.every((value) => !value));
});
test('Whaley marks threshold excursions and fixed levels replace defaults', () => {
  const input = data(6); input.advances.fill(9); input.declines.fill(1);
  assert.deepEqual(calculateBreadthToolkit(input, settings(4)).thrusts, [false, false, false, false, true, true]);
  const fixed = calculateBreadthToolkit(input, settings(4, { fixed_levels: true, top: 95, bottom: 5 }));
  assert.equal(fixed.top, 95); assert.equal(fixed.bottom, 5);
  assert.ok(fixed.thrusts.every((value) => !value));
});
test('zero denominators and incomplete volume never become false thrusts', () => {
  const input = data(); input.declines.fill(0);
  assert.ok(calculateBreadthToolkit(input, settings(0)).values.every((value) => value === null));
  input.advances.fill(0);
  assert.ok(calculateBreadthToolkit(input).values.every((value) => value === null));
  input.advancing_volume.fill(null);
  for (const method of [3, 5]) {
    const result = calculateBreadthToolkit(input, settings(method));
    assert.ok(result.values.every((value) => value === null));
    assert.ok(result.thrusts.every((value) => !value));
  }
});
const pivotData = (values) => ({ advances: values.map(() => 1), declines: values.map(() => 1), advancing_volume: values.map((value) => 100 + value), declining_volume: values.map(() => 100) });
test('divergences use confirmed pivots and index prices without future leakage', () => {
  const config = settings(3, { divergence_length: 1 });
  assert.deepEqual(calculateBreadthToolkit(pivotData([1, 5, 2, 4, 1]), config, [9, 10, 11, 12, 13]).divergences,
    [{ from: 1, to: 3, confirmedAt: 4, bullish: false }]);
  assert.deepEqual(calculateBreadthToolkit(pivotData([1, 5, 2, 4]), config, [9, 10, 11, 12]).divergences, []);
  assert.deepEqual(calculateBreadthToolkit(pivotData([-1, -5, -2, -4, -1]), config, [11, 10, 9, 8, 7]).divergences,
    [{ from: 1, to: 3, confirmedAt: 4, bullish: true }]);
  assert.deepEqual(calculateBreadthToolkit(pivotData([1, 5, 2, 4, 1]), { ...config, divergences: false }, [9, 10, 11, 12, 13]).divergences, []);
});
test('native study changes methods; divergence drawings belong to the study pane', () => {
  const timestamps = ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06'];
  const store = { breadth: { ...pivotData([1, 5, 2, 4, 1]), timestamps }, bars: timestamps.map((date, i) => ({ time: Date.parse(date), close: 9 + i })), indexAt: (time) => store.bars.findIndex((bar) => bar.time === time) };
  const definition = buildBreadthToolkit({ Std: { time: (bar) => bar.time } }, store);
  const study = new definition.constructor();
  let config = settings(3, { divergence_length: 1 });
  const callback = (i) => Object.values(config)[i];
  study.init({}, callback);
  assert.equal(study.main(store.bars[0], callback)[0], 1);
  config = settings(2); assert.equal(study.main(store.bars[0], callback)[0], 50);
  config = settings(3, { divergence_length: 1 });
  const removed = [], lines = [];
  const ids = drawToolkitDivergences({ removeEntity: (id) => removed.push(id), createMultipointShape: (points, options) => { lines.push({ points, options }); return 'line'; } },
    { getInputValues: () => Object.entries(config).map(([id, value]) => ({ id, value })) }, 'toolkit-study', store, ['old-line']);
  assert.deepEqual(removed, ['old-line']); assert.deepEqual(ids, ['line']);
  assert.equal(lines[0].options.ownerStudyId, 'toolkit-study'); assert.equal(lines[0].options.disableSave, true);
  assert.equal(lines[0].points[0].price, 5); assert.equal(lines[0].points[1].price, 4);
  assert.equal(lines[0].points[0].time, Date.parse(timestamps[1]) / 1000);
  for (const palette of Object.values(definition.metainfo.defaults.palettes.direction.colors)) {
    assert.equal(typeof palette.width, 'number'); assert.equal(typeof palette.style, 'number');
  }
  assert.equal(toolkitSettings({ divergence_length: -1 }).divergence_length, 1);
});
