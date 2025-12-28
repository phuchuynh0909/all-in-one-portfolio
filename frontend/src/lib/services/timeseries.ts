import type { UTCTimestamp } from 'lightweight-charts';
import { subDays, format } from 'date-fns';

export interface TimeseriesResponse {
  symbol: string;
  interval: string;
  timestamps: string[];
  timeseries: {
    open: number[];
    high: number[];
    low: number[];
    close: number[];
    volume: number[];
  };
  indicators?: {
    rsi?: (number | null)[];
    rsi_5?: (number | null)[];
    atr?: (number | null)[];
    atr_trailing?: (number | null)[];
    vwap_highest?: (number | null)[];
    vwap_lowest?: (number | null)[];
    bvc?: (number | null)[];
    kalman_zscore?: (number | null)[];
    yz_volatility?: (number | null)[];
    rs_rating_20?: (number | null)[];
    rs_rating_50?: (number | null)[];
    rs_rating_252?: (number | null)[];
    rs_rating_20_ema?: (number | null)[];
    rs_rating_50_ema?: (number | null)[];
    rs_rating_252_ema?: (number | null)[];
  };
}

export interface IndicatorParams {
  name: string;
  params?: Record<string, number | string>;
}

export interface TimeseriesRequest {
  interval?: string;
  start_date?: string;
  end_date?: string;
  indicators?: IndicatorParams[];
}

// ============================================================================
// Cache configuration and utilities
// ============================================================================
const CACHE_PREFIX = 'timeseries_cache_';
const CACHE_DURATION_MS = 60 * 60 * 1000; // 1 hour in milliseconds

interface CachedData<T> {
  data: T;
  timestamp: number;
}

/**
 * Generate a cache key from symbol and request params
 */
const getCacheKey = (symbol: string, params: TimeseriesRequest): string => {
  const paramsStr = JSON.stringify(params);
  return `${CACHE_PREFIX}${symbol}_${btoa(paramsStr).slice(0, 32)}`;
};

/**
 * Get cached data if valid (not expired)
 */
const getFromCache = <T>(key: string): T | null => {
  try {
    const cached = localStorage.getItem(key);
    if (!cached) return null;
    
    const { data, timestamp }: CachedData<T> = JSON.parse(cached);
    const age = Date.now() - timestamp;
    
    if (age > CACHE_DURATION_MS) {
      // Cache expired, remove it
      localStorage.removeItem(key);
      return null;
    }
    
    return data;
  } catch {
    return null;
  }
};

/**
 * Save data to cache with timestamp
 */
const saveToCache = <T>(key: string, data: T): void => {
  try {
    const cached: CachedData<T> = {
      data,
      timestamp: Date.now(),
    };
    localStorage.setItem(key, JSON.stringify(cached));
  } catch (e) {
    // Handle localStorage quota exceeded
    console.warn('Failed to cache timeseries data:', e);
    clearOldCache();
  }
};

/**
 * Clear old cache entries when storage is full
 */
const clearOldCache = (): void => {
  const keys = Object.keys(localStorage).filter(k => k.startsWith(CACHE_PREFIX));
  const now = Date.now();
  
  for (const key of keys) {
    try {
      const cached = localStorage.getItem(key);
      if (cached) {
        const { timestamp } = JSON.parse(cached);
        if (now - timestamp > CACHE_DURATION_MS) {
          localStorage.removeItem(key);
        }
      }
    } catch {
      localStorage.removeItem(key);
    }
  }
};

// ============================================================================
// API Functions with caching
// ============================================================================

export const fetchTimeseries = async (symbol: string, params: TimeseriesRequest): Promise<TimeseriesResponse> => {
  const cacheKey = getCacheKey(symbol, params);
  
  // Check cache first
  const cached = getFromCache<TimeseriesResponse>(cacheKey);
  if (cached) {
    console.debug(`[Cache HIT] Timeseries for ${symbol}`);
    return cached;
  }
  
  console.debug(`[Cache MISS] Fetching timeseries for ${symbol}`);
  
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}/timeseries/${symbol}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(params),
  });

  if (!response.ok) {
    throw new Error(`Error ${response.status}: ${await response.text()}`);
  }

  const data = await response.json();
  
  // Save to cache
  saveToCache(cacheKey, data);
  
  return data;
};

/**
 * Clear all timeseries cache (useful for manual refresh)
 */
export const clearTimeseriesCache = (): void => {
  const keys = Object.keys(localStorage).filter(k => k.startsWith(CACHE_PREFIX));
  keys.forEach(key => localStorage.removeItem(key));
  console.debug(`[Cache] Cleared ${keys.length} cached entries`);
};

