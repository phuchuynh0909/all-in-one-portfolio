import type { UTCTimestamp } from 'lightweight-charts';

/**
 * Timestamp columns come back from DuckDB/Arrow as epoch milliseconds
 * (numbers), not ISO strings. These helpers accept either form so the
 * components never format a raw epoch number into the UI.
 */
export function toDate(value: unknown): Date | null {
  if (value == null || value === '') return null;
  const d = typeof value === 'number' ? new Date(value) : new Date(String(value));
  return Number.isNaN(d.getTime()) ? null : d;
}

/** 'YYYY-MM-DD', or an em dash when the value is missing. */
export function toIsoDate(value: unknown): string {
  const d = toDate(value);
  return d ? d.toISOString().slice(0, 10) : '—';
}

/** lightweight-charts wants seconds since epoch, not milliseconds. */
export function toUtcTimestamp(value: unknown): UTCTimestamp {
  const d = toDate(value);
  return Math.floor((d ? d.getTime() : 0) / 1000) as UTCTimestamp;
}
