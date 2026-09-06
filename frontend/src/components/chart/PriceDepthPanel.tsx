import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box, CircularProgress, Stack, Typography } from '@mui/material';

import { fetchPriceDepth, type PriceDepthLevel } from '../../lib/services/tradeFlow';

const BUY = 'var(--color-long)';
const SELL = 'var(--color-short)';
const TEXT = 'var(--color-text-primary)';
const MUTED = 'var(--color-text-tertiary)';

function formatPrice(value: number): string {
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function formatSize(value: number): string {
  const absolute = Math.abs(value);
  if (absolute >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
  if (absolute >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (absolute >= 1e3) return `${(value / 1e3).toFixed(0)}K`;
  return Math.round(value).toLocaleString('en-US');
}

function SideBar({
  size,
  maxSize,
  color,
  align,
}: {
  size: number;
  maxSize: number;
  color: string;
  align: 'left' | 'right';
}) {
  return (
    <Box
      sx={{
        height: 12,
        display: 'flex',
        justifyContent: align === 'right' ? 'flex-end' : 'flex-start',
        bgcolor: 'action.hover',
        borderRadius: 0.5,
        overflow: 'hidden',
      }}
    >
      <Box
        sx={{
          width: `${maxSize > 0 ? (size / maxSize) * 100 : 0}%`,
          bgcolor: color,
          opacity: 0.8,
        }}
      />
    </Box>
  );
}

export default function PriceDepthPanel({ symbol }: { symbol: string }) {
  const { data, error, isFetching } = useQuery({
    queryKey: ['priceDepth', symbol],
    queryFn: () => fetchPriceDepth(symbol),
    enabled: Boolean(symbol),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  const levels = useMemo<PriceDepthLevel[]>(
    () => (data?.levels ?? []).slice().sort((a, b) => b.price - a.price),
    [data],
  );
  const maxSize = levels.reduce(
    (max, level) => Math.max(max, level.buy_size, level.sell_size),
    0,
  );

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, width: '100%' }}>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ px: 0.75, pb: 0.5, flexShrink: 0 }}>
        <Typography sx={{ fontSize: 11.5, fontWeight: 700, letterSpacing: 0.3 }}>
          PRICE DEPTH
        </Typography>
        <Typography sx={{ fontSize: 11.5, color: 'primary.light', fontWeight: 700 }}>
          {symbol}
        </Typography>
        {isFetching && <CircularProgress size={10} />}
      </Stack>

      <Stack direction="row" justifyContent="space-between" sx={{ px: 0.75, pb: 0.75, flexShrink: 0 }}>
        <Typography sx={{ color: SELL, fontSize: 10, fontVariantNumeric: 'tabular-nums' }}>
          SELL {formatSize(data?.total_sell_size ?? 0)}
        </Typography>
        <Typography sx={{ color: BUY, fontSize: 10, fontVariantNumeric: 'tabular-nums' }}>
          BUY {formatSize(data?.total_buy_size ?? 0)}
        </Typography>
      </Stack>

      <Box sx={{ px: 0.75, pb: 0.5, flexShrink: 0 }}>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: '1fr 64px 1fr',
            gap: 0.75,
            color: MUTED,
            fontSize: 9,
            textTransform: 'uppercase',
            letterSpacing: 0.3,
          }}
        >
          <Typography sx={{ color: SELL, fontSize: 'inherit', textAlign: 'right' }}>Sell size</Typography>
          <Typography sx={{ color: MUTED, fontSize: 'inherit', textAlign: 'center' }}>Price</Typography>
          <Typography sx={{ color: BUY, fontSize: 'inherit' }}>Buy size</Typography>
        </Box>
      </Box>

      <Box sx={{ flex: 1, minHeight: 0, overflow: 'auto', px: 0.75 }}>
        {error && (
          <Typography sx={{ color: SELL, fontSize: 11 }}>
            {error instanceof Error ? error.message : 'Failed to load price depth.'}
          </Typography>
        )}
        {!error && !isFetching && levels.length === 0 && (
          <Typography sx={{ color: 'text.disabled', fontSize: 11 }}>
            {data?.note ?? `No executed depth for ${symbol}.`}
          </Typography>
        )}
        {levels.length > 0 && (
          <Stack spacing={0.35}>
            {levels.map((level) => (
              <Box
                key={level.price}
                sx={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 64px 1fr',
                  gap: 0.75,
                  alignItems: 'center',
                }}
              >
                <Stack direction="row" alignItems="center" justifyContent="flex-end" spacing={0.5}>
                  <Typography
                    sx={{ color: SELL, fontSize: 10, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}
                  >
                    {formatSize(level.sell_size)}
                  </Typography>
                  <Box sx={{ width: '100%', minWidth: 20 }}>
                    <SideBar size={level.sell_size} maxSize={maxSize} color={SELL} align="right" />
                  </Box>
                </Stack>
                <Typography
                  sx={{
                    color: TEXT,
                    fontSize: 10.5,
                    textAlign: 'center',
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {formatPrice(level.price)}
                </Typography>
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <Box sx={{ width: '100%', minWidth: 20 }}>
                    <SideBar size={level.buy_size} maxSize={maxSize} color={BUY} align="left" />
                  </Box>
                  <Typography
                    sx={{ color: BUY, fontSize: 10, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}
                  >
                    {formatSize(level.buy_size)}
                  </Typography>
                </Stack>
              </Box>
            ))}
          </Stack>
        )}
      </Box>

      <Typography sx={{ px: 0.75, pt: 0.75, color: MUTED, fontSize: 10, flexShrink: 0 }}>
        {data?.session_date ? `Latest session ${data.session_date}` : 'Latest trading session'}
        {' · executed aggressor size, not resting orders'}
      </Typography>
    </Box>
  );
}
