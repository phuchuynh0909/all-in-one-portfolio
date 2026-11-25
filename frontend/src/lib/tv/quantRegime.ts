import type { CustomIndicator, EntityId, IChartWidgetApi, IPineStudyResult, IStudyApi, LibraryPineStudy, PineJS } from './charting_library';

export const REGIME_PARAMS = { chop_window: 14, trend_window: 126, vr_window: 252, vr_lag: 10 };
export const REGIME_NAME = 'Quantitative Regime';

export function addRegimeLegend(chart: Pick<IChartWidgetApi, 'createAnchoredShape'>, ownerStudyId: EntityId) {
  return chart.createAnchoredShape({ x: 0.82, y: 0.2 }, {
    shape: 'anchored_text', ownerStudyId,
    text: 'REGIME GUIDE\n−1  Downtrend     0  Mixed / unclear\n+1  Uptrend          2  Range / mean reversion\nCategory codes, not strength scores.',
    lock: true, disableSelection: true, disableSave: true, disableUndo: true,
    showInObjectsTree: false,
    overrides: {
      color: '#e2e8f0', fontsize: 12, bold: false,
      fillBackground: true, backgroundColor: '#0f172a', backgroundTransparency: 10,
      drawBorder: true, borderColor: '#475569',
    },
  });
}

const mean = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / values.length;
const variance = (values: number[]) => {
  const average = mean(values);
  return values.reduce((sum, value) => sum + (value - average) ** 2, 0) / (values.length - 1);
};
// Two-sided normal tail (Abramowitz–Stegun approximation).
function pValue(z: number) {
  const x = Math.abs(z);
  const t = 1 / (1 + 0.2316419 * x);
  return Math.min(1, 2 * Math.exp(-x * x / 2) / Math.sqrt(2 * Math.PI)
    * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429)))));
}

export function mannKendall(prices: number[]) {
  const n = prices.length;
  if (n < 3) return { tau: NaN, p: NaN };
  let s = 0;
  const ties = new Map<number, number>();
  for (let i = 0; i < n; i++) {
    ties.set(prices[i], (ties.get(prices[i]) ?? 0) + 1);
    for (let j = i + 1; j < n; j++) s += Math.sign(prices[j] - prices[i]);
  }
  const correction = [...ties.values()].reduce((sum, count) => sum + count * (count - 1) * (2 * count + 5), 0);
  const varS = (n * (n - 1) * (2 * n + 5) - correction) / 18;
  const z = varS > 0 ? (s - Math.sign(s)) / Math.sqrt(varS) : 0;
  return { tau: s / (n * (n - 1) / 2), p: pValue(z) };
}

/** Matches hurst.compute_Hc(kind='price', simplified=True), including its scales. */
export function hurstExponent(prices: number[]) {
  if (prices.length < 100 || prices.some((price) => !Number.isFinite(price) || price <= 0)) return NaN;
  const sizes: number[] = [];
  for (let exponent = 1; exponent < Math.log10(prices.length - 1); exponent += 0.25) sizes.push(Math.floor(10 ** exponent));
  sizes.push(prices.length);
  const points = sizes.map((size) => {
    const ratios: number[] = [];
    for (let start = 0; start + size <= prices.length; start += size) {
      const segment = prices.slice(start, start + size);
      const returns = segment.slice(1).map((price, i) => price / segment[i] - 1);
      const range = Math.max(...segment) / Math.min(...segment) - 1;
      const sd = Math.sqrt(variance(returns));
      if (range > 0 && sd > 0) ratios.push(range / sd);
    }
    return [Math.log10(size), Math.log10(mean(ratios))];
  });
  if (points.some((point) => !Number.isFinite(point[1]))) return NaN;
  const xMean = mean(points.map(([x]) => x));
  const yMean = mean(points.map(([, y]) => y));
  return points.reduce((sum, [x, y]) => sum + (x - xMean) * (y - yMean), 0)
    / points.reduce((sum, [x]) => sum + (x - xMean) ** 2, 0);
}

