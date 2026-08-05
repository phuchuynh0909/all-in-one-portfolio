/**
 * Real-time quote service.
 *
 * Backs the chart's real-time bar updates: the backend proxies DNSE's OpenAPI
 * (`GET /quote/{symbol}/latest`) because the request has to be HMAC-signed with
 * a secret that must not reach the browser. Prices come back on the same scale
 * as the historical bars (thousands of VND).
 */

/** Latest matched trade for one symbol. */
export interface LatestQuote {
  symbol: string;
  /** VN trading date the quote belongs to, `YYYY-MM-DD` (the bar it updates). */
  trading_date: string;
  /** Match time as reported by the exchange (VN local, no offset). */
  time: string;
  /** Last match price. */
  price: number;
  /** Session open / high / low for the board; null until reported. */
  open: number | null;
  high: number | null;
  low: number | null;
  /** Cumulative session volume (shares). */
  volume: number | null;
  board_id: string | null;
  market_id: string | null;
  /** Last close before `trading_date`, the reference the change is measured on. */
  prev_close: number | null;
  change: number | null;
  change_pct: number | null;
  /**
   * `live` = a matched trade from the provider; `eod` = the app's own last
   * end-of-day bar, used for symbols the provider does not trade (indices such
   * as VNINDEX) or that have not traded yet today.
   */
  source: 'live' | 'eod';
}

export interface QuoteBatch {
  quotes: LatestQuote[];
  /** Symbols with neither a live trade nor any history. */
  unavailable: string[];
}

export const fetchLatestQuote = async (symbol: string): Promise<LatestQuote> => {
  const response = await fetch(
    `${import.meta.env.VITE_API_BASE_URL}/quote/${symbol}/latest`,
  );

  if (!response.ok) {
    throw new Error(`Error ${response.status}: ${await response.text()}`);
  }

  return response.json();
};

/** Quotes for a list of symbols, in the order requested (watchlist rows). */
export const fetchQuotes = async (symbols: string[]): Promise<QuoteBatch> => {
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}/quote/batch`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ symbols }),
  });

  if (!response.ok) {
    throw new Error(`Error ${response.status}: ${await response.text()}`);
  }

  return response.json();
};

/** Vietnam is GMT+7 year-round (no DST). */
const VIETNAM_TZ_OFFSET_HOURS = 7;

/**
 * Whether the VN equity market is currently in a session (Mon–Fri, 09:00–15:00
 * Vietnam time, which spans the ATO/continuous/ATC windows).
 *
 * Only used to avoid pointless polling outside trading hours — holidays are not
 * modelled, so a closed-market poll is still possible (and harmless: the quote
 * simply doesn't change).
 */
export function isVnMarketSession(now: Date = new Date()): boolean {
  const vn = new Date(now.getTime() + VIETNAM_TZ_OFFSET_HOURS * 60 * 60 * 1000);
  const day = vn.getUTCDay();
  if (day === 0 || day === 6) return false;
  const minutes = vn.getUTCHours() * 60 + vn.getUTCMinutes();
  return minutes >= 9 * 60 && minutes <= 15 * 60;
}
