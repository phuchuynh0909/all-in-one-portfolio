/**
 * Centralised number/date formatting.
 *
 * The book is denominated in VND, so currency formatting must not fall back to
 * a dollar sign anywhere. Everything that renders a number goes through here
 * (directly, or via the <Numeric> component) so the locale is defined once.
 */

export const APP_LOCALE = 'vi-VN';
export const APP_CURRENCY = 'VND';

/** VND has no minor unit — never show decimals on a currency amount. */
export const CURRENCY_DECIMALS = 0;

export function formatCurrency(
  value: number,
  { compact = false, decimals = CURRENCY_DECIMALS }: { compact?: boolean; decimals?: number } = {},
): string {
  return new Intl.NumberFormat(APP_LOCALE, {
    style: 'currency',
    currency: APP_CURRENCY,
    notation: compact ? 'compact' : 'standard',
    minimumFractionDigits: compact ? 0 : decimals,
    maximumFractionDigits: compact ? 1 : decimals,
  }).format(value);
}

export function formatNumber(value: number, decimals = 2): string {
  return new Intl.NumberFormat(APP_LOCALE, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

/** Share counts are whole numbers in practice. */
export function formatQuantity(value: number): string {
  return new Intl.NumberFormat(APP_LOCALE, { maximumFractionDigits: 0 }).format(value);
}

export function formatPercent(value: number, decimals = 2): string {
  return `${formatNumber(value, decimals)}%`;
}

export function formatCompact(value: number): string {
  return new Intl.NumberFormat(APP_LOCALE, { notation: 'compact', maximumFractionDigits: 1 }).format(
    value,
  );
}

export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return '—';
  const d = typeof value === 'string' ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(APP_LOCALE, { year: 'numeric', month: '2-digit', day: '2-digit' });
}

export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return '—';
  const d = typeof value === 'string' ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString(APP_LOCALE, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}
