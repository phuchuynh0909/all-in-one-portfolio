/**
 * Custom studies that bridge the backend-computed indicator arrays into the
 * TradingView charting library.
 *
 * Each study's `main()` looks up the current bar (by time) in {@link tvStore}
 * and returns the precomputed values for that bar — no in-browser math. Plots
 * map positionally to `metainfo.plots`; colorer plots (for per-bar histogram/
 * line colors) follow the value plots and return a palette index.
 *
 * The `id` matches the app's indicator config id; `name` is the study name
 * passed to `activeChart().createStudy(name)`.
 */
import { tvStore, indexAtTimeMs } from './store';
import type { CustomIndicator, PineJS } from './charting_library';

// LineStudyPlotStyle
const PLOT_LINE = 0;
const PLOT_HISTOGRAM = 1;
// LineStyle
const LINE_SOLID = 0;
const LINE_DASHED = 2;

type Getter = (ind: Record<string, any>, i: number) => number | null | undefined;

interface PlotSpec {
  id: string;
  title: string;
  color: string;
  width?: number;
  dashed?: boolean;
  histogram?: boolean;
  get: Getter;
}
interface PaletteSpec {
  target: string; // value plot id whose color this drives
  colors: string[]; // index -> color
  index: Getter; // returns palette index for the bar
}
interface BandSpec {
  value: number;
  color: string;
}
interface FillSpec {
  a: string;
  b: string;
  color: string;
  title: string;
}
interface ShapeSpec {
  id: string;
  title: string;
  /** PlotShapeId, e.g. 'shape_arrow_up'. */
  shape: string;
  /** MarkLocation, e.g. 'AboveBar'. */
  location: string;
  color: string;
  /** Returns the anchor value when the shape should show, else null/NaN. */
  get: Getter;
}
export interface StudySpec {
  id: string;
  name: string;
  priceStudy: boolean;
  precision?: number;
  plots: PlotSpec[];
  palettes?: PaletteSpec[];
  bands?: BandSpec[];
  fills?: FillSpec[];
  shapes?: ShapeSpec[];
}

function num(v: number | null | undefined): number {
  return typeof v === 'number' && isFinite(v) ? v : NaN;
}

