/**
 * The chart's custom studies. Three execution paths live here:
 *
 * 1. **Bridged** ({@link STUDY_SPECS}) — the backend computes legacy
 *    indicators and returns arrays aligned to the bars. Each study's `main()`
 *    looks up the current bar by time.
 *
 * 2. **PineJS** ({@link COMPUTED_STUDY_SPECS}) — small native custom studies
 *    that calculate in TradingView's browser runtime.
 *
 * 3. **OpenScript** — browser-side studies compiled and run by
 *    `openalgo-script`. They receive the same OHLCV bars as the chart and need
 *    no indicator payload from the Python backend.
 *
 * In every path, `id` matches the app indicator config and `name` is passed to
 * `activeChart().createStudy(name)` — see {@link STUDY_CATALOGUE}.
 */
import { tvStore, indexAtTimeMs } from './store';
import type {
  CustomIndicator,
  IContext,
  IPineStudyResult,
  LibraryPineStudy,
  PineJS,
  StudyInputValue,
} from './charting_library';
import { studyPalette } from './theme';
import {
  ATR_TRAILING_OPENSCRIPT,
  BVC_OPENSCRIPT,
  CHANDELIER_EXIT_OPENSCRIPT,
  GKYZ_VOLATILITY_OPENSCRIPT,
  GAUSSIAN_FRAMA_OPENSCRIPT,
  HULL_BUTTERFLY_OPENSCRIPT,
  KALMAN_ZSCORE_OPENSCRIPT,
  KAMA_OPENSCRIPT,
  LR_PREDICTION_OPENSCRIPT,
  MATRIX_SERIES_OPENSCRIPT,
  SMART_MONEY_FLOW_OPENSCRIPT,
  SQUEEZE_TTM_OPENSCRIPT,
  VWAP_BANDS_OPENSCRIPT,
  WILLIAMS_VIX_FIX_OPENSCRIPT,
  YZ_VOLATILITY_OPENSCRIPT,
  buildOpenScriptStudy,
} from './openscript';

// LineStudyPlotStyle
const PLOT_LINE = 0;
const PLOT_HISTOGRAM = 1;
// LineStyle
const LINE_SOLID = 0;
const LINE_DASHED = 2;

/**
 * Reads one bar's value out of the bridged indicator payload. `opts` holds the
 * study's boolean inputs (see {@link BoolInputSpec}), so a getter can blank
 * itself out when its toggle is off — the equivalent of Pine's `show… ? v : na`.
 */
type Getter = (
  ind: Record<string, any>,
  i: number,
  opts: Record<string, boolean>,
) => number | null | undefined;

interface PlotSpec {
  id: string;
  title: string;
  color: string;
  width?: number;
  dashed?: boolean;
  histogram?: boolean;
  /** 0–100; 100 draws nothing but still anchors fills (Pine's `display=none` helper plots). */
  transparency?: number;
  /** Hides the plot's row in the study settings' Style tab. */
  hiddenStyle?: boolean;
  get?: Getter;
  /**
   * Takes the value from the chart's own bars instead of the bridged payload —
   * used for plots that just mirror price (Pine's `plot(close)` fill anchor).
   */
  fromBar?: (pine: PineJS, context: IContext) => number;
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
  /** 0–100, defaults to 80. */
  transparency?: number;
  /** Per-bar fill color, like a colorer plot but targeting the filled area. */
  palette?: { colors: string[]; index: Getter };
}
interface ShapeSpec {
  id: string;
  title: string;
  /** PlotShapeId, e.g. 'shape_arrow_up'. */
  shape: string;
  /** MarkLocation, e.g. 'AboveBar'. */
  location: string;
  color: string;
  /** Label text, for the `shape_label_*` shapes. */
  text?: string;
  /** Text color; defaults to `color`, which hides the text inside a filled label. */
  textColor?: string;
  /** PlotSymbolSize, defaults to 'tiny'. */
  size?: string;
  /** Returns the anchor value when the shape should show, else null/NaN. */
  get: Getter;
}
interface CharSpec {
  id: string;
  title: string;
  /** The glyph drawn at the bar, e.g. '✦'. */
  char: string;
  /** MarkLocation, e.g. 'AboveBar'. */
  location: string;
  color: string;
  /** PlotSymbolSize, defaults to 'tiny'. */
  size?: string;
  /** Returns non-NaN when the char should show. */
  get: Getter;
}
/** A boolean study input, surfaced as a checkbox in the study settings. */
interface BoolInputSpec {
  id: string;
  name: string;
  defval: boolean;
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
  chars?: CharSpec[];
  inputs?: BoolInputSpec[];
  /** Recolors the chart's own candles per bar (Pine's `plotcandle` bar painting). */
  barColors?: { colors: string[]; index: Getter };
}

