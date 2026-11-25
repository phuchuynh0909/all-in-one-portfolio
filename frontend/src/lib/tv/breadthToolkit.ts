/**
 * Adapted from Market Breadth Toolkit © LuxAlgo, supplied by the user.
 * CC BY-NC-SA 4.0: https://creativecommons.org/licenses/by-nc-sa/4.0/
 * Changes: TypeScript/TradingView adapter, local-universe data, solid fills,
 * explicit missing-data handling, and confirmed pivot divergence drawings.
 */
import { formatChartTime, type MarketBreadthResponse } from '../services/timeseries';
import type { CustomIndicator, EntityId, IChartWidgetApi, IStudyApi, LibraryPineStudy, IPineStudyResult, PineJS, StudyInputValue } from './charting_library';
import type { MarketBreadthStore } from './breadth';

export const TOOLKIT_NAME = 'Market Breadth Toolkit';
export const TOOLKIT_METHODS = [
  'Breakaway Momentum 10 (Deemer)', 'Breakaway Momentum 20 (Deemer)',
  'Breadth Thrust (Zweig)', 'Market Thrust (Zweig)',
  'Advance Decline Thrust (Whaley)', 'Up/Down Volume Thrust (Whaley)',
] as const;
export interface ToolkitSettings {
  method: typeof TOOLKIT_METHODS[number];
  divergences: boolean;
  divergence_length: number;
  fixed_levels: boolean;
  top: number;
  bottom: number;
}
export const TOOLKIT_DEFAULTS: ToolkitSettings = {
  method: TOOLKIT_METHODS[2], divergences: true, divergence_length: 10,
  fixed_levels: false, top: 61.5, bottom: 40,
};
const GREEN = '#089981';
const RED = '#F23645';
const GRAY = '#808080';
const METHOD_LEVELS = [
  [1.97, 0.5, 1], [1.72, 0.5, 1], [61.5, 40, 50],
  [0, 0, 0], [73.66, 19.05, 50], [77.88, 16.41, 50],
] as const;
const keys = Object.keys(TOOLKIT_DEFAULTS) as (keyof ToolkitSettings)[];
type ToolkitData = Pick<MarketBreadthResponse, 'advances' | 'declines' | 'advancing_volume' | 'declining_volume'>;
export interface ToolkitDivergence { from: number; to: number; confirmedAt: number; bullish: boolean }

export function toolkitSettings(values: Record<string, StudyInputValue>): ToolkitSettings {
  const method = TOOLKIT_METHODS.find((name) => name === values.method) ?? TOOLKIT_DEFAULTS.method;
  const numeric = (key: 'top' | 'bottom' | 'divergence_length') =>
    typeof values[key] === 'number' && Number.isFinite(values[key]) ? values[key] as number : TOOLKIT_DEFAULTS[key];
  return {
    method,
    divergences: typeof values.divergences === 'boolean' ? values.divergences : true,
    divergence_length: Math.min(100, Math.max(1, Math.round(numeric('divergence_length')))),
    fixed_levels: values.fixed_levels === true,
    top: numeric('top'), bottom: numeric('bottom'),
  };
}