/** Declarative catalogue of every bridged indicator, keyed by app config id. */
export const STUDY_SPECS: StudySpec[] = [
  // ── Separate-pane oscillators ──────────────────────────────────────────────
  {
    id: 'rsi', name: 'RSI (bridged)', priceStudy: false, precision: 2,
    plots: [
      { id: 'rsi', title: 'RSI (14)', color: '#6366f1', width: 2, get: (d, i) => d.rsi?.[i] },
      { id: 'rsi5', title: 'RSI (5)', color: '#f59e0b', width: 2, get: (d, i) => d.rsi_5?.[i] },
    ],
    bands: [
      { value: 70, color: 'rgba(239, 68, 68, 0.5)' },
      { value: 30, color: 'rgba(34, 197, 94, 0.5)' },
    ],
  },
  {
    id: 'bvc', name: 'BVC (bridged)', priceStudy: false, precision: 2,
    plots: [{ id: 'bvc', title: 'BVC', color: '#a855f7', width: 2, get: (d, i) => d.bvc?.[i] }],
    bands: [{ value: 0, color: 'rgba(156, 163, 175, 0.4)' }],
  },
  {
    id: 'kalman_zscore', name: 'Kalman Z-Score (bridged)', priceStudy: false, precision: 2,
    plots: [{ id: 'z', title: 'Kalman Z-Score', color: '#06b6d4', width: 2, get: (d, i) => d.kalman_zscore?.[i] }],
    bands: [
      { value: 2, color: 'rgba(239, 68, 68, 0.4)' },
      { value: -2, color: 'rgba(34, 197, 94, 0.4)' },
      { value: 0, color: 'rgba(156, 163, 175, 0.4)' },
    ],
  },
  {
    id: 'yz_volatility', name: 'YZ Volatility (bridged)', priceStudy: false, precision: 4,
    plots: [{ id: 'yz', title: 'YZ Volatility', color: '#ec4899', width: 2, get: (d, i) => d.yz_volatility?.[i] }],
  },
  {
    id: 'gkyz_volatility', name: 'GKYZ Volatility (bridged)', priceStudy: false, precision: 3,
    plots: [{ id: 'gkyz', title: 'GKYZ', color: '#f97316', width: 2, get: (d, i) => d.gkyz_volatility?.[i] }],
    bands: [
      { value: 0.8, color: 'rgba(239, 68, 68, 0.6)' },
      { value: 0.2, color: 'rgba(34, 197, 94, 0.6)' },
    ],
  },
  {
    id: 'matrix_series', name: 'Matrix Series (bridged)', priceStudy: false, precision: 1,
    plots: [
      { id: 'sup', title: 'MS Support', color: '#ef4444', width: 2, get: (d, i) => d.matrix_series?.support_line?.[i] },
      { id: 'res', title: 'MS Resistance', color: '#22c55e', width: 2, get: (d, i) => d.matrix_series?.resistance_line?.[i] },
      { id: 'up', title: 'MS Up', color: '#00bcd4', width: 1, get: (d, i) => d.matrix_series?.up_line?.[i] },
      { id: 'down', title: 'MS Down', color: '#eab308', width: 1, get: (d, i) => d.matrix_series?.down_line?.[i] },
      { id: 'hh', title: 'MS HH', color: 'rgba(34,197,94,0.5)', width: 1, get: (d, i) => d.matrix_series?.hh?.[i] },
      { id: 'll', title: 'MS LL', color: 'rgba(239,68,68,0.5)', width: 1, get: (d, i) => d.matrix_series?.ll?.[i] },
    ],
    fills: [{ a: 'hh', b: 'll', color: 'rgba(99,102,241,0.12)', title: 'MS Range' }],
  },
  {
    id: 'squeeze_ttm', name: 'Squeeze TTM (bridged)', priceStudy: false, precision: 2,
    plots: [{ id: 'hist', title: 'Squeeze TTM', color: '#9ca3af', histogram: true, get: (d, i) => d.squeeze_ttm?.histogram?.[i] }],
    palettes: [{
      target: 'hist',
      colors: ['rgba(244,245,244,0.7)', 'rgba(239,68,68,0.7)', 'rgba(34,197,94,0.7)'],
      index: (d, i) => d.squeeze_ttm?.squeeze_state?.[i],
    }],
  },
  {
    id: 'williams_vix_fix', name: 'Williams VIX Fix (bridged)', priceStudy: false, precision: 2,
    plots: [{ id: 'wvf', title: 'WVF', color: 'rgba(34,197,94,0.7)', histogram: true, get: (d, i) => d.williams_vix_fix?.wvf?.[i] }],
    palettes: [{
      target: 'wvf',
      colors: ['rgba(34,197,94,0.7)', 'rgba(255,255,0,0.7)'],
      index: (d, i) => (d.williams_vix_fix?.filtered?.[i] ? 1 : 0),
    }],
    // Blue dot just below the histogram baseline on filtered-entry (cond_fe) bars.
    shapes: [{
      id: 'fe', title: 'FE', shape: 'shape_circle', location: 'Absolute', color: '#3b82f6',
      get: (d, i) => (d.williams_vix_fix?.cond_fe?.[i] ? 1 : null),
    }],
  },

  // ── Price-pane overlays ─────────────────────────────────────────────────────
  {
    id: 'atr_trailing', name: 'ATR Trailing Stop (bridged)', priceStudy: true,
    plots: [{ id: 'atr', title: 'Trailing Stop', color: '#22c55e', width: 2, dashed: true, get: (d, i) => d.atr_trailing?.[i] }],
  },
  {
    id: 'vwap', name: 'VWAP Bands (bridged)', priceStudy: true,
    plots: [
      { id: 'high', title: 'VWAP High', color: '#3b82f6', width: 2, get: (d, i) => d.vwap_highest?.[i] },
      { id: 'low', title: 'VWAP Low', color: '#f97316', width: 2, get: (d, i) => d.vwap_lowest?.[i] },
    ],
  },
  {
    id: 'kama', name: 'KAMA (bridged)', priceStudy: true,
    plots: [{ id: 'kama', title: 'KAMA', color: '#eab308', width: 2, get: (d, i) => d.kama?.[i] }],
  },
  {
    id: 'chandelier_exit', name: 'Chandelier Exit (bridged)', priceStudy: true,
    plots: [{ id: 'ce', title: 'CE', color: '#00ffff', width: 2, dashed: true, get: (d, i) => d.chandelier_exit?.value?.[i] }],
    palettes: [{
      target: 'ce',
      colors: ['#00ffff', '#f23645'],
      index: (d, i) => (d.chandelier_exit?.direction?.[i] === 1 ? 0 : 1),
    }],
  },
  {
    id: 'linreg_channel', name: 'LR Prediction Channel (bridged)', priceStudy: true,
    plots: [
      { id: 'reg', title: 'LR Reg', color: '#38bdf8', width: 1, get: (d, i) => d.linreg_channel?.reg?.[i] },
      { id: 'piUp', title: 'LR PI Up', color: '#f43f5e', width: 1, dashed: true, get: (d, i) => d.linreg_channel?.pi_upper?.[i] },
      { id: 'piLow', title: 'LR PI Low', color: '#f43f5e', width: 1, dashed: true, get: (d, i) => d.linreg_channel?.pi_lower?.[i] },
    ],
    fills: [{ a: 'piUp', b: 'piLow', color: 'rgba(56,189,248,0.08)', title: 'LR Channel' }],
  },
  {
    id: 'gaussian_frama', name: 'Gaussian FRAMA (bridged)', priceStudy: true,
    plots: [
      { id: 'frama', title: 'G-FRAMA', color: '#9ca3af', width: 2, get: (d, i) => d.gaussian_frama?.frama?.[i] },
      { id: 'longV', title: 'G-FRAMA Long', color: '#3b82f6', width: 1, dashed: true, get: (d, i) => d.gaussian_frama?.long_v?.[i] },
      { id: 'shortV', title: 'G-FRAMA Short', color: '#ef4444', width: 1, dashed: true, get: (d, i) => d.gaussian_frama?.short_v?.[i] },
    ],
    // Regime coloring: blue (qb=+1, bullish) / red (qb=-1, bearish) / gray (neutral).
    palettes: [{
      target: 'frama',
      colors: ['#3b82f6', '#ef4444', '#9ca3af'],
      index: (d, i) => (d.gaussian_frama?.qb?.[i] === 1 ? 0 : d.gaussian_frama?.qb?.[i] === -1 ? 1 : 2),
    }],
    fills: [{ a: 'longV', b: 'shortV', color: 'rgba(99,102,241,0.08)', title: 'G-FRAMA Cloud' }],
  },
  {
    id: 'hull_butterfly', name: 'Hull Butterfly Oscillator (bridged)', priceStudy: false, precision: 2,
    plots: [{ id: 'hso', title: 'HBO', color: '#9ca3af', histogram: true, get: (d, i) => d.hull_butterfly?.hso?.[i] }],
    // Bullish/bearish/neutral state coloring driven by the discrete `os` signal.
    palettes: [{
      target: 'hso',
      colors: ['#22c55e', '#ef4444', 'rgba(156,163,175,0.6)'],
      index: (d, i) => (d.hull_butterfly?.os?.[i] === 1 ? 0 : d.hull_butterfly?.os?.[i] === -1 ? 1 : 2),
    }],
  },
  {
    id: 'smart_money_flow', name: 'SMF Cloud (bridged)', priceStudy: true,
    plots: [
      { id: 'bOpen', title: 'SMF Basis Open', color: 'rgba(0,200,255,0.35)', width: 1, get: (d, i) => d.smart_money_flow?.b_open?.[i] },
      { id: 'bClose', title: 'SMF Basis Close', color: 'rgba(255,0,93,0.35)', width: 1, get: (d, i) => d.smart_money_flow?.b_close?.[i] },
      { id: 'upper', title: 'SMF Upper', color: 'rgba(0,200,255,0.55)', width: 1, get: (d, i) => d.smart_money_flow?.upper?.[i] },
      { id: 'lower', title: 'SMF Lower', color: 'rgba(255,0,93,0.55)', width: 1, get: (d, i) => d.smart_money_flow?.lower?.[i] },
    ],
    fills: [{ a: 'bOpen', b: 'bClose', color: 'rgba(0,200,255,0.18)', title: 'SMF Basis' }],
  },
];