function num(v: number | null | undefined): number {
  return typeof v === 'number' && isFinite(v) ? v : NaN;
}


/** Declarative catalogue of every bridged indicator, keyed by app config id. */
export const STUDY_SPECS: StudySpec[] = [
  // ── Separate-pane oscillators ──────────────────────────────────────────────
  // (RSI lives in COMPUTED_STUDY_SPECS — it is calculated in-browser.)

  // ── Price-pane overlays ─────────────────────────────────────────────────────
];

// ── PineJS-computed studies ───────────────────────────────────────────────────

/** A study that computes its own values in the browser via PineJS. */
export interface ComputedStudySpec {
  id: string;
  name: string;
  /**
   * Maps the app's indicator params (the Indicators panel sliders) onto the
   * study's inputs, so one set of controls drives both kinds of study.
   */
  inputsFrom: (params: Record<string, number>) => Record<string, StudyInputValue>;
  build: (pine: PineJS) => CustomIndicator;
}

/** Price sources selectable by the `source` input, by input value. */
const SOURCE_FNS: Record<string, (pine: PineJS, context: IContext) => number> = {
  open: (pine, c) => pine.Std.open(c),
  high: (pine, c) => pine.Std.high(c),
  low: (pine, c) => pine.Std.low(c),
  close: (pine, c) => pine.Std.close(c),
  hl2: (pine, c) => pine.Std.hl2(c),
  hlc3: (pine, c) => pine.Std.hlc3(c),
  ohlc4: (pine, c) => pine.Std.ohlc4(c),
};

const RSI_NAME = 'RSI';

/**
 * Relative Strength Index over two lengths (slow + fast), computed in-browser.
 *
 * Wilder's definition, the same one Pine's `ta.rsi` implements: average gain and
 * average loss are RMA-smoothed (an EMA with `alpha = 1/length`), then
 * `Std.rsi(avgGain, avgLoss)` turns them into the 0–100 oscillator.
 */
