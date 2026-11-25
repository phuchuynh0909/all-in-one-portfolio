import { formatChartTime, type MarketBreadthResponse, type TimeseriesResponse } from '../services/timeseries';
import { studyPalette } from './theme';
import { buildBreadthToolkit } from './breadthToolkit';
import type {
  Bar,
  CustomIndicator,
  DatafeedConfiguration,
  ErrorCallback,
  HistoryCallback,
  IPineStudyResult,
  IBasicDataFeed,
  LibraryPineStudy,
  IContext,
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
const PLOT_LINE = 0;
const PLOT_HISTOGRAM = 1;
const LINE_SOLID = 0;
const LINE_DASHED = 2;

export type BreadthView = 'ad_line' | 'mcclellan' | 'breadth';

export const BREADTH_STUDIES = {
  oscillator: 'McClellan Oscillator',
  adLine: 'Advance/Decline Line',
  summation: 'McClellan Summation Index',
  advancesDeclines: 'Advances / Declines',
  aboveMovingAverages: '% Stocks Above 20/50/200-day MA',
  netNewHighs: 'Net New Highs (252 sessions)',
} as const;

function calcSma(values: (number | null)[], period: number): (number | null)[] {
  const result = new Array<number | null>(values.length).fill(null);
  let sum = 0;
  let valid = 0;

  for (let i = 0; i < values.length; i++) {
    const entered = values[i];
    if (typeof entered === 'number') {
      sum += entered;
      valid++;
    }
    if (i >= period) {
      const exited = values[i - period];
      if (typeof exited === 'number') {
        sum -= exited;
        valid--;
      }
    }
    if (i >= period - 1 && valid === period) result[i] = sum / period;
  }
  return result;
}

export class MarketBreadthStore {
  readonly breadth: MarketBreadthResponse;
  readonly vnindex: TimeseriesResponse;
  readonly bars: Bar[];
  readonly adSma20: (number | null)[];
  readonly summationSma20: (number | null)[];
  private readonly timesMs: number[];
  private readonly indexByTimeMs: Map<number, number>;

  constructor(breadth: MarketBreadthResponse, vnindex: TimeseriesResponse) {
    this.breadth = breadth;
    this.vnindex = vnindex;
    this.timesMs = breadth.timestamps.map((timestamp) => formatChartTime(timestamp) * 1000);
    this.indexByTimeMs = new Map(this.timesMs.map((time, index) => [time, index]));
    this.adSma20 = calcSma(breadth.ad_line, 20);
    this.summationSma20 = calcSma(breadth.mcclellan_summation, 20);

    const { open, high, low, close, volume } = vnindex.timeseries;
    this.bars = vnindex.timestamps.flatMap((timestamp, index) => {
      if (typeof open[index] !== 'number' || typeof close[index] !== 'number') return [];
      return [{
        time: formatChartTime(timestamp) * 1000,
        open: open[index],
        high: high[index],
        low: low[index],
        close: close[index],
        volume: volume[index],
      } satisfies Bar];
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

const CONFIGURATION: DatafeedConfiguration = {
  supported_resolutions: SUPPORTED_RESOLUTIONS,
  supports_marks: false,
  supports_timescale_marks: false,
  supports_time: false,
  exchanges: [],
  symbols_types: [{ name: 'Index', value: 'index' }],
};

export function createMarketBreadthDatafeed(store: MarketBreadthStore): IBasicDataFeed {
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
        name: 'VNINDEX',
        full_name: 'VNINDEX',
        ticker: 'VNINDEX',
        description: 'VN-Index',
        type: 'index',
        session: '0900-1500',
        exchange: 'HOSE',
        listed_exchange: 'HOSE',
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

type ValueGetter = (store: MarketBreadthStore, index: number) => number | null | undefined;

type PlotDefinition = {
  id: string;
  title: string;
  color: string;
  get: ValueGetter;
  width?: number;
  histogram?: boolean;
  transparency?: number;
  hidden?: boolean;
};

type PaletteDefinition = {
  target: string;
  colors: string[];
  index: ValueGetter;
};

type StudyDefinition = {
  id: string;
  name: string;
  precision: number;
  plots: PlotDefinition[];
  palettes?: PaletteDefinition[];
  bands?: { value: number; color: string }[];
};

function finite(value: number | null | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : NaN;
}

function buildStudy(
  pine: PineJS,
  store: MarketBreadthStore,
  definition: StudyDefinition,
): CustomIndicator {
  const palettesMeta: Record<string, unknown> = {};
  const palettesDefaults: Record<string, unknown> = {};
  const plots: Record<string, unknown>[] = definition.plots.map(({ id }) => ({ id, type: 'line' }));

  (definition.palettes ?? []).forEach((palette, paletteIndex) => {
    const paletteId = `palette${paletteIndex}`;
    plots.push({ id: `color${paletteIndex}`, type: 'colorer', target: palette.target, palette: paletteId });
    const colorsMeta: Record<number, { name: string }> = {};
    const colors: Record<number, { color: string; width: number; style: number }> = {};
    const valToIndex: Record<number, number> = {};
    const target = definition.plots.find((plot) => plot.id === palette.target);
    palette.colors.forEach((color, colorIndex) => {
      colorsMeta[colorIndex] = { name: `Color ${colorIndex}` };
      colors[colorIndex] = { color, width: target?.width ?? 1, style: LINE_SOLID };
      valToIndex[colorIndex] = colorIndex;
    });
    palettesMeta[paletteId] = { colors: colorsMeta, valToIndex, addDefaultColor: false };
    palettesDefaults[paletteId] = { colors };
  });

  const styles = Object.fromEntries(definition.plots.map((plot) => [
    plot.id,
    { title: plot.title, histogramBase: 0, isHidden: plot.hidden ?? false },
  ]));
  const styleDefaults = Object.fromEntries(definition.plots.map((plot) => [
    plot.id,
    {
      linestyle: LINE_SOLID,
      linewidth: plot.width ?? 1,
      plottype: plot.histogram ? PLOT_HISTOGRAM : PLOT_LINE,
      trackPrice: false,
      transparency: plot.transparency ?? 0,
      visible: true,
      color: plot.color,
    },
  ]));
  const bands = (definition.bands ?? []).map((_, index) => ({ id: `band${index}`, name: `Band ${index}` }));
  const bandDefaults = (definition.bands ?? []).map((band) => ({
    color: band.color,
    linestyle: LINE_DASHED,
    linewidth: 1,
    value: band.value,
    visible: true,
  }));
  const metainfo = {
    _metainfoVersion: 53,
    id: `${definition.id}@aiop-breadth-1`,
    name: definition.name,
    description: definition.name,
    shortDescription: definition.name,
    isCustomIndicator: true,
    is_price_study: false,
    format: { type: 'price', precision: definition.precision },
    plots,
    inputs: [],
    styles,
    bands: bands.length ? bands : undefined,
    palettes: Object.keys(palettesMeta).length ? palettesMeta : undefined,
    defaults: {
      styles: styleDefaults,
      bands: bandDefaults.length ? bandDefaults : undefined,
      palettes: Object.keys(palettesDefaults).length ? palettesDefaults : undefined,
      precision: definition.precision,
      inputs: {},
    },
  };

  return {
    name: definition.name,
    metainfo,
    constructor: function (this: LibraryPineStudy<IPineStudyResult>) {
      this.main = function (
        context: IContext,
      ) {
        const index = store.indexAt(pine.Std.time(context));
        const values = definition.plots.map((plot) => index < 0 ? NaN : finite(plot.get(store, index)));
        const colors = (definition.palettes ?? []).map((palette) => index < 0 ? NaN : finite(palette.index(store, index)));
        // The generated numeric tuple follows the positional metainfo plots.
        return [...values, ...colors] as unknown as IPineStudyResult;
      };
    },
  } as unknown as CustomIndicator;
}

function definitions(): StudyDefinition[] {
  return [
    {
      id: 'market-breadth-net-new-highs',
      name: BREADTH_STUDIES.netNewHighs,
      precision: 0,
      plots: [{
        id: 'net', title: 'New highs − new lows', color: studyPalette.greenBar,
        histogram: true, get: (store, index) => store.breadth.net_new_highs[index],
      }],
      palettes: [{
        target: 'net', colors: [studyPalette.greenBar, studyPalette.redBar],
        index: (store, index) => (store.breadth.net_new_highs[index] ?? 0) >= 0 ? 0 : 1,
      }],
      bands: [{ value: 0, color: studyPalette.zeroLine }],
    },
    {
      id: 'market-breadth-oscillator',
      name: BREADTH_STUDIES.oscillator,
      precision: 1,
      plots: [{
        id: 'oscillator',
        title: 'McClellan Osc',
        color: studyPalette.greenBar,
        histogram: true,
        get: (store, index) => store.breadth.mcclellan_oscillator[index],
      }],
      palettes: [{
        target: 'oscillator',
        colors: [studyPalette.greenBar, studyPalette.redBar],
        index: (store, index) => (store.breadth.mcclellan_oscillator[index] ?? 0) >= 0 ? 0 : 1,
      }],
      bands: [{ value: 0, color: studyPalette.zeroLine }],
    },
    {
      id: 'market-breadth-ad-line',
      name: BREADTH_STUDIES.adLine,
      precision: 0,
      plots: [
        { id: 'ad', title: 'A/D Line', color: studyPalette.blue, width: 2, get: (store, index) => store.breadth.ad_line[index] },
        { id: 'sma', title: 'SMA 20', color: studyPalette.orange, get: (store, index) => store.adSma20[index] },
      ],
    },
    {
      id: 'market-breadth-summation',
      name: BREADTH_STUDIES.summation,
      precision: 0,
      plots: [
        { id: 'summation', title: 'Summation Index', color: studyPalette.blue, width: 2, get: (store, index) => store.breadth.mcclellan_summation[index] },
        { id: 'sma', title: 'SMA 20', color: studyPalette.orange, get: (store, index) => store.summationSma20[index] },
      ],
      bands: [{ value: 0, color: studyPalette.zeroLine }],
    },
    {
      id: 'market-breadth-advances-declines',
      name: BREADTH_STUDIES.advancesDeclines,
      precision: 0,
      plots: [
        { id: 'advances', title: 'Advances', color: studyPalette.greenBar, histogram: true, get: (store, index) => store.breadth.advances[index] },
        { id: 'declines', title: 'Declines', color: studyPalette.redBar, histogram: true, get: (store, index) => -store.breadth.declines[index] },
      ],
      bands: [{ value: 0, color: studyPalette.zeroLine }],
    },
    {
      id: 'market-breadth-above-moving-averages',
      name: BREADTH_STUDIES.aboveMovingAverages,
      precision: 1,
      plots: [
        { id: 'ma20', title: '% Above SMA 20', color: studyPalette.green, width: 2, get: (store, index) => store.breadth.pct_above_ma_20[index] },
        { id: 'ma50', title: '% Above SMA 50', color: studyPalette.blue, width: 2, get: (store, index) => store.breadth.pct_above_ma_50[index] },
        { id: 'ma200', title: '% Above SMA 200', color: studyPalette.orange, width: 2, get: (store, index) => store.breadth.pct_above_ma_200[index] },
      ],
      bands: [0, 50, 100].map((value) => ({ value, color: studyPalette.zeroLine })),
    },
  ];
}

export function marketBreadthIndicatorsGetter(store: MarketBreadthStore) {
  return (pine: PineJS): Promise<CustomIndicator[]> => Promise.resolve(
    [...definitions().map((definition) => buildStudy(pine, store, definition)), buildBreadthToolkit(pine, store)],
  );
}

export function studyForView(view: BreadthView): string {
  if (view === 'ad_line') return BREADTH_STUDIES.adLine;
  if (view === 'breadth') return BREADTH_STUDIES.advancesDeclines;
  return BREADTH_STUDIES.summation;
}
