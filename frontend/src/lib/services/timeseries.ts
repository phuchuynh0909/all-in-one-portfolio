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

export const formatChartTime = (timestamp: string): UTCTimestamp => 
  (new Date(timestamp).getTime() / 1000) as UTCTimestamp;

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
