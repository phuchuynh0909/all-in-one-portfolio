/**
 * Shared in-memory store bridging the app's REST timeseries API to the
 * TradingView charting library.
 *
 * The backend computes every indicator server-side and returns the values as
 * arrays aligned to the OHLCV bars. The charting library, by contrast, expects
 * indicators to be computed in-browser by custom studies. This store is the
 * bridge: `getBars` asks it for one page of bars, and each bridged custom study
 * looks up its precomputed value for the current bar by time.
 *
 * Pagination lives on the backend (`POST /timeseries/{symbol}/bars`): every
 * `getBars` call fetches exactly the page the library asked for (`count_back`
 * bars ending at `to`). Pages are merged into one growing dataset here, because
 * the studies need a single time-indexed series across everything loaded so far.
 *
 * One chart shows one symbol at a time, so the store keeps the most recently
 * loaded symbol's dataset. Changing indicator parameters resets it.
 */
import {
  fetchBars,
  formatChartTime,
  type BarsResponse,
  type TimeseriesResponse,
  type IndicatorParams,
} from '../services/timeseries';

/** Default visible history on chart load (calendar years). */
export const HISTORY_YEARS = 5;

export interface LoadedSeries {
  symbol: string;
  response: TimeseriesResponse;
  /** Bar time in **milliseconds** UTC (charting-library convention) per index. */
  timesMs: number[];
  /** Bar time (ms) → array index, for indicator value lookup by time. */
  indexByTimeMs: Map<number, number>;
  /** False once the backend reports no bars older than the earliest one loaded. */
  hasMoreHistory: boolean;
}

/** What the library asks for on a single `getBars` call. */
export interface PageRequest {
  /** Exclusive upper bound, unix seconds. */
  toSec: number;
  /** Inclusive lower bound, unix seconds (soft — `countBack` wins). */
  fromSec?: number;
  /** Number of bars the library needs, ending at `toSec`. */
  countBack?: number;
}

/** One page of bars as returned to `getBars` (this page only, not the merge). */
export interface BarsPage {
  symbol: string;
  /** Bar times (ms) for this page, ascending. */
  timesMs: number[];
  timeseries: TimeseriesResponse['timeseries'];
  noData: boolean;
  /** Unix seconds of the closest available bar when `noData` (skips gaps). */
  nextTime: number | null;
  hasMoreHistory: boolean;
}

/**
 * Slices every per-bar array in a (possibly nested) payload — the response shape
 * is a mix of flat arrays and nested indicator objects, all bottoming out in
 * arrays aligned to `timestamps`.
 */
function sliceAligned(node: unknown, start: number, end: number): unknown {
  if (Array.isArray(node)) return node.slice(start, end);
  if (node && typeof node === 'object') {
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      out[key] = sliceAligned(value, start, end);
    }
    return out;
  }
  return node;
}

/**
 * Concatenates two aligned payloads (`a` covering `aLen` bars, `b` covering
 * `bLen`). A key present on only one side is padded with nulls on the other so
 * the arrays stay aligned to the merged timestamps.
 */
function concatAligned(a: unknown, aLen: number, b: unknown, bLen: number): unknown {
  if (Array.isArray(a) || Array.isArray(b)) {
    const av = Array.isArray(a) ? a : new Array(aLen).fill(null);
    const bv = Array.isArray(b) ? b : new Array(bLen).fill(null);
    return [...av, ...bv];
  }
  if ((a && typeof a === 'object') || (b && typeof b === 'object')) {
    const ao = (a ?? {}) as Record<string, unknown>;
    const bo = (b ?? {}) as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of new Set([...Object.keys(ao), ...Object.keys(bo)])) {
      out[key] = concatAligned(ao[key], aLen, bo[key], bLen);
    }
    return out;
  }
  return b ?? a ?? null;
}

/** Where a page sits relative to the loaded series: what's new on each side. */
function pageOverlap(loaded: string[], page: string[]): { pre: number; sufStart: number } {
  const first = loaded[0];
  const last = loaded[loaded.length - 1];
  let pre = 0;
  while (pre < page.length && page[pre] < first) pre++;
  let sufStart = page.length;
  while (sufStart > pre && page[sufStart - 1] > last) sufStart--;
  return { pre, sufStart };
}

/** Merges a page into the loaded response, keeping bars ascending and unique. */
function mergePage(base: TimeseriesResponse, page: BarsResponse): TimeseriesResponse {
  if (page.timestamps.length === 0) return base;

  const { pre, sufStart } = pageOverlap(base.timestamps, page.timestamps);
  const sufLen = page.timestamps.length - sufStart;
  if (pre === 0 && sufLen === 0) return base; // page already covered

  const baseLen = base.timestamps.length;
  const join = (pageNode: unknown, baseNode: unknown): unknown => {
    const head = concatAligned(sliceAligned(pageNode, 0, pre), pre, baseNode, baseLen);
    return concatAligned(head, pre + baseLen, sliceAligned(pageNode, sufStart, page.timestamps.length), sufLen);
  };

  return {
    ...base,
    timestamps: [
      ...page.timestamps.slice(0, pre),
      ...base.timestamps,
      ...page.timestamps.slice(sufStart),
    ],
    timeseries: join(page.timeseries, base.timeseries) as TimeseriesResponse['timeseries'],
    indicators: (base.indicators || page.indicators)
      ? (join(page.indicators, base.indicators) as TimeseriesResponse['indicators'])
      : undefined,
  };
}

