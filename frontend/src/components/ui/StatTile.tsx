import type { ReactNode } from 'react';
import { Box, Paper, Skeleton, Tooltip, Typography } from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import Numeric, { type NumericProps } from './Numeric';

export interface StatTileProps {
  label: string;
  /** Pass `value` for a formatted numeric, or `children` for anything else. */
  value?: number | null;
  children?: ReactNode;
  /** Secondary line — a delta, a benchmark, a date range. */
  hint?: ReactNode;
  /** Explains the metric on hover. */
  help?: string;
  loading?: boolean;
  /** Formatting passed through to <Numeric>. */
  format?: NumericProps['format'];
  decimals?: number;
  currency?: string;
  signed?: boolean;
  showSign?: boolean;
  /** Left edge accent — use to flag a headline or a breached threshold. */
  accent?: 'none' | 'primary' | 'long' | 'short' | 'warning';
}

const accentColor: Record<NonNullable<StatTileProps['accent']>, string | undefined> = {
  none: undefined,
  primary: 'primary.main',
  long: 'market.long',
  short: 'market.short',
  warning: 'warning.main',
};

/** Single KPI in a dashboard row. Keeps label/value/hint rhythm identical everywhere. */
export default function StatTile({
  label,
  value,
  children,
  hint,
  help,
  loading = false,
  format = 'number',
  decimals = 2,
  currency,
  signed = false,
  showSign = false,
  accent = 'none',
}: StatTileProps) {
  const bar = accentColor[accent];

  return (
    <Paper
      sx={{
        position: 'relative',
        px: 2,
        py: 1.5,
        display: 'flex',
        flexDirection: 'column',
        gap: 0.5,
        minWidth: 0,
        overflow: 'hidden',
        ...(bar && {
          '&::before': {
            content: '""',
            position: 'absolute',
            insetBlock: 0,
            left: 0,
            width: 2,
            bgcolor: bar,
          },
        }),
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0 }}>
        <Typography variant="overline2" noWrap>
          {label}
        </Typography>
        {help && (
          <Tooltip title={help}>
            <InfoOutlinedIcon sx={{ fontSize: 12, color: 'text.tertiary', cursor: 'help' }} />
          </Tooltip>
        )}
      </Box>

      {loading ? (
        <Skeleton width="60%" height={28} />
      ) : children ? (
        <Box sx={{ minWidth: 0 }}>{children}</Box>
      ) : (
        <Numeric
          value={value}
          format={format}
          decimals={decimals}
          currency={currency}
          signed={signed}
          showSign={showSign}
          sx={{ fontSize: '1.375rem', fontWeight: 600, lineHeight: 1.2 }}
        />
      )}

      {hint && !loading && (
        <Typography variant="caption" sx={{ color: 'text.tertiary' }} noWrap component="div">
          {hint}
        </Typography>
      )}
    </Paper>
  );
}