function buildRsiStudy(pine: PineJS): CustomIndicator {
  const metainfo: any = {
    _metainfoVersion: 53,
    id: 'rsi@tv-custom-1',
    name: RSI_NAME,
    description: RSI_NAME,
    shortDescription: 'RSI',
    isCustomIndicator: true,
    is_price_study: false,
    format: { type: 'price', precision: 2 },
    plots: [
      { id: 'slow', type: 'line' },
      { id: 'fast', type: 'line' },
    ],
    inputs: [
      { id: 'length', name: 'Length', type: 'integer', defval: 14, min: 2, max: 500 },
      { id: 'fast_length', name: 'Fast length', type: 'integer', defval: 5, min: 2, max: 500 },
      {
        id: 'source', name: 'Source', type: 'source', defval: 'close',
        options: Object.keys(SOURCE_FNS),
      },
    ],
    bands: [
      { id: 'upper', name: 'Overbought' },
      { id: 'lower', name: 'Oversold' },
    ],
    styles: {
      slow: { title: 'RSI', histogramBase: 0, isHidden: false },
      fast: { title: 'RSI Fast', histogramBase: 0, isHidden: false },
    },
    defaults: {
      styles: {
        slow: {
          linestyle: LINE_SOLID, linewidth: 2, plottype: PLOT_LINE,
          trackPrice: false, transparency: 0, visible: true, color: studyPalette.rsi,
        },
        fast: {
          linestyle: LINE_SOLID, linewidth: 2, plottype: PLOT_LINE,
          trackPrice: false, transparency: 0, visible: true, color: studyPalette.rsiSignal,
        },
      },
      bands: [
        { color: studyPalette.rsiUpper, linestyle: LINE_DASHED, linewidth: 1, value: 70, visible: true },
        { color: studyPalette.rsiLower, linestyle: LINE_DASHED, linewidth: 1, value: 30, visible: true },
      ],
      precision: 2,
      inputs: { length: 14, fast_length: 5, source: 'close' },
    },
  };

  return {
    name: RSI_NAME,
    metainfo,
    constructor: function (this: any) {
      this.init = function (context: any, inputCallback: any) {
        this._context = context;
        this._input = inputCallback;
      };
      this.main = function (context: any, inputCallback: any) {
        this._context = context;
        this._input = inputCallback;

        const slowLength = Math.max(2, Math.round(this._input(0)));
        const fastLength = Math.max(2, Math.round(this._input(1)));
        const sourceFn = SOURCE_FNS[this._input(2)] ?? SOURCE_FNS.close;

        // Every context var / stateful Std call must happen on every bar in the
        // same order: PineJS keys its per-bar storage by call order, so a
        // conditional call would shift the slots and corrupt the series.
        const source = context.new_var(sourceFn(pine, context));
        const delta = pine.Std.change(source);
        const gain = context.new_var(Math.max(delta, 0));
        const loss = context.new_var(-Math.min(delta, 0));

        return [
          pine.Std.rsi(
            pine.Std.rma(gain, slowLength, context),
            pine.Std.rma(loss, slowLength, context),
          ),
          pine.Std.rsi(
            pine.Std.rma(gain, fastLength, context),
            pine.Std.rma(loss, fastLength, context),
          ),
        ];
      };
    },
  } as unknown as CustomIndicator;
}

const RVOL_NAME = 'RVOL';

/**
 * Current volume divided by the simple average of the prior N bars. Excluding
 * the current bar from the denominator keeps a breakout spike from diluting
 * its own relative-volume reading.
 */
function buildRvolStudy(pine: PineJS): CustomIndicator {
  const metainfo = {
    _metainfoVersion: 53,
    id: 'rvol@tv-custom-1',
    name: RVOL_NAME,
    description: RVOL_NAME,
    shortDescription: RVOL_NAME,
    isCustomIndicator: true,
    is_price_study: false,
    format: { type: 'price', precision: 2 },
    plots: [{ id: 'rvol', type: 'line' }],
    inputs: [
      { id: 'length', name: 'Length', type: 'integer', defval: 20, min: 2, max: 500 },
    ],
    bands: [
      { id: 'average', name: 'Average volume' },
      { id: 'confirmation', name: 'Breakout confirmation' },
    ],
    styles: {
      rvol: { title: 'RVOL', histogramBase: 0, isHidden: false },
    },
    defaults: {
      styles: {
        rvol: {
          linestyle: LINE_SOLID, linewidth: 2, plottype: PLOT_HISTOGRAM,
          trackPrice: false, transparency: 0, visible: true, color: studyPalette.cyan,
        },
      },
      bands: [
        { color: studyPalette.zeroLine, linestyle: LINE_DASHED, linewidth: 1, value: 1, visible: true },
        { color: studyPalette.rsiUpper, linestyle: LINE_DASHED, linewidth: 1, value: 1.5, visible: true },
      ],
      precision: 2,
      inputs: { length: 20 },
    },
  };

  return {
    name: RVOL_NAME,
    metainfo,
    constructor: function (this: LibraryPineStudy<IPineStudyResult>) {
      this.main = function (context, inputCallback) {
        const length = Math.max(2, Math.round(inputCallback<number>(0)));
        const currentVolume = pine.Std.volume(context);
        const volumeSeries = context.new_var(currentVolume);
        const priorVolumeSeries = context.new_var(volumeSeries.get(1));
        const priorAverage = pine.Std.sma(priorVolumeSeries, length, context);

        return [priorAverage > 0 ? currentVolume / priorAverage : NaN];
      };
    },
  } as unknown as CustomIndicator;
}


