/**
 * TradingView Charting Library glue for the Future (VN30F1M intraday) page.
 *
 * Unlike the daily-equity chart (see `datafeed.ts` / `store.ts`), the future
 * endpoint (`/future/ohlc-5m/{symbol}`) returns the *whole* intraday series for
 * one timeframe in a single response, indicators (BSI, quantile bands, KAMA)
 * already computed and aligned to the bars. So this bridge is deliberately
 * simpler: one in-memory {@link FutureStore} holds the current response, the
 * datafeed answers `getBars` from it in one shot, and two bridged custom studies
 * (KAMA overlay + BSI oscillator pane) read their precomputed values by bar time.
 */
import type {
  Bar,
  DatafeedConfiguration,
  ErrorCallback,
  HistoryCallback,
  IBasicDataFeed,
  LibrarySymbolInfo,
  OnReadyCallback,
  PeriodParams,
  ResolutionString,
  ResolveCallback,
  SearchSymbolsCallback,
  SubscribeBarsCallback,
  CustomIndicator,
  IContext,
  PineJS,
} from './charting_library';
import type { FutureOhlcResponse } from '../services/future';
import { isVnMarketSession } from '../services/quote';
import { studyPalette } from './theme';

/** How often the datafeed polls the backend for a fresh series while live. */
const REALTIME_POLL_MS = 10_000;

/** App timeframe token → library resolution string. */
export const TF_TO_RESOLUTION: Record<string, ResolutionString> = {
  '5m': '5' as ResolutionString,
  '15m': '15' as ResolutionString,
  '30m': '30' as ResolutionString,
  '1h': '60' as ResolutionString,
};

const SUPPORTED_RESOLUTIONS = Object.values(TF_TO_RESOLUTION);

/** Vietnam is UTC+7 year-round (no DST). */
const VN_OFFSET_MS = 7 * 60 * 60 * 1000;

/**
 * Bar time (ms, UTC) for a raw backend timestamp.
 *
 * The future endpoint returns *naive* Vietnam wall-clock strings like
 * `"2024-01-15T09:15:00"` (no timezone suffix). `new Date(ts)` would parse those
 * in the browser's local timezone, giving the wrong instant off-VN — and since
 * the charting library maps each bar into the symbol's `Asia/Ho_Chi_Minh`
 * session (`0900-1500`), a mis-shifted intraday bar falls outside the session
 * and is silently dropped ("No data"). So parse the wall clock as VN explicitly:
 * the UTC instant is the wall clock minus 7h.
 */
export function barTimeMs(ts: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})/.exec(ts);
  if (!m) return new Date(ts).getTime();
  const [, Y, Mo, D, H, Mi, S] = m;
  return Date.UTC(+Y, +Mo - 1, +D, +H, +Mi, +S) - VN_OFFSET_MS;
}

/** Shared store: the datafeed writes it, the bridged studies read it. */
export class FutureStore {
  private _data: FutureOhlcResponse | null = null;
  private _timesMs: number[] = [];
  private _indexByTimeMs = new Map<number, number>();
  /** Page-supplied loader used by the real-time subscription to poll fresh data. */
  private _fetcher: (() => Promise<FutureOhlcResponse | null>) | null = null;
  private readonly _listeners = new Set<() => void>();

  set(data: FutureOhlcResponse | null): void {
    this._data = data;
    this._timesMs = [];
    this._indexByTimeMs = new Map();
    if (data) {
      data.timestamps.forEach((ts, i) => {
        const ms = barTimeMs(ts);
        this._timesMs.push(ms);
        this._indexByTimeMs.set(ms, i);
      });
    }
    this._listeners.forEach((fn) => fn());
  }

  get data(): FutureOhlcResponse | null {
    return this._data;
  }

  /** Newest loaded bar time (ms), or 0 when empty. */
  get lastBarTimeMs(): number {
    return this._timesMs.length ? this._timesMs[this._timesMs.length - 1] : 0;
  }

  /** The loader the real-time subscription polls (fetches with current params). */
  setFetcher(fn: (() => Promise<FutureOhlcResponse | null>) | null): void {
    this._fetcher = fn;
  }
  get fetcher(): (() => Promise<FutureOhlcResponse | null>) | null {
    return this._fetcher;
  }

  /** Fires whenever {@link set} runs (initial load or a real-time poll). */
  onUpdate(fn: () => void): () => void {
    this._listeners.add(fn);
    return () => this._listeners.delete(fn);
  }

