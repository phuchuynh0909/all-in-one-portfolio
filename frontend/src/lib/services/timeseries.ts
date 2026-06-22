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
    matrix_series?: {
      hh: (number | null)[];
      ll: (number | null)[];
      support_line: (number | null)[];
      resistance_line: (number | null)[];
      up_line: (number | null)[];
      down_line: (number | null)[];
    };
    williams_vix_fix?: {
      wvf: (number | null)[];
      range_high: (number | null)[];
      filtered: boolean[];
      cond_fe: boolean[];
    };
    squeeze_ttm?: {
      histogram: (number | null)[];
      squeeze_on: boolean[];
    };
    smart_money_flow?: {
      last_signal: (number | null)[];
      switch_up: boolean[];
      switch_down: boolean[];
      upper: (number | null)[];
      lower: (number | null)[];
      b_close: (number | null)[];
      b_open: (number | null)[];
      mf_smooth: (number | null)[];
      strength: (number | null)[];
      bull_dot: boolean[];
      bear_dot: boolean[];
      strength_signed: (number | null)[];
    };
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

export const fetchTimeseries = async (symbol: string, params: TimeseriesRequest): Promise<TimeseriesResponse> => {
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

  return response.json();
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

// Market Breadth Types
export interface MarketBreadthResponse {
  timestamps: string[];
  ad_line: (number | null)[];
  mcclellan_oscillator: (number | null)[];
  mcclellan_summation: (number | null)[];
  advances: number[];
  declines: number[];
  unchanged: number[];
}

export interface MarketBreadthRequest {
  start_date?: string;
  end_date?: string;
}

export const fetchMarketBreadth = async (params: MarketBreadthRequest = {}): Promise<MarketBreadthResponse> => {
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}/timeseries/market/breadth`, {
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