export const COMPUTED_STUDY_SPECS: ComputedStudySpec[] = [
  {
    id: 'rsi',
    name: RSI_NAME,
    // `period` is the app's existing RSI slider; `fast_period` drives the second line.
    inputsFrom: (params) => ({
      length: params.period ?? 14,
      fast_length: params.fast_period ?? 5,
      source: 'close',
    }),
    build: buildRsiStudy,
  },
  {
    id: 'rvol',
    name: RVOL_NAME,
    inputsFrom: (params) => ({ length: params.period ?? 20 }),
    build: buildRvolStudy,
  },
];

const OPENSCRIPT_STUDY_SPECS = [
  ATR_TRAILING_OPENSCRIPT,
  BVC_OPENSCRIPT,
  KAMA_OPENSCRIPT,
  CHANDELIER_EXIT_OPENSCRIPT,
  HULL_BUTTERFLY_OPENSCRIPT,
  KALMAN_ZSCORE_OPENSCRIPT,
  GAUSSIAN_FRAMA_OPENSCRIPT,
  YZ_VOLATILITY_OPENSCRIPT,
  LR_PREDICTION_OPENSCRIPT,
  WILLIAMS_VIX_FIX_OPENSCRIPT,
  GKYZ_VOLATILITY_OPENSCRIPT,
  SQUEEZE_TTM_OPENSCRIPT,
  VWAP_BANDS_OPENSCRIPT,
  MATRIX_SERIES_OPENSCRIPT,
  SMART_MONEY_FLOW_OPENSCRIPT,
];

/**
 * Every study the chart can create, bridged and computed alike, in the order the
 * Indicators panel lists them.
 */
export const STUDY_CATALOGUE: { id: string; name: string; computed: boolean }[] = [
  ...COMPUTED_STUDY_SPECS.map((s) => ({ id: s.id, name: s.name, computed: true })),
  ...OPENSCRIPT_STUDY_SPECS.map((s) => ({ id: s.id, name: s.name, computed: true })),
  ...STUDY_SPECS.map((s) => ({ id: s.id, name: s.name, computed: false })),
];

/** Study inputs for an in-browser PineJS/OpenScript study, or null when bridged. */
export function computedStudyInputs(
  id: string,
  params: Record<string, number>,
): Record<string, StudyInputValue> | null {
  const pineStudy = COMPUTED_STUDY_SPECS.find((s) => s.id === id);
  if (pineStudy) return pineStudy.inputsFrom(params);
  const openScriptStudy = OPENSCRIPT_STUDY_SPECS.find((s) => s.id === id);
  return openScriptStudy ? openScriptStudy.inputsFrom(params) : null;
}

/** Map of app indicator id → the study name to pass to `createStudy`. */
export const STUDY_NAME_BY_ID: Record<string, string> = Object.fromEntries(
  STUDY_CATALOGUE.map((s) => [s.id, s.name]),
);

