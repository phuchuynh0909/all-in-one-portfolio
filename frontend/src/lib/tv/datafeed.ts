/**
 * TradingView datafeed backed by the app's REST timeseries API.
 *
 * Daily VN-equity data only. History is paginated server-side: each `getBars`
 * call forwards the library's `to` / `countBack` window to
 * `POST /timeseries/{symbol}/bars` and answers with exactly that page. Pages
 * accumulate in {@link TvDataStore} for the bridged custom studies to read.
 * Research reports are surfaced as chart marks via `getMarks`.
 *
 * Real time comes from polling `GET /quote/{symbol}/latest` (the backend's
 * signed DNSE proxy) and pushing the current day's bar through the
 * `subscribeBars` tick callback — see {@link subscribeBars} below.
 */
import type {
  Bar,
  DatafeedConfiguration,
  ErrorCallback,
  GetMarksCallback,
  HistoryCallback,
  IBasicDataFeed,
  LibrarySymbolInfo,
  Mark,
  OnReadyCallback,
  PeriodParams,
  ResolutionString,
  ResolveCallback,
  SearchSymbolsCallback,
  SubscribeBarsCallback,
} from './charting_library';
import { formatChartTime, formatReportDateForChart } from '../services/timeseries';
import { fetchReports, type Report } from '../services/report';
import { fetchLatestQuote, isVnMarketSession, type LatestQuote } from '../services/quote';
import type { BarsPage, LoadedSeries, TvDataStore } from './store';

const SUPPORTED_RESOLUTIONS = ['1D', '1W', '1M'] as ResolutionString[];

/** The only resolution real-time ticks are emitted for (see `subscribeBars`). */
const DAILY = '1D' as ResolutionString;

/** How often the latest quote is polled while the market is open. */
const REALTIME_POLL_MS = 5_000;

const CONFIGURATION: DatafeedConfiguration = {
  supported_resolutions: SUPPORTED_RESOLUTIONS,
  supports_marks: true,
  supports_timescale_marks: false,
  supports_time: false,
  exchanges: [],
  symbols_types: [{ name: 'Stock', value: 'stock' }],
};

/** Builds the library's Bar[] from one backend page. */
function barsFor(page: BarsPage): Bar[] {
  const { open, high, low, close, volume } = page.timeseries;
  const bars: Bar[] = [];
  for (let i = 0; i < page.timesMs.length; i++) {
    const o = open[i];
    const c = close[i];
    if (typeof o !== 'number' || typeof c !== 'number') continue;
    bars.push({
      time: page.timesMs[i],
      open: o,
      high: high[i],
      low: low[i],
      close: c,
      volume: volume?.[i],
    });
  }
  return bars;
}

/** The bar the store already holds for `timeMs`, if any. */
function loadedBarAt(store: TvDataStore, symbol: string, timeMs: number): Bar | null {
  const series = store.loaded;
  if (!series || series.symbol !== symbol) return null;
  const i = series.indexByTimeMs.get(timeMs);
  if (i === undefined) return null;
  const { open, high, low, close, volume } = series.response.timeseries;
  if (typeof open[i] !== 'number' || typeof close[i] !== 'number') return null;
  return {
    time: timeMs,
    open: open[i],
    high: high[i],
    low: low[i],
    close: close[i],
    volume: volume?.[i],
  };
}

/** Newest bar time (ms) loaded for `symbol`, or null when nothing is loaded. */
function newestLoadedTime(store: TvDataStore, symbol: string): number | null {
  const series = store.loaded;
  if (!series || series.symbol !== symbol || series.timesMs.length === 0) return null;
  return series.timesMs[series.timesMs.length - 1];
}

function definedNumbers(...values: (number | null | undefined)[]): number[] {
  return values.filter((v): v is number => typeof v === 'number');
}

/**
 * Builds the current daily bar from a quote, merged with whatever the backend
 * already knows about that day.
 *
 * The quote carries the board's session open/high/low plus cumulative volume, so
 * a full bar can be reconstructed from a single response. When the backend has
 * already published a bar for the same day, its open wins (it is the settled
 * value) and the extremes are widened rather than replaced, so a late-session
 * poll can never shrink the bar's range.
 */
function barFromQuote(quote: LatestQuote, existing: Bar | null): Bar {
  const time = formatChartTime(quote.trading_date) * 1000;
  const close = quote.price;
  return {
    time,
    open: existing?.open ?? quote.open ?? close,
    high: Math.max(...definedNumbers(close, quote.high, existing?.high)),
    low: Math.min(...definedNumbers(close, quote.low, existing?.low)),
    close,
    volume: Math.max(...definedNumbers(quote.volume, existing?.volume, 0)),
  };
}

function sameBar(a: Bar, b: Bar): boolean {
  return a.time === b.time
    && a.open === b.open
    && a.high === b.high
    && a.low === b.low
    && a.close === b.close
    && a.volume === b.volume;
}

/** Snap a report to the nearest existing bar time (marks must sit on a bar). */
function snapToBar(series: LoadedSeries, targetMs: number): number | null {
  if (series.indexByTimeMs.has(targetMs)) return targetMs;
  let best: number | null = null;
  let bestDiff = Infinity;
  for (const ms of series.timesMs) {
    const diff = Math.abs(ms - targetMs);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = ms;
    }
  }
  // Only snap within ~4 days, otherwise the report predates/exceeds the data.
  return bestDiff <= 4 * 24 * 60 * 60 * 1000 ? best : null;
}