export function toolkitInterpretation(settings: ToolkitSettings): string {
  const method = TOOLKIT_METHODS.indexOf(settings.method);
  const top = settings.fixed_levels ? settings.top : METHOD_LEVELS[method][0];
  const bottom = settings.fixed_levels ? settings.bottom : METHOD_LEVELS[method][1];
  const descriptions = [
    '10-day average of the daily advancing/declining stock ratio. A value of 1.5 means the daily ratio averaged 1.5. Above 1 favors advancing stocks; below 1 favors declining stocks.',
    '20-day average of the daily advancing/declining stock ratio. A value of 1.5 means the daily ratio averaged 1.5. Above 1 favors advancing stocks; below 1 favors declining stocks.',
    '10-day EMA of advancing stocks as a percentage of advancing + declining stocks (0–100). Above 50 means positive smoothed participation; below 50 means negative participation. For example, 55 indicates moderately positive breadth.',
    'Advancing stock count × advancing volume, minus declining stock count × declining volume. Positive means the advancing-side product dominates; negative means the declining side dominates. This is a raw score, not a percentage, and its size depends on the stock universe and trading volume.',
    '5-day average percentage of advancing stocks among advancing + declining stocks (0–100). For example, 60 means advancing stocks averaged 60% of directional issues. Above 50 favors advancing stocks; below 50 favors declining stocks.',
    '5-day average percentage of up volume among up + down volume (0–100). For example, 60 means up volume averaged 60% of directional volume. Above 50 favors volume in advancing stocks; below 50 favors volume in declining stocks.',
  ];
  const signal = method < 2 ? `A Thrust marks a crossover above ${top}. Below ${bottom} indicates weak breadth.`
    : method === 2 ? `A Thrust requires a crossover above ${top}, with a reading below ${bottom} within the last 10 trading intervals. Being above ${top} alone is not a new thrust.`
      : method === 3 ? 'This method does not generate Thrust labels.'
        : `A Thrust marks each reading above ${top} (upper extreme) or below ${bottom} (lower extreme).`;
  return `${descriptions[method]}\n\n${settings.fixed_levels ? 'Custom' : 'Default'} levels: upper ${top}, lower ${bottom}. ${signal}\n\nUnchanged stocks are excluded. Uses the local stock universe; thresholds are from the supplied script and have not been calibrated to this universe.`;
}

const valid = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value);
const ratio = (numerator: number, denominator: number) => denominator > 0 ? numerator / denominator : null;
function sma(values: (number | null)[], length: number) {
  // Missing observations leave gaps rather than substituting zero volume.
  return values.map((_, i) => {
    const window = values.slice(Math.max(0, i - length + 1), i + 1);
    return window.length === length && window.every(valid) ? window.reduce((sum, value) => sum + value, 0) / length : null;
  });
}
function ema(values: (number | null)[], length: number) {
  let previous: number | null = null;
  return values.map((value) => {
    if (!valid(value)) return null;
    previous = previous === null ? value : previous + 2 / (length + 1) * (value - previous);
    return previous;
  });
}

export function calculateBreadthToolkit(data: ToolkitData, settings = TOOLKIT_DEFAULTS, prices: (number | null)[] = []) {
  const method = TOOLKIT_METHODS.indexOf(settings.method);
  const raw = data.advances.map((advances, i) => {
    const declines = data.declines[i];
    if (!valid(advances) || !valid(declines) || advances < 0 || declines < 0) return null;
    if (method < 2) return ratio(advances, declines);
    if (method === 2 || method === 4) return ratio(100 * advances, advances + declines);
    const up = data.advancing_volume?.[i];
    const down = data.declining_volume?.[i];
    if (!valid(up) || !valid(down) || up < 0 || down < 0) return null;
    return method === 3 ? advances * up - declines * down : ratio(100 * up, up + down);
  });
  const values = method < 2 ? sma(raw, method === 0 ? 10 : 20)
    : method === 2 ? ema(raw, 10) : method === 4 || method === 5 ? sma(raw, 5) : raw;
  const top = settings.fixed_levels ? settings.top : METHOD_LEVELS[method][0];
  const bottom = settings.fixed_levels ? settings.bottom : METHOD_LEVELS[method][1];
  const base = METHOD_LEVELS[method][2];
  const thrusts = values.map((value, i) => {
    if (!valid(value)) return false;
    if (method === 3) return false;
    if (method >= 4) return value > top || value < bottom;
    const previous = values[i - 1];
    const crossed = valid(previous) && previous <= top && value > top;
    return crossed && (method !== 2 || values.slice(Math.max(0, i - 10), i + 1).some((v) => valid(v) && v < bottom));
  });
  const divergences: ToolkitDivergence[] = [];
  if (settings.divergences) {
    const length = settings.divergence_length;
    let lastHigh: number | null = null;
    let lastLow: number | null = null;
    for (let confirmedAt = length * 2; confirmedAt < values.length; confirmedAt++) {
      const pivot = confirmedAt - length;
      const value = values[pivot];
      const current = values[confirmedAt];
      if (!valid(value) || !valid(current) || !valid(prices[pivot])) continue;
      const left = values.slice(pivot - length, pivot);
      const right = values.slice(pivot + 1, confirmedAt + 1);
      const high = left.every((v) => valid(v) && value >= v) && right.every((v) => valid(v) && value > v);
      const low = left.every((v) => valid(v) && value <= v) && right.every((v) => valid(v) && value < v);
      if (high && current > base) {
        if (lastHigh !== null && value < values[lastHigh]! && prices[pivot]! >= prices[lastHigh]!) {
          divergences.push({ from: lastHigh, to: pivot, confirmedAt, bullish: false });
        }
        lastHigh = pivot;
      }
      if (low && current < base) {
        if (lastLow !== null && value > values[lastLow]! && prices[pivot]! <= prices[lastLow]!) {
          divergences.push({ from: lastLow, to: pivot, confirmedAt, bullish: true });
        }
        lastLow = pivot;
      }
    }
  }
  return { values, thrusts, divergences, top, bottom, base };
}

