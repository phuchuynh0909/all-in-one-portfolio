import type { RegimeResponse } from '../services/regime';
import { formatChartTime } from '../services/timeseries';
import { primitives } from '../../theme/tokens';
import type {
  Bar,
  CustomIndicator,
  DatafeedConfiguration,
  ErrorCallback,
  HistoryCallback,
  IBasicDataFeed,
  IContext,
  IPineStudyResult,
  LibraryPineStudy,
  LibrarySymbolInfo,
  OnReadyCallback,
  PeriodParams,
  PineJS,
  ResolutionString,
  ResolveCallback,
  SearchSymbolsCallback,
  SubscribeBarsCallback,
} from './charting_library';

const DAILY = '1D' as ResolutionString;
const SUPPORTED_RESOLUTIONS = [DAILY];
const DAY_MS = 24 * 60 * 60 * 1000;
const LINE_SOLID = 0;
const LINE_DASHED = 2;
const PLOT_LINE = 0;
const PLOT_HISTOGRAM = 1;

export const REGIME_COLORS: Record<number, string> = {
  2: primitives.green[600],
  1: primitives.teal[500],
  0: primitives.neutral[400],
  [-1]: primitives.red[400],
  [-2]: primitives.red[600],
};

export const REGIME_FALLBACK = primitives.neutral[400];

export const TICA_LABEL_COLORS: Record<string, string> = {
  'Risk-On': primitives.green[500],
  Caution: primitives.amber[500],
  'Risk-Off': primitives.orange[600],
  Crisis: primitives.red[700],
};

export const REGIME_STUDIES = {
  kama: 'Regime KAMA',
  markov: 'Markov-KAMA Regime',
  probabilities: 'Regime Probability',
  yzPercentile: 'YZ Vol Percentile',
  ticaHmm: 'TICA+HMM Regime',
} as const;

const CONFIGURATION: DatafeedConfiguration = {
  supported_resolutions: SUPPORTED_RESOLUTIONS,
  supports_marks: false,
  supports_timescale_marks: false,
  supports_time: false,
  exchanges: [],
  symbols_types: [{ name: 'Stock', value: 'stock' }],
};

function finite(value: number | null | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : NaN;
}

export class RegimeStore {
  readonly data: RegimeResponse;
  readonly bars: Bar[];
  private readonly timesMs: number[];
  private readonly indexByTimeMs: Map<number, number>;

  constructor(data: RegimeResponse) {
    this.data = data;
    this.timesMs = data.timestamps.map((timestamp) => formatChartTime(timestamp) * 1000);
    this.indexByTimeMs = new Map(this.timesMs.map((time, index) => [time, index]));
    this.bars = data.timestamps.flatMap((_, index) => {
      const open = data.open[index];
      const high = data.high[index];
      const low = data.low[index];
      const close = data.close[index];
      if (![open, high, low, close].every(Number.isFinite)) return [];
      return [{ time: this.timesMs[index], open, high, low, close } satisfies Bar];
    });
  }

  indexAt(timeMs: number): number {
    const exact = this.indexByTimeMs.get(timeMs);
    if (exact !== undefined) return exact;
    if (this.timesMs.length === 0) return -1;

    let lo = 0;
    let hi = this.timesMs.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (this.timesMs[mid] < timeMs) lo = mid + 1;
      else hi = mid;
    }
    let nearest = lo;
    if (lo > 0 && Math.abs(this.timesMs[lo - 1] - timeMs) < Math.abs(this.timesMs[nearest] - timeMs)) {
      nearest = lo - 1;
    }
    return Math.abs(this.timesMs[nearest] - timeMs) <= DAY_MS ? nearest : -1;
  }
}