export function createDatafeed(store: TvDataStore): IBasicDataFeed {
  const reportsBySymbol = new Map<string, Report[]>();
  /** Live subscriptions by listener GUID, so `unsubscribeBars` can stop them. */
  const subscriptions = new Map<string, () => void>();

  async function ensureReports(symbol: string): Promise<Report[]> {
    const cached = reportsBySymbol.get(symbol);
    if (cached) return cached;
    try {
      const reports = await fetchReports(symbol);
      reportsBySymbol.set(symbol, reports);
      return reports;
    } catch {
      reportsBySymbol.set(symbol, []);
      return [];
    }
  }

  return {
    onReady(callback: OnReadyCallback): void {
      // Must be async per the datafeed contract.
      setTimeout(() => callback(CONFIGURATION), 0);
    },

    searchSymbols(
      userInput: string,
      _exchange: string,
      _symbolType: string,
      onResult: SearchSymbolsCallback,
    ): void {
      // No symbol master list; echo the typed query so any VN ticker resolves.
      const q = userInput.trim().toUpperCase();
      if (!q) { onResult([]); return; }
      onResult([{
        symbol: q,
        full_name: q,
        ticker: q,
        description: q,
        exchange: '',
        type: 'stock',
      }]);
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
        type: 'stock',
        session: '0900-1500',
        exchange: '',
        listed_exchange: '',
        timezone: 'Asia/Ho_Chi_Minh',
        format: 'price',
        pricescale: 100,
        minmov: 1,
        has_intraday: false,
        visible_plots_set: 'ohlcv',
        supported_resolutions: SUPPORTED_RESOLUTIONS,
        volume_precision: 0,
        // The daily bar is kept live from the quote feed (see `subscribeBars`),
        // so the library should show the streaming badge rather than "EOD".
        data_status: 'streaming',
      };
      setTimeout(() => onResolve(info), 0);
    },

    async getBars(
      symbolInfo: LibrarySymbolInfo,
      _resolution: ResolutionString,
      periodParams: PeriodParams,
      onResult: HistoryCallback,
      onError: ErrorCallback,
    ): Promise<void> {
      const symbol = (symbolInfo.ticker ?? symbolInfo.name).toUpperCase();
      try {
        // The backend paginates: it returns `countBack` bars ending just before
        // `to`, so each request is answered with one round-trip and no
        // client-side history bookkeeping.
        const page = await store.loadPage(symbol, {
          toSec: periodParams.to,
          fromSec: periodParams.from,
          countBack: periodParams.countBack,
        });

        const bars = page.noData ? [] : barsFor(page);
        if (bars.length === 0) {
          onResult([], {
            noData: true,
            ...(page.nextTime != null ? { nextTime: page.nextTime } : {}),
          });
          return;
        }
        onResult(bars, { noData: false });
      } catch (err) {
        onError(err instanceof Error ? err.message : String(err));
      }
    },

    async getMarks(
      symbolInfo: LibrarySymbolInfo,
      from: number,
      to: number,
      onDataCallback: GetMarksCallback<Mark>,
      _resolution: ResolutionString,
    ): Promise<void> {
      const symbol = (symbolInfo.ticker ?? symbolInfo.name).toUpperCase();
      const series = store.loaded;
      if (!series || series.symbol !== symbol) {
        onDataCallback([]);
        return;
      }
      const reports = await ensureReports(symbol);
      const fromMs = from * 1000;
      const toMs = to * 1000;

      const marks: Mark[] = [];
      reports.forEach((report, i) => {
        if (!report.ngaykn) return;
        const snapped = snapToBar(series, formatReportDateForChart(report.ngaykn) * 1000);
        if (snapped == null || snapped < fromMs || snapped > toMs) return;
        marks.push({
          id: report.id ?? `report-${i}`,
          time: snapped, // milliseconds (Mark.time convention)
          color: { border: '#a855f7', background: '#a855f7' },
          text: `${report.tenbaocao}\n\n${report.nguon}`,
          label: 'R',
          labelFontColor: '#ffffff',
          minSize: 18,
        });
      });
      onDataCallback(marks);
    },

    /**
     * Keeps the current daily bar live by polling the latest matched trade.
     *
     * Only `1D` streams: the weekly/monthly resolutions would need the tick time
     * bucketed to the start of the week/month the library is drawing, and a
     * mis-aligned time silently appends a spurious bar.
     */
    subscribeBars(
      symbolInfo: LibrarySymbolInfo,
      resolution: ResolutionString,
      onTick: SubscribeBarsCallback,
      listenerGuid: string,
      _onResetCacheNeededCallback: () => void,
    ): void {
      if (resolution !== DAILY) return;
      const symbol = (symbolInfo.ticker ?? symbolInfo.name).toUpperCase();

      let stopped = false;
      let lastTick: Bar | null = null;

      const poll = async (): Promise<void> => {
        try {
          const quote = await fetchLatestQuote(symbol);
          if (stopped) return;

          const bar = barFromQuote(quote, loadedBarAt(store, symbol, formatChartTime(quote.trading_date) * 1000));

          // A quote older than the newest bar we hold means the market is shut
          // and the provider is echoing a previous session — the library rejects
          // out-of-order times anyway, so drop it.
          const newest = newestLoadedTime(store, symbol);
          if (newest != null && bar.time < newest) return;
          if (lastTick && (bar.time < lastTick.time || sameBar(bar, lastTick))) return;

          lastTick = bar;
          onTick(bar);
        } catch {
          // Transient upstream/network failure: the next poll retries.
        }
      };

      // Prime once regardless of session state so the chart shows the last
      // traded price immediately, then keep polling only while the market runs.
      void poll();
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