export function varianceRatio(prices: number[], q: number) {
  if (!Number.isInteger(q) || q < 2 || prices.some((price) => !Number.isFinite(price) || price <= 0)) return { vr: NaN, z: NaN, p: NaN };
  const returns = prices.slice(1).map((price, i) => Math.log(price / prices[i]));
  const n = returns.length;
  if (n <= q) return { vr: NaN, z: NaN, p: NaN };
  const mu = mean(returns);
  const squared = returns.map((value) => (value - mu) ** 2);
  const total = squared.reduce((sum, value) => sum + value, 0);
  if (total === 0) return { vr: NaN, z: NaN, p: NaN };
  let qSum = 0;
  let rolling = returns.slice(0, q).reduce((sum, value) => sum + value, 0);
  for (let i = 0; i <= n - q; i++) {
    if (i > 0) rolling += returns[i + q - 1] - returns[i - 1];
    qSum += (rolling - q * mu) ** 2;
  }
  const vr = qSum / (q * (n - q + 1) * (1 - q / n)) / (total / (n - 1));
  let delta = 0;
  for (let k = 1; k < q; k++) {
    let product = 0;
    for (let i = k; i < n; i++) product += squared[i] * squared[i - k];
    delta += (2 * (q - k) / q) ** 2 * product / total ** 2;
  }
  const z = (vr - 1) / (delta > 0 ? Math.sqrt(delta) : 1e-6);
  return { vr, z, p: pValue(z) };
}

export interface RegimeBar { time: number; high: number; low: number; close: number }
export function calculateRegime(bars: RegimeBar[], params = REGIME_PARAMS) {
  const { chop_window: chopWindow, trend_window: trendWindow, vr_window: vrWindow, vr_lag: q } = params;
  const prices = bars.map((bar) => bar.close);
  let chop = NaN;
  if (bars.length >= chopWindow) {
    const recent = bars.slice(-chopWindow);
    const range = Math.max(...recent.map((bar) => bar.high)) - Math.min(...recent.map((bar) => bar.low));
    const tr = recent.reduce((sum, bar, i) => {
      const previous = bars[bars.length - recent.length + i - 1]?.close;
      return sum + Math.max(bar.high - bar.low, previous === undefined ? 0 : Math.abs(bar.high - previous), previous === undefined ? 0 : Math.abs(bar.low - previous));
    }, 0);
    if (range > 0 && tr > 0) chop = 100 * Math.log10(tr / range) / Math.log10(chopWindow);
  }
  const mk = prices.length >= trendWindow ? mannKendall(prices.slice(-trendWindow)) : { tau: NaN, p: NaN };
  const h = prices.length >= trendWindow ? hurstExponent(prices.slice(-trendWindow)) : NaN;
  const vr = prices.length >= vrWindow ? varianceRatio(prices.slice(-vrWindow), q) : { vr: NaN, z: NaN, p: NaN };
  let regime = NaN;
  if ([chop, mk.tau, mk.p, h, vr.vr, vr.p].every(Number.isFinite)) {
    const trending = Number(chop < 38.2) + Number(mk.p < 0.05) + Number(h > 0.5) + Number(vr.vr > 1 && vr.p < 0.05);
    const reverting = Number(chop > 61.8) + Number(h < 0.5) + Number(vr.vr < 1 && vr.p < 0.05);
    // Three trend votes plus significant direction; two range/reversion votes otherwise.
    regime = trending >= 3 && mk.p < 0.05 ? Math.sign(mk.tau) : reverting >= 2 ? 2 : 0;
  }
  return { regime, chop, tau: mk.tau, mkP: mk.p, hurst: h, vr: vr.vr, vrZ: vr.z, vrP: vr.p };
}

const outputs = [
  ['regime', 'Regime: -1 down, 0 mixed, 1 up, 2 range/reversion'],
  ['chop', 'CHOP'], ['tau', 'Mann–Kendall tau'], ['mkP', 'Mann–Kendall p'],
  ['hurst', 'Hurst H'], ['vr', 'Variance ratio'], ['vrZ', 'Variance ratio z'], ['vrP', 'Variance ratio p'],
] as const;
const inputs = [
  { id: 'chop_window', name: 'CHOP window', defval: 14, min: 2, max: 100 },
  { id: 'trend_window', name: 'Mann–Kendall / Hurst window', defval: 126, min: 100, max: 500 },
  { id: 'vr_window', name: 'Variance ratio window', defval: 252, min: 12, max: 1000 },
  { id: 'vr_lag', name: 'Variance ratio lag (less than window - 1)', defval: 10, min: 2, max: 100 },
];