export function createRegimeDatafeed(store: RegimeStore): IBasicDataFeed {
  return {
    onReady(callback: OnReadyCallback): void {
      setTimeout(() => callback(CONFIGURATION), 0);
    },
    searchSymbols(
      _userInput: string,
      _exchange: string,
      _symbolType: string,
      onResult: SearchSymbolsCallback,
    ): void {
      onResult([]);
    },
    resolveSymbol(
      _symbolName: string,
      onResolve: ResolveCallback,
      _onError: ErrorCallback,
    ): void {
      const info: LibrarySymbolInfo = {
        name: store.data.symbol,
        full_name: store.data.symbol,
        ticker: store.data.symbol,
        description: store.data.symbol,
        type: 'stock',
        session: '0900-1500',
        exchange: 'Vietnam',
        listed_exchange: 'Vietnam',
        timezone: 'Asia/Ho_Chi_Minh',
        format: 'price',
        pricescale: 100,
        minmov: 1,
        has_intraday: false,
        visible_plots_set: 'ohlcv',
        supported_resolutions: SUPPORTED_RESOLUTIONS,
        volume_precision: 0,
        data_status: 'endofday',
      };
      setTimeout(() => onResolve(info), 0);
    },
    getBars(
      _symbolInfo: LibrarySymbolInfo,
      _resolution: ResolutionString,
      periodParams: PeriodParams,
      onResult: HistoryCallback,
      _onError: ErrorCallback,
    ): void {
      const fromMs = periodParams.from * 1000;
      const toMs = periodParams.to * 1000;
      let bars = store.bars.filter((bar) => bar.time >= fromMs && bar.time < toMs);
      if (periodParams.countBack && bars.length > periodParams.countBack) {
        bars = bars.slice(-periodParams.countBack);
      }
      onResult(bars, { noData: bars.length === 0 });
    },
    subscribeBars(
      _symbolInfo: LibrarySymbolInfo,
      _resolution: ResolutionString,
      _onTick: SubscribeBarsCallback,
      _listenerGuid: string,
      _onResetCacheNeededCallback: () => void,
    ): void {},
    unsubscribeBars(_listenerGuid: string): void {},
  };
}

type PlotDefinition = {
  id: string;
  title: string;
  color: string;
  get: (store: RegimeStore, index: number) => number | null | undefined;
  width?: number;
  histogram?: boolean;
};

type PaletteDefinition = {
  target: string;
  colors: string[];
  index: (store: RegimeStore, index: number) => number;
};

type StudyDefinition = {
  id: string;
  name: string;
  precision: number;
  priceStudy?: boolean;
  plots: PlotDefinition[];
  palettes?: PaletteDefinition[];
  bands?: { value: number; color: string }[];
};

function buildStudy(pine: PineJS, store: RegimeStore, definition: StudyDefinition): CustomIndicator {
  const plots: Record<string, unknown>[] = definition.plots.map(({ id }) => ({ id, type: 'line' }));
  const palettesMeta: Record<string, unknown> = {};
  const palettesDefaults: Record<string, unknown> = {};

  (definition.palettes ?? []).forEach((palette, paletteIndex) => {
    const paletteId = `palette${paletteIndex}`;
    plots.push({ id: `color${paletteIndex}`, type: 'colorer', target: palette.target, palette: paletteId });
    const target = definition.plots.find((plot) => plot.id === palette.target);
    palettesMeta[paletteId] = {
      colors: Object.fromEntries(palette.colors.map((_, index) => [index, { name: `Color ${index}` }])),
      valToIndex: Object.fromEntries(palette.colors.map((_, index) => [index, index])),
      addDefaultColor: false,
    };
    palettesDefaults[paletteId] = {
      colors: Object.fromEntries(palette.colors.map((color, index) => [index, {
        color,
        width: target?.width ?? 1,
        style: LINE_SOLID,
      }])),
    };
  });

  const bands = (definition.bands ?? []).map((_, index) => ({ id: `band${index}`, name: `Band ${index}` }));
  const metainfo = {
    _metainfoVersion: 53,
    id: `${definition.id}@aiop-regime-1`,
    name: definition.name,
    description: definition.name,
    shortDescription: definition.name,
    isCustomIndicator: true,
    is_price_study: definition.priceStudy ?? false,
    format: definition.priceStudy ? { type: 'inherit' } : { type: 'price', precision: definition.precision },
    plots,
    inputs: [],
    styles: Object.fromEntries(definition.plots.map((plot) => [plot.id, {
      title: plot.title,
      histogramBase: 0,
      isHidden: false,
    }])),
    bands: bands.length ? bands : undefined,
    palettes: Object.keys(palettesMeta).length ? palettesMeta : undefined,
    defaults: {
      styles: Object.fromEntries(definition.plots.map((plot) => [plot.id, {
        linestyle: LINE_SOLID,
        linewidth: plot.width ?? 1,
        plottype: plot.histogram ? PLOT_HISTOGRAM : PLOT_LINE,
        trackPrice: false,
        transparency: 0,
        visible: true,
        color: plot.color,
      }])),
      bands: (definition.bands ?? []).map((band) => ({
        color: band.color,
        linestyle: LINE_DASHED,
        linewidth: 1,
        value: band.value,
        visible: true,
      })),
      palettes: Object.keys(palettesDefaults).length ? palettesDefaults : undefined,
      precision: definition.precision,
      inputs: {},
    },
  };

  return {
    name: definition.name,
    metainfo,
    constructor: function (this: LibraryPineStudy<IPineStudyResult>) {
      this.main = function (context: IContext) {
        const index = store.indexAt(pine.Std.time(context));
        const values = definition.plots.map((plot) => index < 0 ? NaN : finite(plot.get(store, index)));
        const colors = (definition.palettes ?? []).map((palette) => index < 0 ? NaN : palette.index(store, index));
        return [...values, ...colors] as unknown as IPineStudyResult;
      };
    },
  } as unknown as CustomIndicator;
}