  /** Array index for a bar time (ms), or -1. Exact match first, nearest bar
   *  within one bar-width as a fallback for any rounding the library applies. */
  indexAt(timeMs: number): number {
    const exact = this._indexByTimeMs.get(timeMs);
    if (exact !== undefined) return exact;
    const arr = this._timesMs;
    if (arr.length === 0) return -1;
    let lo = 0;
    let hi = arr.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid] < timeMs) lo = mid + 1;
      else hi = mid;
    }
    let best = lo;
    if (lo > 0 && Math.abs(arr[lo - 1] - timeMs) < Math.abs(arr[best] - timeMs)) best = lo - 1;
    // Tolerate up to ~1h (largest supported bar) of drift.
    return Math.abs(arr[best] - timeMs) <= 60 * 60 * 1000 ? best : -1;
  }
}

/** Singleton shared by the datafeed and the custom-indicators getter. */
export const futureStore = new FutureStore();

const CONFIGURATION: DatafeedConfiguration = {
  supported_resolutions: SUPPORTED_RESOLUTIONS,
  supports_marks: false,
  supports_timescale_marks: false,
  supports_time: true,
  exchanges: [],
  symbols_types: [{ name: 'Future', value: 'future' }],
};

/** Builds the library Bar[] from the loaded future response. */
function barsFor(data: FutureOhlcResponse): Bar[] {
  const { open, high, low, close } = data.ohlc;
  const bars: Bar[] = [];
  for (let i = 0; i < data.timestamps.length; i++) {
    const o = open[i];
    const c = close[i];
    if (typeof o !== 'number' || typeof c !== 'number') continue;
    bars.push({
      time: barTimeMs(data.timestamps[i]),
      open: o,
      high: high[i],
      low: low[i],
      close: c,
      volume: data.volume?.total?.[i],
    });
  }
  return bars;
}