const metricGroups = [
  { id: 'show_chop', name: 'Show CHOP metrics', plots: ['chop'] },
  { id: 'show_mk', name: 'Show Mann–Kendall metrics (tau, p)', plots: ['tau', 'mkP'] },
  { id: 'show_hurst', name: 'Show Hurst metric', plots: ['hurst'] },
  { id: 'show_vr', name: 'Show variance ratio metrics (VR, z, p)', plots: ['vr', 'vrZ', 'vrP'] },
];

/** Display flags also hide values from the legend and data window, unlike `visible` alone. */
export function syncRegimeMetricVisibility(study: Pick<IStudyApi, 'getInputValues' | 'getStyleValues' | 'applyOverrides'>) {
  const settings = Object.fromEntries(study.getInputValues().map(({ id, value }) => [id, value]));
  const styles = study.getStyleValues().styles;
  const overrides: Record<string, number | boolean> = {};
  for (const group of metricGroups) {
    const visible = settings[group.id] === true;
    const display = visible ? 15 : 0;
    for (const id of group.plots) {
      const title = outputs.find(([key]) => key === id)![1];
      if (styles[id]?.display !== display) overrides[`${title}.display`] = display;
      if (styles[id]?.visible !== visible) overrides[`${title}.visible`] = visible;
    }
  }
  if (Object.keys(overrides).length) study.applyOverrides(overrides);
}

export function buildQuantRegimeStudy(pine: PineJS): CustomIndicator {
  return {
    name: REGIME_NAME,
    metainfo: {
      _metainfoVersion: 53, id: 'quant_regime@tv-custom-1', name: REGIME_NAME,
      description: REGIME_NAME, shortDescription: 'Regime', isCustomIndicator: true,
      is_price_study: false, format: { type: 'price', precision: 4 },
      plots: [...outputs.map(([id]) => ({ id, type: 'line' })), { id: 'color', type: 'colorer', target: 'regime', palette: 'regimes' }],
      inputs: [...inputs.map((input) => ({ ...input, type: 'integer' })),
        ...metricGroups.map(({ id, name }) => ({ id, name, type: 'bool', defval: false }))],
      styles: Object.fromEntries(outputs.map(([id, title]) => [id, { title, histogramBase: 0 }])),
      palettes: { regimes: { colors: { 0: { name: 'Downtrend' }, 1: { name: 'Mixed' }, 2: { name: 'Uptrend' }, 3: { name: 'Range / mean reversion' } }, valToIndex: { 0: 0, 1: 1, 2: 2, 3: 3 } } },
      defaults: {
        inputs: { ...REGIME_PARAMS, ...Object.fromEntries(metricGroups.map(({ id }) => [id, false])) },
        styles: Object.fromEntries(outputs.map(([id]) => [id, { color: '#94a3b8', linestyle: 0, linewidth: 2, plottype: id === 'regime' ? 14 : 0, visible: id === 'regime', display: id === 'regime' ? 15 : 0, trackPrice: false }])),
        palettes: { regimes: { colors: { 0: { color: '#ef4444', width: 2, style: 0 }, 1: { color: '#94a3b8', width: 2, style: 0 }, 2: { color: '#22c55e', width: 2, style: 0 }, 3: { color: '#f59e0b', width: 2, style: 0 } } } },
      },
    },
    constructor: function (this: LibraryPineStudy<IPineStudyResult>) {
      let bars: RegimeBar[] = [];
      this.init = () => { bars = []; };
      this.main = (context, input) => {
        const params = Object.fromEntries(inputs.map((spec, i) => [spec.id, Math.min(spec.max, Math.max(spec.min, Math.round(input<number>(i))))])) as typeof REGIME_PARAMS;
        const depth = Math.max(params.chop_window + 1, params.trend_window, params.vr_window);
        // Register history during TradingView's empty probe; our JS buffer is invisible to its lookback discovery.
        context.new_var(pine.Std.close(context)).get(depth - 1);
        const bar = { time: pine.Std.time(context), high: pine.Std.high(context), low: pine.Std.low(context), close: pine.Std.close(context) };
        if (!Object.values(bar).every(Number.isFinite) || bar.close <= 0 || bar.high < bar.low) return Array(9).fill(NaN);
        const last = bars.at(-1);
        if (last && bar.time < last.time) bars = [];
        if (bars.at(-1)?.time === bar.time) bars[bars.length - 1] = bar;
        else bars.push(bar);
        bars = bars.slice(-depth);
        const result = calculateRegime(bars, params);
        return [...outputs.map(([id]) => result[id]), result.regime + 1];
      };
    },
  } as unknown as CustomIndicator;
}