// Helper function to format indicator data
export const formatIndicatorData = (timestamps: string[], values: (number | null | undefined)[]): { time: UTCTimestamp; value: number }[] => {
  return timestamps
    .map((timestamp: string, i: number) => {
      const value = values[i];
      if (typeof value !== 'number') return null;
      
      return {
        time: (new Date(timestamp).getTime() / 1000) as UTCTimestamp,
        value: value,
      };
    })
    .filter((item: unknown): item is { time: UTCTimestamp; value: number } => item !== null)
    .filter((item: { time: UTCTimestamp; value: number }) => !isNaN(item.value));
};

// Create constant value lines for indicators
export const createConstantLine = (data: { time: UTCTimestamp; value: number }[], constantValue: number) => 
  data.map(item => ({
    time: item.time,
    value: constantValue,
  }));

// Helper functions for date handling
export const getDateRange = (days: number = 365) => ({
  start_date: format(subDays(new Date(), days), 'yyyy-MM-dd'),
  end_date: format(new Date(), 'yyyy-MM-dd'),
});

export const timestampToDate = (timestamp: string): UTCTimestamp => 
  (new Date(timestamp).getTime() / 1000) as UTCTimestamp;

// Vietnam timezone offset in hours (GMT+7)
const VIETNAM_TZ_OFFSET_HOURS = 7;

/**
 * Format timestamp for daily chart alignment.
 * Normalizes to UTC midnight to ensure markers align with daily OHLC bars.
 * 
 * Uses fixed Vietnam timezone (GMT+7) for consistent date interpretation
 * regardless of the user's browser timezone.
 */
export const formatChartTime = (timestamp: string): UTCTimestamp => {
  // Parse the timestamp
  const date = new Date(timestamp);
  
  // Get UTC time and add Vietnam offset to get Vietnam local time
  const utcTime = date.getTime();
  const vietnamTime = utcTime + (VIETNAM_TZ_OFFSET_HOURS * 60 * 60 * 1000);
  const vietnamDate = new Date(vietnamTime);
  
  // Extract date components in Vietnam timezone
  const year = vietnamDate.getUTCFullYear();
  const month = vietnamDate.getUTCMonth();
  const day = vietnamDate.getUTCDate();
  
  // Create UTC midnight timestamp for this Vietnam date
  const utcMidnight = Date.UTC(year, month, day, 0, 0, 0, 0);
  
  return (utcMidnight / 1000) as UTCTimestamp;
};

/**
 * Format a date string for chart markers (reports, events, etc.)
 * Handles various date formats and interprets them as Vietnam local dates.
 * 
 * This function extracts just the date portion without applying timezone offset,
 * since report dates are typically already in Vietnam local date format.
 */
export const formatReportDateForChart = (dateStr: string): UTCTimestamp => {
  const date = new Date(dateStr);
  
  // If the date string is date-only (like "2024-01-15"), new Date() interprets it as UTC
  // If it has time/timezone, we need to convert to Vietnam time first
  const hasTime = dateStr.includes('T') || dateStr.includes(' ');
  
  let year: number, month: number, day: number;
  
  if (hasTime) {
    // Has time component - convert to Vietnam timezone
    const utcTime = date.getTime();
    const vietnamTime = utcTime + (VIETNAM_TZ_OFFSET_HOURS * 60 * 60 * 1000);
    const vietnamDate = new Date(vietnamTime);
    year = vietnamDate.getUTCFullYear();
    month = vietnamDate.getUTCMonth();
    day = vietnamDate.getUTCDate();
  } else {
    // Date-only string - use components directly (already interpreted as UTC midnight)
    year = date.getUTCFullYear();
    month = date.getUTCMonth();
    day = date.getUTCDate();
  }
  
  // Create UTC midnight timestamp
  const utcMidnight = Date.UTC(year, month, day, 0, 0, 0, 0);
  
  return (utcMidnight / 1000) as UTCTimestamp;
};

export interface SectorData {
  id: number;
  name: string;
  data: number[];
}

export interface SectorTimeseries {
  timestamps: string[];
  sector_data: SectorData[];
}

export const fetchSectorTimeseries = async (level: number, params: TimeseriesRequest): Promise<SectorTimeseries> => {
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}/timeseries/sector/${level}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(params),
  });

  if (!response.ok) {
    throw new Error(`Error ${response.status}: ${await response.text()}`);
  }

  return response.json();
};