export function createFutureDatafeed(store: FutureStore): IBasicDataFeed {
  /** Live subscriptions by listener GUID, so `unsubscribeBars` can stop them. */
  const subscriptions = new Map<string, () => void>();

  return {
    onReady(callback: OnReadyCallback): void {
      setTimeout(() => callback(CONFIGURATION), 0);
    },

    searchSymbols(
      userInput: string,
      _exchange: string,
      _symbolType: string,
      onResult: SearchSymbolsCallback,
    ): void {
      const q = userInput.trim().toUpperCase();
      if (!q) { onResult([]); return; }
      onResult([{ symbol: q, full_name: q, ticker: q, description: q, exchange: '', type: 'future' }]);
    },

    resolveSymbol(
      symbolName: string,
      onResolve: ResolveCallback,
      _onError: ErrorCallback,
    ): void {
      const symbol = symbolName.toUpperCase();
      const info: LibrarySymbolInfo = {
        name: symbol,
        full_name: symbol,
        ticker: symbol,
        description: symbol,
        type: 'future',
        session: '0900-1500',
        exchange: '',
        listed_exchange: '',
        timezone: 'Asia/Ho_Chi_Minh',
        format: 'price',
        pricescale: 100,
        minmov: 1,
        has_intraday: true,
        intraday_multipliers: ['5', '15', '30', '60'],
        visible_plots_set: 'ohlcv',
        supported_resolutions: SUPPORTED_RESOLUTIONS,
        volume_precision: 0,
        // Keep the last bar live via the polling `subscribeBars` below.
        data_status: 'streaming',
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
      // The backend already returned the full window for the selected timeframe;
      // hand it all over on the first request and report "no more" afterwards.
      const data = store.data;
      if (!periodParams.firstDataRequest || !data) {
        onResult([], { noData: true });
        return;
      }
      const bars = barsFor(data);
      onResult(bars, { noData: bars.length === 0 });
    },

    /**
     * Keeps the series live by polling the page-supplied loader and pushing any
     * new/updated bars through the tick callback — the library's own real-time
     * path, so no manual `resetData()` churn. The bridged studies recompute for
     * the ticked bars because {@link FutureStore.set} refreshes their source.
     */
    subscribeBars(
      _symbolInfo: LibrarySymbolInfo,
      _resolution: ResolutionString,
      onTick: SubscribeBarsCallback,
      listenerGuid: string,
      _onResetCacheNeededCallback: () => void,
    ): void {
      let stopped = false;
      let lastTime = store.lastBarTimeMs;

      const poll = async (): Promise<void> => {
        const fetcher = store.fetcher;
        if (!fetcher) return;
        try {
          const data = await fetcher();
          if (stopped || !data) return;
          store.set(data);
          // Emit ascending; re-emitting the newest bar just updates it in place.
          for (const bar of barsFor(data)) {
            if (bar.time >= lastTime) {
              onTick(bar);
              lastTime = bar.time;
            }
          }
        } catch {
          // Transient failure: the next poll retries.
        }
      };

      const timer = window.setInterval(() => {
        if (isVnMarketSession()) void poll();
      }, REALTIME_POLL_MS);

      subscriptions.set(listenerGuid, () => {
        stopped = true;
        window.clearInterval(timer);
      });
    },

    unsubscribeBars(listenerGuid: string): void {
      subscriptions.get(listenerGuid)?.();
      subscriptions.delete(listenerGuid);
    },
  };
}

// ── Bridged custom studies (KAMA overlay + BSI oscillator) ────────────────────

const LINE_SOLID = 0;
const LINE_DASHED = 2;
const PLOT_LINE = 0;

function num(v: number | null | undefined): number {
  return typeof v === 'number' && isFinite(v) ? v : NaN;
}

/** KAMA price-pane overlay reading the bridged `indicators.kama`. */
function buildKamaStudy(pine: PineJS, store: FutureStore): CustomIndicator {
  const metainfo: any = {
    _metainfoVersion: 53,
    id: 'future_kama@tv-bridged-1',
    name: 'KAMA',
    description: 'KAMA',
    shortDescription: 'KAMA',
    isCustomIndicator: true,
    is_price_study: true,
    format: { type: 'inherit' },
    plots: [{ id: 'kama', type: 'line' }],
    styles: { kama: { title: 'KAMA', histogramBase: 0, isHidden: false } },
    inputs: [],
    defaults: {
      styles: {
        kama: {
          linestyle: LINE_SOLID, linewidth: 2, plottype: PLOT_LINE,
          trackPrice: false, transparency: 0, visible: true, color: studyPalette.orange,
        },
      },
      precision: 2,
      inputs: {},
    },
  };

  return {
    name: 'KAMA',
    metainfo,
    constructor: function (this: any) {
      this.init = function (context: IContext) { this._context = context; };
      this.main = function (context: IContext) {
        const data = store.data;
        if (!data) return [NaN];
        const i = store.indexAt(pine.Std.time(context));
        if (i < 0) return [NaN];
        return [num(data.indicators.kama[i])];
      };
    },
  } as unknown as CustomIndicator;
}

/** BSI oscillator pane with the quantile band lines + a zero reference band. */
function buildBsiStudy(pine: PineJS, store: FutureStore): CustomIndicator {
  const metainfo: any = {
    _metainfoVersion: 53,
    id: 'future_bsi@tv-bridged-1',
    name: 'Hawkes BSI',
    description: 'Hawkes BSI',
    shortDescription: 'BSI',
    isCustomIndicator: true,
    is_price_study: false,
    format: { type: 'price', precision: 0 },
    plots: [
      { id: 'bsi', type: 'line' },
      { id: 'qhi', type: 'line' },
      { id: 'qlo', type: 'line' },
    ],
    bands: [{ id: 'zero', name: 'Zero' }],
    styles: {
      bsi: { title: 'BSI', histogramBase: 0, isHidden: false },
      qhi: { title: 'q_hi', histogramBase: 0, isHidden: false },
      qlo: { title: 'q_lo', histogramBase: 0, isHidden: false },
    },
    inputs: [],
    defaults: {
      styles: {
        bsi: { linestyle: LINE_SOLID, linewidth: 2, plottype: PLOT_LINE, trackPrice: false, transparency: 0, visible: true, color: studyPalette.blue },
        qhi: { linestyle: LINE_DASHED, linewidth: 2, plottype: PLOT_LINE, trackPrice: false, transparency: 0, visible: true, color: studyPalette.red },
        qlo: { linestyle: LINE_DASHED, linewidth: 2, plottype: PLOT_LINE, trackPrice: false, transparency: 0, visible: true, color: studyPalette.teal },
      },
      bands: [
        { color: studyPalette.zeroLine, linestyle: LINE_SOLID, linewidth: 1, value: 0, visible: true },
      ],
      precision: 0,
      inputs: {},
    },
  };

  return {
    name: 'Hawkes BSI',
    metainfo,
    constructor: function (this: any) {
      this.init = function (context: IContext) { this._context = context; };
      this.main = function (context: IContext) {
        const data = store.data;
        if (!data) return [NaN, NaN, NaN];
        const i = store.indexAt(pine.Std.time(context));
        if (i < 0) return [NaN, NaN, NaN];
        const { bsi, q_hi, q_lo } = data.indicators;
        return [num(bsi[i]), num(q_hi[i]), num(q_lo[i])];
      };
    },
  } as unknown as CustomIndicator;
}

/** Study names created via `activeChart().createStudy(name)`. */
export const FUTURE_KAMA_STUDY = 'KAMA';
export const FUTURE_BSI_STUDY = 'Hawkes BSI';

/** Widget `custom_indicators_getter` for the future chart. */
export function futureIndicatorsGetter(store: FutureStore) {
  return (pine: PineJS): Promise<CustomIndicator[]> =>
    Promise.resolve([buildKamaStudy(pine, store), buildBsiStudy(pine, store)]);
}
