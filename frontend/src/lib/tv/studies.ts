/**
 * The chart's custom studies. Two kinds live here:
 *
 * 1. **Bridged** ({@link STUDY_SPECS}) — the backend computes the indicator and
 *    returns arrays aligned to the bars. Each study's `main()` looks up the
 *    current bar (by time) in {@link tvStore} and returns the precomputed values
 *    for that bar; no in-browser math. Plots map positionally to
 *    `metainfo.plots`; colorer plots (for per-bar histogram/line colors) follow
 *    the value plots and return a palette index.
 *
 * 2. **Computed** ({@link COMPUTED_STUDY_SPECS}) — the study does the math
 *    in-browser with PineJS, like a native library study. These need no backend
 *    round-trip, take their parameters as real study inputs (so they recalculate
 *    live and survive symbol/resolution changes), and update with the real-time
 *    bar as ticks arrive.
 *
 * For both, `id` matches the app's indicator config id and `name` is the study
 * name passed to `activeChart().createStudy(name)` — see {@link STUDY_CATALOGUE}.
 */
import { tvStore, indexAtTimeMs } from './store';
import type { CustomIndicator, IContext, PineJS, StudyInputValue } from './charting_library';
import { studyPalette } from './theme';

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

// ── Smart Money Flow Cloud ────────────────────────────────────────────────────
// Mirrors the plotting of "Smart Money Flow Cloud [BOSWaves]" (PineScript):
// a regime-colored basis cloud, adaptive bands shaded down to price, Buy/Sell
// labels on regime switches, ✦ retest chars, and regime-painted candles.
// The bands themselves are invisible in Pine (100% transparent) — only their
// fill against price is drawn.

const SMF_BULL = studyPalette.bull;
const SMF_BEAR = studyPalette.bear;
/** Palette slot for "don't draw this fill on this bar". */
const SMF_OFF = studyPalette.transparent;
/** Buy/Sell label text — dark, since the label itself is filled with a bright regime color. */

const smf = (d: Record<string, any>) => d.smart_money_flow;
/** Regime palette index: 0 = bull (last_signal +1), 1 = bear (-1). */
const smfRegime: Getter = (d, i) => (smf(d)?.last_signal?.[i] === 1 ? 0 : 1);
/** True only on bars where the regime matches, for the one-sided band fills. */
const smfFillIndex = (want: number): Getter => (d, i, o) =>
  o.showBands && smf(d)?.last_signal?.[i] === want ? 0 : 1;
const smfFlag = (key: string, input: string): Getter => (d, i, o) =>
  o[input] && smf(d)?.[key]?.[i] ? 1 : null;