export class TvDataStore {
  private indicators: IndicatorParams[] = [];
  private current: LoadedSeries | null = null;
  /** Key (symbol + indicator params) the merged dataset belongs to. */
  private currentKey = '';
  /** Serializes page fetches so concurrent `getBars` calls can't race the merge. */
  private queue: Promise<unknown> = Promise.resolve();
  private readonly listeners = new Set<(s: LoadedSeries) => void>();

  /** Sets the indicator request payload used by subsequent page fetches. */
  setIndicators(indicators: IndicatorParams[]): void {
    this.indicators = indicators;
  }

  /** The dataset loaded so far (all pages merged), if any. */
  get loaded(): LoadedSeries | null {
    return this.current;
  }

  /** Subscribe to merges (fires whenever a page lands). */
  onLoaded(listener: (s: LoadedSeries) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /**
   * Drops everything loaded. Call before asking the chart to re-request data
   * (symbol change, indicator param change) so pages aren't merged across
   * different indicator settings.
   */
  reset(): void {
    this.current = null;
    this.currentKey = '';
  }

  /** Key that changes when previously fetched pages are no longer compatible. */
  private keyFor(symbol: string): string {
    return `${symbol}::${JSON.stringify(this.indicators)}`;
  }

  /**
   * Fetches one page of bars from the backend and merges it into the loaded
   * dataset. Returns the page itself — `getBars` must answer with only the bars
   * it asked for, not the whole merge.
   */
  loadPage(symbol: string, params: PageRequest): Promise<BarsPage> {
    const run = this.queue.then(
      () => this.fetchPage(symbol, params),
      () => this.fetchPage(symbol, params),
    );
    // Keep the chain alive even if this page fails.
    this.queue = run.then(
      () => undefined,
      () => undefined,
    );
    return run;
  }

  private async fetchPage(symbol: string, params: PageRequest): Promise<BarsPage> {
    const key = this.keyFor(symbol);
    const response = await fetchBars(symbol, {
      interval: '1d',
      to: params.toSec,
      from: params.fromSec,
      count_back: params.countBack,
      indicators: this.indicators,
    });

    const timesMs = response.timestamps.map((ts) => formatChartTime(ts) * 1000);
    const page: BarsPage = {
      symbol,
      timesMs,
      timeseries: response.timeseries,
      noData: response.no_data || response.timestamps.length === 0,
      nextTime: response.next_time ?? null,
      hasMoreHistory: response.has_more_history,
    };

    // Indicator params changed while this page was in flight: the chart will
    // re-request everything, so don't pollute the (already reset) dataset.
    if (key === this.keyFor(symbol) && !page.noData) {
      this.mergeIntoLoaded(symbol, key, response);
    }
    return page;
  }

  private mergeIntoLoaded(symbol: string, key: string, response: BarsResponse): void {
    const base = this.currentKey === key ? this.current : null;
    const merged = base ? mergePage(base.response, response) : this.toSeriesResponse(response);
    const extendsFront = !base || response.timestamps[0] < base.response.timestamps[0];

    this.currentKey = key;
    this.current = this.buildSeries(symbol, merged, {
      hasMoreHistory: extendsFront ? response.has_more_history : (base?.hasMoreHistory ?? true),
    });
    this.listeners.forEach((fn) => fn(this.current!));
  }

  private toSeriesResponse(response: BarsResponse): TimeseriesResponse {
    return {
      symbol: response.symbol,
      interval: response.interval,
      timestamps: response.timestamps,
      timeseries: response.timeseries,
      indicators: response.indicators,
    };
  }

  private buildSeries(
    symbol: string,
    response: TimeseriesResponse,
    { hasMoreHistory }: { hasMoreHistory: boolean },
  ): LoadedSeries {
    const timesMs: number[] = [];
    const indexByTimeMs = new Map<number, number>();
    response.timestamps.forEach((ts, i) => {
      const ms = formatChartTime(ts) * 1000;
      timesMs.push(ms);
      indexByTimeMs.set(ms, i);
    });

    return { symbol, response, timesMs, indexByTimeMs, hasMoreHistory };
  }
}

/** Singleton store shared by the datafeed and the custom studies getter. */
export const tvStore = new TvDataStore();

/** One day in milliseconds. */
const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Resolves a bar time (ms) to its array index in the loaded dataset.
 * Tries an exact match first, then the nearest bar within one day (tolerates
 * any session/timezone shift the library applies to daily bar times).
 * Returns -1 when no bar is close enough.
 */
export function indexAtTimeMs(series: LoadedSeries, timeMs: number): number {
  const exact = series.indexByTimeMs.get(timeMs);
  if (exact !== undefined) return exact;

  // Binary search over ascending timesMs for the closest value.
  const arr = series.timesMs;
  let lo = 0;
  let hi = arr.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < timeMs) lo = mid + 1;
    else hi = mid;
  }
  let best = lo;
  if (lo > 0 && Math.abs(arr[lo - 1] - timeMs) < Math.abs(arr[best] - timeMs)) {
    best = lo - 1;
  }
  return Math.abs(arr[best] - timeMs) <= DAY_MS ? best : -1;
}