function yzPaletteIndex(value: number | null | undefined): number {
  if (value == null) return 0;
  if (value > 90) return 3;
  if (value > 75) return 2;
  if (value < 25) return 0;
  return 1;
}

function definitions(): StudyDefinition[] {
  const markovColors = [-2, -1, 0, 1, 2].map((code) => REGIME_COLORS[code]);
  const ticaLabels = ['Risk-On', 'Caution', 'Risk-Off', 'Crisis'];
  return [
    {
      id: 'regime-kama',
      name: REGIME_STUDIES.kama,
      precision: 2,
      priceStudy: true,
      plots: [{
        id: 'kama', title: 'KAMA', color: REGIME_FALLBACK, width: 2,
        get: (store, index) => store.data.markov_kama.kama[index],
      }],
      palettes: [{
        target: 'kama', colors: markovColors,
        index: (store, index) => Math.max(0, Math.min(4, store.data.markov_kama.regime_code[index] + 2)),
      }],
    },
    {
      id: 'markov-kama-regime',
      name: REGIME_STUDIES.markov,
      precision: 0,
      plots: [{
        id: 'regime', title: 'Regime', color: REGIME_FALLBACK, histogram: true,
        get: (store, index) => store.data.markov_kama.regime_code[index],
      }],
      palettes: [{
        target: 'regime', colors: markovColors,
        index: (store, index) => Math.max(0, Math.min(4, store.data.markov_kama.regime_code[index] + 2)),
      }],
      bands: [{ value: 0, color: primitives.neutral[500] }],
    },
    {
      id: 'regime-probability',
      name: REGIME_STUDIES.probabilities,
      precision: 2,
      plots: [
        { id: 'markov', title: 'Bull P (MK)', color: primitives.green[500], width: 2, get: (store, index) => store.data.markov_kama.high_var_prob[index] },
        { id: 'stress', title: 'Stress P (MS)', color: primitives.red[500], width: 2, get: (store, index) => store.data.ms_regime.regime_prob[index] },
      ],
      bands: [{ value: 0.5, color: primitives.neutral[500] }],
    },
    {
      id: 'yz-vol-percentile',
      name: REGIME_STUDIES.yzPercentile,
      precision: 0,
      plots: [{
        id: 'percentile', title: 'YZ Pct', color: primitives.green[500], width: 2,
        get: (store, index) => store.data.yz_percentile.pct_rank[index],
      }],
      palettes: [{
        target: 'percentile',
        colors: [primitives.blue[500], primitives.green[500], primitives.orange[500], primitives.red[500]],
        index: (store, index) => yzPaletteIndex(store.data.yz_percentile.pct_rank[index]),
      }],
      bands: [
        { value: 25, color: primitives.blue[500] },
        { value: 75, color: primitives.orange[500] },
        { value: 90, color: primitives.red[500] },
      ],
    },
    {
      id: 'tica-hmm-regime',
      name: REGIME_STUDIES.ticaHmm,
      precision: 0,
      plots: [{
        id: 'regime', title: 'TICA+HMM', color: TICA_LABEL_COLORS['Risk-On'], histogram: true,
        get: (store, index) => store.data.tica_hmm.regime_label[index] ? 1 : null,
      }],
      palettes: [{
        target: 'regime',
        colors: ticaLabels.map((label) => TICA_LABEL_COLORS[label]),
        index: (store, index) => Math.max(0, ticaLabels.indexOf(store.data.tica_hmm.regime_label[index])),
      }],
    },
  ];
}

export function regimeIndicatorsGetter(store: RegimeStore) {
  return (pine: PineJS): Promise<CustomIndicator[]> => Promise.resolve(
    definitions().map((definition) => buildStudy(pine, store, definition)),
  );
}