const SMART_MONEY_FLOW_SPEC: StudySpec = {
  id: 'smart_money_flow', name: 'SMF Cloud (bridged)', priceStudy: true,
  inputs: [
    { id: 'showCloud', name: 'Show Cloud', defval: false },
    { id: 'showBands', name: 'Show Adaptive Bands', defval: true },
    { id: 'paintBars', name: 'Color Bars', defval: true },
    { id: 'showSwitch', name: 'Show Buy/Sell Signals', defval: true },
    { id: 'showDots', name: 'Trend Retest Signals', defval: true },
  ],
  plots: [
    {
      id: 'bOpen', title: 'Basis Open', color: SMF_BULL, width: 1,
      get: (d, i, o) => (o.showCloud ? smf(d)?.b_open?.[i] : null),
    },
    {
      id: 'bClose', title: 'Basis Close', color: SMF_BULL, width: 2,
      get: (d, i, o) => (o.showCloud ? smf(d)?.b_close?.[i] : null),
    },
    {
      id: 'upper', title: 'Upper Band', color: SMF_BEAR, width: 1,
      transparency: 100, hiddenStyle: true,
      get: (d, i, o) => (o.showBands ? smf(d)?.upper?.[i] : null),
    },
    {
      id: 'lower', title: 'Lower Band', color: SMF_BULL, width: 1,
      transparency: 100, hiddenStyle: true,
      get: (d, i, o) => (o.showBands ? smf(d)?.lower?.[i] : null),
    },
    // Fill anchor for the band shading — Pine's `plot(close, display=none)`.
    {
      id: 'price', title: 'Price', color: SMF_BULL, width: 1,
      transparency: 100, hiddenStyle: true,
      fromBar: (pine, context) => pine.Std.close(context),
    },
  ],
  // Both basis lines follow the regime color, like Pine's `st.barCol`.
  palettes: [
    { target: 'bOpen', colors: [SMF_BULL, SMF_BEAR], index: smfRegime },
    { target: 'bClose', colors: [SMF_BULL, SMF_BEAR], index: smfRegime },
  ],
  fills: [
    {
      a: 'bOpen', b: 'bClose', title: 'Basis Cloud', color: SMF_BULL, transparency: 75,
      palette: { colors: [SMF_BULL, SMF_BEAR], index: smfRegime },
    },
    // Pine gradients these from the band toward price; the library only does
    // solid fills, so they run lighter than Pine's 40 to stay readable.
    {
      a: 'upper', b: 'price', title: 'Bear Fill', color: SMF_BEAR, transparency: 70,
      palette: { colors: [SMF_BEAR, SMF_OFF], index: smfFillIndex(-1) },
    },
    {
      a: 'lower', b: 'price', title: 'Bull Fill', color: SMF_BULL, transparency: 70,
      palette: { colors: [SMF_BULL, SMF_OFF], index: smfFillIndex(1) },
    },
  ],
  // shapes: [
  //   {
  //     id: 'buy', title: 'Buy', shape: 'shape_label_up', location: 'BelowBar',
  //     color: SMF_BULL, text: 'Buy', textColor: SMF_LABEL_TEXT, size: 'small',
  //     get: smfFlag('switch_up', 'showSwitch'),
  //   },
  //   {
  //     id: 'sell', title: 'Sell', shape: 'shape_label_down', location: 'AboveBar',
  //     color: SMF_BEAR, text: 'Sell', textColor: SMF_LABEL_TEXT, size: 'small',
  //     get: smfFlag('switch_down', 'showSwitch'),
  //   },
  // ],
  chars: [
    {
      id: 'bullDot', title: 'Bullish Retest', char: '✦', location: 'BelowBar',
      color: SMF_BULL, get: smfFlag('bull_dot', 'showDots'),
    },
    {
      id: 'bearDot', title: 'Bearish Retest', char: '✦', location: 'AboveBar',
      color: SMF_BEAR, get: smfFlag('bear_dot', 'showDots'),
    },
  ],
  barColors: {
    colors: [SMF_BULL, SMF_BEAR],
    index: (d, i, o) => (o.paintBars ? smfRegime(d, i, o) : null),
  },
};