/** Map of app indicator id → the study name to pass to `createStudy`. */
export const STUDY_NAME_BY_ID: Record<string, string> = Object.fromEntries(
  STUDY_SPECS.map((s) => [s.id, s.name]),
);

/** Builds one `CustomIndicator` from a spec. */
function buildStudy(pine: PineJS, spec: StudySpec): CustomIndicator {
  const palettes = spec.palettes ?? [];
  const valuePlots = spec.plots;
  const shapePlots = spec.shapes ?? [];

  // Plot descriptors in main() output order: value plots, shape plots, colorers.
  const plots: any[] = valuePlots.map((p) => ({ id: p.id, type: 'line' }));
  shapePlots.forEach((s) => plots.push({ id: s.id, type: 'shapes' }));
  palettes.forEach((pal, k) => {
    plots.push({ id: `__color${k}`, type: 'colorer', target: pal.target, palette: `pal${k}` });
  });

  // Per-plot style metadata + defaults.
  const stylesMeta: Record<string, any> = {};
  const stylesDefaults: Record<string, any> = {};
  valuePlots.forEach((p) => {
    stylesMeta[p.id] = { title: p.title, histogramBase: 0, isHidden: false };
    stylesDefaults[p.id] = {
      linestyle: p.dashed ? LINE_DASHED : LINE_SOLID,
      linewidth: p.width ?? 1,
      plottype: p.histogram ? PLOT_HISTOGRAM : PLOT_LINE,
      trackPrice: false,
      transparency: 0,
      visible: true,
      color: p.color,
    };
  });
  shapePlots.forEach((s) => {
    stylesMeta[s.id] = { title: s.title, isHidden: false, size: 'tiny' };
    stylesDefaults[s.id] = {
      plottype: s.shape,
      location: s.location,
      color: s.color,
      textColor: s.color,
      transparency: 0,
      visible: true,
    };
  });

  // Palettes (metainfo + defaults).
  const palettesMeta: Record<string, any> = {};
  const palettesDefaults: Record<string, any> = {};
  palettes.forEach((pal, k) => {
    const colorsMeta: Record<string, any> = {};
    const colorsDef: Record<string, any> = {};
    // Colorer palettes own the drawn stroke — their `style`/`width` override
    // the value-plot defaults. Inherit dashed/width from the target plot so
    // `dashed: true` on e.g. chandelier_exit actually renders.
    const target = valuePlots.find((p) => p.id === pal.target);
    const palStyle = target?.dashed ? LINE_DASHED : LINE_SOLID;
    const palWidth = target?.width ?? 1;
    pal.colors.forEach((color, ci) => {
      colorsMeta[ci] = { name: `Color ${ci}` };
      colorsDef[ci] = { color, width: palWidth, style: palStyle };
    });
    palettesMeta[`pal${k}`] = { colors: colorsMeta, addDefaultColor: false };
    palettesDefaults[`pal${k}`] = { colors: colorsDef };
  });

  // Bands (reference hlines).
  const bandsMeta = (spec.bands ?? []).map((_, i) => ({ id: `band${i}`, name: `Band ${i}` }));
  const bandsDefaults = (spec.bands ?? []).map((b) => ({
    color: b.color, linestyle: LINE_DASHED, linewidth: 1, value: b.value, visible: true,
  }));

  // Filled areas (between two value plots).
  const filledAreas = (spec.fills ?? []).map((f, i) => ({
    id: `fill${i}`, objAId: f.a, objBId: f.b, title: f.title, type: 'plot_plot',
  }));
  const filledAreasStyle: Record<string, any> = {};
  (spec.fills ?? []).forEach((f, i) => {
    filledAreasStyle[`fill${i}`] = { color: f.color, transparency: 80, visible: true };
  });

  const format = spec.priceStudy
    ? { type: 'inherit' }
    : { type: 'price', precision: spec.precision ?? 2 };

  const metainfo: any = {
    _metainfoVersion: 53,
    id: `${spec.id}@tv-bridged-1`,
    name: spec.name,
    description: spec.name,
    shortDescription: spec.name,
    isCustomIndicator: true,
    is_price_study: spec.priceStudy,
    format,
    plots,
    inputs: [],
    bands: bandsMeta.length ? bandsMeta : undefined,
    palettes: Object.keys(palettesMeta).length ? palettesMeta : undefined,
    filledAreas: filledAreas.length ? filledAreas : undefined,
    defaults: {
      styles: stylesDefaults,
      palettes: Object.keys(palettesDefaults).length ? palettesDefaults : undefined,
      bands: bandsDefaults.length ? bandsDefaults : undefined,
      filledAreasStyle: Object.keys(filledAreasStyle).length ? filledAreasStyle : undefined,
      precision: spec.precision ?? 2,
      inputs: {},
    },
    styles: stylesMeta,
  };

  const valueGetters = valuePlots.map((p) => p.get);
  const shapeGetters = shapePlots.map((s) => s.get);
  const paletteGetters = palettes.map((p) => p.index);
  const total = valueGetters.length + shapeGetters.length + paletteGetters.length;

  return {
    name: spec.name,
    metainfo,
    constructor: function (this: any) {
      this.init = function (context: any) {
        this._context = context;
      };
      this.main = function (context: any) {
        const out = new Array(total).fill(NaN);
        const series = tvStore.loaded;
        if (!series) return out;
        const t = pine.Std.time(context);
        const i = indexAtTimeMs(series, t);
        if (i < 0) return out;
        const ind = (series.response.indicators ?? {}) as Record<string, any>;
        for (let k = 0; k < valueGetters.length; k++) {
          out[k] = num(valueGetters[k](ind, i));
        }
        for (let k = 0; k < shapeGetters.length; k++) {
          out[valueGetters.length + k] = num(shapeGetters[k](ind, i));
        }
        const paletteBase = valueGetters.length + shapeGetters.length;
        for (let k = 0; k < paletteGetters.length; k++) {
          const idx = paletteGetters[k](ind, i);
          out[paletteBase + k] = typeof idx === 'number' && isFinite(idx) ? idx : NaN;
        }
        return out;
      };
    },
  } as unknown as CustomIndicator;
}

/** Widget `custom_indicators_getter`: returns every bridged study. */
export function customIndicatorsGetter(pine: PineJS): Promise<CustomIndicator[]> {
  return Promise.resolve(STUDY_SPECS.map((spec) => buildStudy(pine, spec)));
}
