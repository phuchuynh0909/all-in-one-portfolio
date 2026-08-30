import { Box, Typography, type TypographyProps } from '@mui/material';
import ArrowDropUpIcon from '@mui/icons-material/ArrowDropUp';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import { APP_CURRENCY, APP_LOCALE, CURRENCY_DECIMALS } from '../../lib/format';

export interface NumericProps extends Omit<TypographyProps, 'variant' | 'children'> {
  value: number | null | undefined;
  /** Colour by sign: green positive, red negative, muted zero/null. */
  signed?: boolean;
  /** Prefix an arrow glyph. Implies `signed`. */
  arrow?: boolean;
  /** Always show a leading + on positives. */
  showSign?: boolean;
  format?: 'number' | 'percent' | 'currency' | 'compact';
  currency?: string;
  /** Defaults per format: 2 for number/percent, 0 for VND currency. */
  decimals?: number;
  /** Rendered when the value is null/undefined/NaN. */
  placeholder?: string;
}

function formatValue(
  value: number,
  format: NumericProps['format'],
  decimals: number | undefined,
  currency: string,
): string {
  switch (format) {
    case 'percent':
      return `${new Intl.NumberFormat(APP_LOCALE, {
        minimumFractionDigits: decimals ?? 2,
        maximumFractionDigits: decimals ?? 2,
      }).format(value)}%`;
    case 'currency':
      return new Intl.NumberFormat(APP_LOCALE, {
        style: 'currency',
        currency,
        minimumFractionDigits: decimals ?? CURRENCY_DECIMALS,
        maximumFractionDigits: decimals ?? CURRENCY_DECIMALS,
      }).format(value);
    case 'compact':
      return new Intl.NumberFormat(APP_LOCALE, {
        notation: 'compact',
        maximumFractionDigits: 1,
      }).format(value);
    default:
      return new Intl.NumberFormat(APP_LOCALE, {
        minimumFractionDigits: decimals ?? 2,
        maximumFractionDigits: decimals ?? 2,
      }).format(value);
  }
}

/**
 * Tabular-numeric value with optional P&L colouring. Use this anywhere a
 * number appears in a column so digits align and signs read consistently.
 */
export default function Numeric({
  value,
  signed = false,
  arrow = false,
  showSign = false,
  format = 'number',
  currency = APP_CURRENCY,
  decimals,
  placeholder = '—',
  sx,
  ...rest
}: NumericProps) {
  const isEmpty = value == null || Number.isNaN(value);
  const useSign = signed || arrow;

  const color = !useSign || isEmpty || value === 0
    ? undefined
    : value! > 0
      ? 'market.long'
      : 'market.short';

  if (isEmpty) {
    return (
      <Typography variant="mono" sx={{ color: 'text.tertiary', ...sx }} {...rest}>
        {placeholder}
      </Typography>
    );
  }

  const body = formatValue(value!, format, decimals, currency);
  const prefix = showSign && value! > 0 ? '+' : '';

  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', whiteSpace: 'nowrap' }}>
      {arrow && value !== 0 && (
        value! > 0
          ? <ArrowDropUpIcon sx={{ fontSize: 16, color, ml: -0.5 }} />
          : <ArrowDropDownIcon sx={{ fontSize: 16, color, ml: -0.5 }} />
      )}
      <Typography variant="mono" sx={{ color, ...sx }} {...rest}>
        {prefix}
        {body}
      </Typography>
    </Box>
  );
}