/** Declarative catalogue of every bridged indicator, keyed by app config id. */
export const STUDY_SPECS: StudySpec[] = [
  // ── Separate-pane oscillators ──────────────────────────────────────────────
  // (RSI lives in COMPUTED_STUDY_SPECS — it is calculated in-browser.)
  {
    id: 'bvc', name: 'BVC (bridged)', priceStudy: false, precision: 2,
    plots: [{ id: 'bvc', title: 'BVC', color: studyPalette.violet, width: 2, get: (d, i) => d.bvc?.[i] }],
    bands: [{ value: 0, color: studyPalette.zeroLine }],
  },
  {
    id: 'kalman_zscore', name: 'Kalman Z-Score (bridged)', priceStudy: false, precision: 2,
    plots: [{ id: 'z', title: 'Kalman Z-Score', color: studyPalette.cyan, width: 2, get: (d, i) => d.kalman_zscore?.[i] }],
    bands: [
      { value: 2, color: studyPalette.overbought },
      { value: -2, color: studyPalette.oversold },
      { value: 0, color: studyPalette.zeroLine },
    ],
  },
  {
    id: 'yz_volatility', name: 'YZ Volatility (bridged)', priceStudy: false, precision: 4,
    plots: [{ id: 'yz', title: 'YZ Volatility', color: studyPalette.pink, width: 2, get: (d, i) => d.yz_volatility?.[i] }],
  },
  {
    id: 'gkyz_volatility', name: 'GKYZ Volatility (bridged)', priceStudy: false, precision: 3,
    plots: [{ id: 'gkyz', title: 'GKYZ', color: studyPalette.orange, width: 2, get: (d, i) => d.gkyz_volatility?.[i] }],
    bands: [
      { value: 0.8, color: studyPalette.overboughtStrong },
      { value: 0.2, color: studyPalette.oversoldStrong },
    ],
  },
  {
    id: 'matrix_series', name: 'Matrix Series (bridged)', priceStudy: false, precision: 1,
    inputs: [
      { id: 'showDot', name: 'Show Watch/Warning Point', defval: true },
    ],
    plots: [
      { id: 'sup', title: 'MS Support', color: studyPalette.red, width: 2, get: (d, i) => d.matrix_series?.support_line?.[i] },
      { id: 'res', title: 'MS Resistance', color: studyPalette.green, width: 2, get: (d, i) => d.matrix_series?.resistance_line?.[i] },
      { id: 'up', title: 'MS Up', color: studyPalette.teal, width: 1, get: (d, i) => d.matrix_series?.up_line?.[i] },
      { id: 'down', title: 'MS Down', color: studyPalette.yellow, width: 1, get: (d, i) => d.matrix_series?.down_line?.[i] },
      { id: 'hh', title: 'MS HH', color: studyPalette.greenFill, width: 1, get: (d, i) => d.matrix_series?.hh?.[i] },
      { id: 'll', title: 'MS LL', color: studyPalette.redFill, width: 1, get: (d, i) => d.matrix_series?.ll?.[i] },
    ],
    fills: [{ a: 'hh', b: 'll', color: studyPalette.rangeFill, title: 'MS Range' }],
    // Amber circles marking overbought/oversold watch points (Pine's UP/DOWN
    // Shape plots). UPshape is non-na iff `up > ob`; DOWNshape iff `down < os`
    // (the h01/h02/l01/l02 split only sets the y-level), so we anchor each dot
    // to its line value at the moment the threshold is crossed.
    shapes: [
      {
        id: 'upDot', title: 'UP Shape', shape: 'shape_circle', location: 'Absolute',
        color: studyPalette.amber, size: 'tiny',
        get: (d, i, o) => {
          if (!o.showDot) return null;
          const up = d.matrix_series?.up_line?.[i];
          return typeof up === 'number' && up > 200 ? up : null;
        },
      },
      {
        id: 'downDot', title: 'DOWN Shape', shape: 'shape_circle', location: 'Absolute',
        color: studyPalette.amber, size: 'tiny',
        get: (d, i, o) => {
          if (!o.showDot) return null;
          const down = d.matrix_series?.down_line?.[i];
          return typeof down === 'number' && down < -200 ? down : null;
        },
      },
    ],
  },
  {
    id: 'squeeze_ttm', name: 'Squeeze TTM (bridged)', priceStudy: false, precision: 2,
    plots: [{ id: 'hist', title: 'Squeeze TTM', color: studyPalette.neutral, histogram: true, get: (d, i) => d.squeeze_ttm?.histogram?.[i] }],
    palettes: [{
      target: 'hist',
      colors: [studyPalette.neutralBar, studyPalette.redBar, studyPalette.greenBar],
      index: (d, i) => d.squeeze_ttm?.squeeze_state?.[i],
    }],
  },
  {
    id: 'williams_vix_fix', name: 'Williams VIX Fix (bridged)', priceStudy: false, precision: 2,
    plots: [{ id: 'wvf', title: 'WVF', color: studyPalette.greenBar, histogram: true, get: (d, i) => d.williams_vix_fix?.wvf?.[i] }],
    palettes: [{
      target: 'wvf',
      colors: [studyPalette.greenBar, studyPalette.yellowBar],
      index: (d, i) => (d.williams_vix_fix?.filtered?.[i] ? 1 : 0),
    }],
    // Blue dot just below the histogram baseline on filtered-entry (cond_fe) bars.
    shapes: [{
      id: 'fe', title: 'FE', shape: 'shape_circle', location: 'Absolute', color: studyPalette.blue,
      get: (d, i) => (d.williams_vix_fix?.cond_fe?.[i] ? 1 : null),
    }],
  },

  // ── Price-pane overlays ─────────────────────────────────────────────────────
  {
    id: 'atr_trailing', name: 'ATR Trailing Stop (bridged)', priceStudy: true,
    plots: [{ id: 'atr', title: 'Trailing Stop', color: studyPalette.green, width: 2, dashed: true, get: (d, i) => d.atr_trailing?.[i] }],
  },
  {
    id: 'vwap', name: 'VWAP Bands (bridged)', priceStudy: true,
    plots: [
      { id: 'high', title: 'VWAP High', color: studyPalette.blue, width: 2, get: (d, i) => d.vwap_highest?.[i] },
      { id: 'low', title: 'VWAP Low', color: studyPalette.orange, width: 2, get: (d, i) => d.vwap_lowest?.[i] },
    ],
  },
  {
    id: 'kama', name: 'KAMA (bridged)', priceStudy: true,
    plots: [{ id: 'kama', title: 'KAMA', color: studyPalette.yellow, width: 2, get: (d, i) => d.kama?.[i] }],
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
      { id: 'frama', title: 'G-FRAMA', color: studyPalette.neutral, width: 2, get: (d, i) => d.gaussian_frama?.frama?.[i] },
      { id: 'longV', title: 'G-FRAMA Long', color: studyPalette.blue, width: 1, dashed: true, get: (d, i) => d.gaussian_frama?.long_v?.[i] },
      { id: 'shortV', title: 'G-FRAMA Short', color: studyPalette.red, width: 1, dashed: true, get: (d, i) => d.gaussian_frama?.short_v?.[i] },
    ],
    // Regime coloring: blue (qb=+1, bullish) / red (qb=-1, bearish) / gray (neutral).
    palettes: [{
      target: 'frama',
      colors: [studyPalette.blue, studyPalette.red, studyPalette.neutral],
      index: (d, i) => (d.gaussian_frama?.qb?.[i] === 1 ? 0 : d.gaussian_frama?.qb?.[i] === -1 ? 1 : 2),
    }],
    fills: [{ a: 'longV', b: 'shortV', color: 'rgba(99,102,241,0.08)', title: 'G-FRAMA Cloud' }],
  },
  {
    id: 'hull_butterfly', name: 'Hull Butterfly Oscillator (bridged)', priceStudy: false, precision: 2,
    plots: [{ id: 'hso', title: 'HBO', color: studyPalette.neutral, histogram: true, get: (d, i) => d.hull_butterfly?.hso?.[i] }],
    // Bullish/bearish/neutral state coloring driven by the discrete `os` signal.
    palettes: [{
      target: 'hso',
      colors: [studyPalette.green, studyPalette.red, 'rgba(156,163,175,0.6)'],
      index: (d, i) => (d.hull_butterfly?.os?.[i] === 1 ? 0 : d.hull_butterfly?.os?.[i] === -1 ? 1 : 2),
    }],
  },
  SMART_MONEY_FLOW_SPEC,
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
          trackPrice: false, transparency: 0, visible: true, color: '#6366f1',
        },
        fast: {
          linestyle: LINE_SOLID, linewidth: 2, plottype: PLOT_LINE,
          trackPrice: false, transparency: 0, visible: true, color: '#f59e0b',
        },
      },
      bands: [
        { color: 'rgba(239, 68, 68, 0.5)', linestyle: LINE_DASHED, linewidth: 1, value: 70, visible: true },
        { color: 'rgba(34, 197, 94, 0.5)', linestyle: LINE_DASHED, linewidth: 1, value: 30, visible: true },
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
];

/**
 * Every study the chart can create, bridged and computed alike, in the order the
 * Indicators panel lists them.
 */
export const STUDY_CATALOGUE: { id: string; name: string; computed: boolean }[] = [
  ...COMPUTED_STUDY_SPECS.map((s) => ({ id: s.id, name: s.name, computed: true })),
  ...STUDY_SPECS.map((s) => ({ id: s.id, name: s.name, computed: false })),
];

/** Study inputs for a computed study, or null when the study is bridged. */
export function computedStudyInputs(
  id: string,
  params: Record<string, number>,
): Record<string, StudyInputValue> | null {
  const spec = COMPUTED_STUDY_SPECS.find((s) => s.id === id);
  return spec ? spec.inputsFrom(params) : null;
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

/** Widget `custom_indicators_getter`: every computed + bridged study. */
export function customIndicatorsGetter(pine: PineJS): Promise<CustomIndicator[]> {
  return Promise.resolve([
    ...COMPUTED_STUDY_SPECS.map((spec) => spec.build(pine)),
    ...STUDY_SPECS.map((spec) => buildStudy(pine, spec)),
  ]);
}
