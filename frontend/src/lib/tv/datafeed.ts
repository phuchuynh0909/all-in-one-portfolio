/**
 * TradingView datafeed backed by the app's REST timeseries API.
 *
 * Daily VN-equity data only. History is paginated server-side: each `getBars`
 * call forwards the library's `to` / `countBack` window to
 * `POST /timeseries/{symbol}/bars` and answers with exactly that page. Pages
 * accumulate in {@link TvDataStore} for the bridged custom studies to read.
 * Research reports are surfaced as chart marks via `getMarks`.
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
import { formatReportDateForChart } from '../services/timeseries';
import { fetchReports, type Report } from '../services/report';
import type { BarsPage, LoadedSeries, TvDataStore } from './store';

const SUPPORTED_RESOLUTIONS = ['1D', '1W', '1M'] as ResolutionString[];

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
        data_status: 'endofday',
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

    subscribeBars(
      _symbolInfo: LibrarySymbolInfo,
      _resolution: ResolutionString,
      _onTick: SubscribeBarsCallback,
      _listenerGuid: string,
      _onResetCacheNeededCallback: () => void,
    ): void {
      // End-of-day data: no real-time updates.
    },

    unsubscribeBars(_listenerGuid: string): void {
      // No-op — nothing subscribed.
    },
  };
}