export function toolkitForStore(store: MarketBreadthStore, settings: ToolkitSettings) {
  const closes = new Map(store.bars.map((bar) => [bar.time, bar.close]));
  const prices = store.breadth.timestamps.map((time) => closes.get(formatChartTime(time) * 1000) ?? null);
  return calculateBreadthToolkit(store.breadth, settings, prices);
}

export function buildBreadthToolkit(pine: PineJS, store: MarketBreadthStore): CustomIndicator {
  const plots = ['breadth', 'base', 'bull', 'bear', 'top', 'bottom'];
  const titles = ['Breadth', 'Base level', 'Bullish fill', 'Bearish fill', 'Top level', 'Bottom level'];
  return {
    name: TOOLKIT_NAME,
    metainfo: {
      _metainfoVersion: 53, id: 'market-breadth-toolkit@aiop-breadth-1', name: TOOLKIT_NAME,
      description: TOOLKIT_NAME, shortDescription: TOOLKIT_NAME, isCustomIndicator: true,
      is_price_study: false, format: { type: 'price', precision: 2 },
      plots: [
        ...plots.map((id) => ({ id, type: 'line' })),
        { id: 'thrust', type: 'shapes' }, { id: 'lower_thrust', type: 'shapes' },
        { id: 'color', type: 'colorer', target: 'breadth', palette: 'direction' },
      ],
      inputs: [
        { id: 'method', name: 'Breadth method', type: 'text', defval: TOOLKIT_DEFAULTS.method, options: [...TOOLKIT_METHODS] },
        { id: 'divergences', name: 'Show confirmed divergences', type: 'bool', defval: true },
        { id: 'divergence_length', name: 'Divergence pivot length', type: 'integer', defval: 10, min: 1, max: 100 },
        { id: 'fixed_levels', name: 'Use fixed levels', type: 'bool', defval: false },
        { id: 'top', name: 'Fixed top level', type: 'float', defval: 61.5 },
        { id: 'bottom', name: 'Fixed bottom level', type: 'float', defval: 40 },
      ],
      styles: {
        ...Object.fromEntries(plots.map((id, i) => [id, { title: titles[i], histogramBase: 0, isHidden: ['base', 'bull', 'bear'].includes(id) }])),
        thrust: { title: 'Thrust', text: 'Thrust', size: 'tiny' },
        lower_thrust: { title: 'Lower thrust', text: 'Thrust', size: 'tiny' },
      },
      palettes: { direction: { colors: { 0: { name: 'Bullish' }, 1: { name: 'Bearish' } }, valToIndex: { 0: 0, 1: 1 } } },
      filledAreas: [
        { id: 'bull_fill', objAId: 'base', objBId: 'bull', title: 'Bullish breadth', type: 'plot_plot' },
        { id: 'bear_fill', objAId: 'base', objBId: 'bear', title: 'Bearish breadth', type: 'plot_plot' },
      ],
      defaults: {
        inputs: TOOLKIT_DEFAULTS,
        styles: {
          ...Object.fromEntries(plots.map((id) => [id, {
            color: GRAY, linestyle: id === 'top' || id === 'bottom' ? 2 : 0, linewidth: 1, plottype: 0,
            visible: true, trackPrice: false,
            transparency: ['base', 'bull', 'bear'].includes(id) ? 100 : 0,
            display: ['base', 'bull', 'bear'].includes(id) ? 1 : 15,
          }])),
          thrust: { plottype: 'shape_label_down', location: 'Absolute', color: GREEN, textColor: '#ffffff', visible: true, transparency: 0 },
          lower_thrust: { plottype: 'shape_label_up', location: 'Absolute', color: RED, textColor: '#ffffff', visible: true, transparency: 0 },
        },
        palettes: { direction: { colors: { 0: { color: GREEN, width: 2, style: 0 }, 1: { color: RED, width: 2, style: 0 } } } },
        filledAreasStyle: {
          bull_fill: { color: GREEN, transparency: 80, visible: true },
          bear_fill: { color: RED, transparency: 80, visible: true },
        },
      },
    },
    constructor: function (this: LibraryPineStudy<IPineStudyResult>) {
      let signature = '';
      let result: ReturnType<typeof toolkitForStore> | null = null;
      this.init = () => { signature = ''; result = null; };
      this.main = (context, input) => {
        const settings = toolkitSettings(Object.fromEntries(keys.map((key, i) => [key, input(i)])));
        const nextSignature = JSON.stringify(settings);
        if (!result || nextSignature !== signature) {
          result = toolkitForStore(store, settings);
          signature = nextSignature;
        }
        const i = store.indexAt(pine.Std.time(context));
        if (i < 0) return Array(9).fill(NaN);
        const value = result.values[i];
        if (!valid(value)) return Array(9).fill(NaN);
        const lower = settings.method.includes('Whaley') && value < 50;
        return [value, result.base, value > result.base ? value : NaN, value <= result.base ? value : NaN,
          result.top, result.bottom, result.thrusts[i] && !lower ? value : NaN,
          result.thrusts[i] && lower ? value : NaN, value > result.base ? 0 : 1];
      };
    },
  } as unknown as CustomIndicator;
}

export function drawToolkitDivergences(
  chart: Pick<IChartWidgetApi, 'createMultipointShape' | 'removeEntity'>,
  study: Pick<IStudyApi, 'getInputValues'>, ownerStudyId: EntityId,
  store: MarketBreadthStore, previous: EntityId[],
) {
  previous.forEach((id) => {
    try { chart.removeEntity(id); } catch { /* Drawing may already have been removed with its study. */ }
  });
  const settings = toolkitSettings(Object.fromEntries(study.getInputValues().map(({ id, value }) => [id, value])));
  const result = toolkitForStore(store, settings);
  const ids: EntityId[] = [];
  for (const divergence of result.divergences.slice(-500)) {
    const id = chart.createMultipointShape([divergence.from, divergence.to].map((i) => ({
      time: formatChartTime(store.breadth.timestamps[i]), price: result.values[i]!,
    })), {
      shape: 'trend_line', ownerStudyId, lock: true, disableSelection: true,
      disableSave: true, disableUndo: true, showInObjectsTree: false,
      overrides: { linecolor: divergence.bullish ? GREEN : RED, linewidth: 1, extendLeft: false, extendRight: false },
    });
    if (id) ids.push(id);
  }
  return ids;
}