/** Builds one `CustomIndicator` from a spec. */
function buildStudy(pine: PineJS, spec: StudySpec): CustomIndicator {
  const palettes = spec.palettes ?? [];
  const valuePlots = spec.plots;
  const shapePlots = spec.shapes ?? [];
  const charPlots = spec.chars ?? [];
  const fillSpecs = spec.fills ?? [];
  const boolInputs = spec.inputs ?? [];

  // Palettes (metainfo + defaults), filled in as each palette is declared below.
  const palettesMeta: Record<string, any> = {};
  const palettesDefaults: Record<string, any> = {};
  const addPalette = (
    id: string,
    colors: string[],
    style: { width: number; style: number },
  ): void => {
    const colorsMeta: Record<string, any> = {};
    const colorsDef: Record<string, any> = {};
    const valToIndex: Record<number, number> = {};
    colors.forEach((color, ci) => {
      colorsMeta[ci] = { name: `Color ${ci}` };
      colorsDef[ci] = { color, width: style.width, style: style.style };
      valToIndex[ci] = ci;
    });
    // main() already returns palette indices, so the mapping is the identity —
    // but filled-area colorers require it to be present.
    palettesMeta[id] = { colors: colorsMeta, valToIndex, addDefaultColor: false };
    palettesDefaults[id] = { colors: colorsDef };
  };

  // Plot descriptors in main() output order: value plots, shape plots, char
  // plots, then every colorer (plot palettes, fill palettes, bar colorer).
  const plots: any[] = valuePlots.map((p) => ({ id: p.id, type: 'line' }));
  shapePlots.forEach((s) => plots.push({ id: s.id, type: 'shapes' }));
  charPlots.forEach((c) => plots.push({ id: c.id, type: 'chars' }));

  const colorerGetters: Getter[] = [];
  palettes.forEach((pal, k) => {
    plots.push({ id: `__color${k}`, type: 'colorer', target: pal.target, palette: `pal${k}` });
    colorerGetters.push(pal.index);
    // Colorer palettes own the drawn stroke — their `style`/`width` override
    // the value-plot defaults. Inherit dashed/width from the target plot so
    // `dashed: true` on e.g. chandelier_exit actually renders.
    const target = valuePlots.find((p) => p.id === pal.target);
    addPalette(`pal${k}`, pal.colors, {
      width: target?.width ?? 1,
      style: target?.dashed ? LINE_DASHED : LINE_SOLID,
    });
  });
  fillSpecs.forEach((f, i) => {
    if (!f.palette) return;
    plots.push({ id: `__fillColor${i}`, type: 'colorer', target: `fill${i}`, palette: `fpal${i}` });
    colorerGetters.push(f.palette.index);
    addPalette(`fpal${i}`, f.palette.colors, { width: 1, style: LINE_SOLID });
  });
  if (spec.barColors) {
    plots.push({ id: '__barColor', type: 'bar_colorer', palette: 'barPal' });
    colorerGetters.push(spec.barColors.index);
    addPalette('barPal', spec.barColors.colors, { width: 1, style: LINE_SOLID });
  }

  // Per-plot style metadata + defaults.
  const stylesMeta: Record<string, any> = {};
  const stylesDefaults: Record<string, any> = {};
  valuePlots.forEach((p) => {
    stylesMeta[p.id] = { title: p.title, histogramBase: 0, isHidden: p.hiddenStyle ?? false };
    stylesDefaults[p.id] = {
      linestyle: p.dashed ? LINE_DASHED : LINE_SOLID,
      linewidth: p.width ?? 1,
      plottype: p.histogram ? PLOT_HISTOGRAM : PLOT_LINE,
      trackPrice: false,
      transparency: p.transparency ?? 0,
      visible: true,
      color: p.color,
    };
  });
  shapePlots.forEach((s) => {
    // The library deep-clones metainfo and rejects undefined values, so
    // optional keys are only set when they have one.
    stylesMeta[s.id] = { title: s.title, isHidden: false, size: s.size ?? 'tiny' };
    if (s.text !== undefined) stylesMeta[s.id].text = s.text;
    stylesDefaults[s.id] = {
      plottype: s.shape,
      location: s.location,
      color: s.color,
      textColor: s.textColor ?? s.color,
      transparency: 0,
      visible: true,
    };
  });
  charPlots.forEach((c) => {
    stylesMeta[c.id] = { title: c.title, isHidden: false, size: c.size ?? 'tiny', char: c.char };
    stylesDefaults[c.id] = {
      char: c.char,
      location: c.location,
      color: c.color,
      textColor: c.color,
      transparency: 0,
      visible: true,
    };
  });

  // Bands (reference hlines).
  const bandsMeta = (spec.bands ?? []).map((_, i) => ({ id: `band${i}`, name: `Band ${i}` }));
  const bandsDefaults = (spec.bands ?? []).map((b) => ({
    color: b.color, linestyle: LINE_DASHED, linewidth: 1, value: b.value, visible: true,
  }));

  // Filled areas (between two value plots).
  const filledAreas = fillSpecs.map((f, i) => {
    const area: Record<string, any> = {
      id: `fill${i}`, objAId: f.a, objBId: f.b, title: f.title, type: 'plot_plot',
    };
    if (f.palette) area.palette = `fpal${i}`;
    return area;
  });
  const filledAreasStyle: Record<string, any> = {};
  fillSpecs.forEach((f, i) => {
    filledAreasStyle[`fill${i}`] = {
      color: f.color, transparency: f.transparency ?? 80, visible: true,
    };
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
    inputs: boolInputs.map((b) => ({ id: b.id, name: b.name, type: 'bool', defval: b.defval })),
    bands: bandsMeta.length ? bandsMeta : undefined,
    palettes: Object.keys(palettesMeta).length ? palettesMeta : undefined,
    filledAreas: filledAreas.length ? filledAreas : undefined,
    defaults: {
      styles: stylesDefaults,
      palettes: Object.keys(palettesDefaults).length ? palettesDefaults : undefined,
      bands: bandsDefaults.length ? bandsDefaults : undefined,
      filledAreasStyle: Object.keys(filledAreasStyle).length ? filledAreasStyle : undefined,
      precision: spec.precision ?? 2,
      inputs: Object.fromEntries(boolInputs.map((b) => [b.id, b.defval])),
    },
    styles: stylesMeta,
  };

  const markerGetters = [...shapePlots.map((s) => s.get), ...charPlots.map((c) => c.get)];
  const total = valuePlots.length + markerGetters.length + colorerGetters.length;
  const markerBase = valuePlots.length;
  const colorerBase = markerBase + markerGetters.length;

  return {
    name: spec.name,
    metainfo,
    constructor: function (this: any) {
      this.init = function (context: any) {
        this._context = context;
      };
      this.main = function (context: any, inputCallback: any) {
        const out = new Array(total).fill(NaN);
        // Inputs are read every bar (and before any early return) so the
        // library always sees the same call sequence.
        const opts: Record<string, boolean> = {};
        boolInputs.forEach((b, k) => {
          opts[b.id] = Boolean(inputCallback(k));
        });

        // Plots reading straight off the bars work with or without the bridged
        // payload, so they're filled in before the lookup can bail out.
        valuePlots.forEach((p, k) => {
          if (p.fromBar) out[k] = num(p.fromBar(pine, context));
        });

        const series = tvStore.loaded;
        if (!series) return out;
        const t = pine.Std.time(context);
        const i = indexAtTimeMs(series, t);
        if (i < 0) return out;
        const ind = (series.response.indicators ?? {}) as Record<string, any>;

        valuePlots.forEach((p, k) => {
          if (p.get) out[k] = num(p.get(ind, i, opts));
        });
        for (let k = 0; k < markerGetters.length; k++) {
          out[markerBase + k] = num(markerGetters[k](ind, i, opts));
        }
        for (let k = 0; k < colorerGetters.length; k++) {
          const idx = colorerGetters[k](ind, i, opts);
          out[colorerBase + k] = typeof idx === 'number' && isFinite(idx) ? idx : NaN;
        }
        return out;
      };
    },
  } as unknown as CustomIndicator;
}

/** Widget `custom_indicators_getter`: PineJS, OpenScript, then backend bridges. */
export function customIndicatorsGetter(pine: PineJS): Promise<CustomIndicator[]> {
  return Promise.resolve([
    ...COMPUTED_STUDY_SPECS.map((spec) => spec.build(pine)),
    ...OPENSCRIPT_STUDY_SPECS.map((spec) => buildOpenScriptStudy(pine, spec)),
    ...STUDY_SPECS.map((spec) => buildStudy(pine, spec)),
  ]);
}
