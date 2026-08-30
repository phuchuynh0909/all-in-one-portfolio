/**
 * Trade-flow anomaly panel — fills the width of the chart's right side panel.
 *
 * Shows flagged windows for the *active* symbol, newest first. The backend
 * endpoint scores one symbol per call, so this follows the chart's symbol
 * rather than scanning every watchlist row: a whole-watchlist sweep would be
 * ~200 requests per refresh.
 *
 * Each row is a window Isolation Forest scored as unusual — its whole feature
 * vector is odd for this symbol at this time of day. The verdict is
 * point-in-time: it does not distinguish a lone odd window from the start of a
 * sustained run.
 *
 * The side panel is drag-resizable, so columns are revealed progressively as
 * room appears (see COLUMNS). The panel measures itself rather than taking a
 * width prop, so it stays correct wherever it is mounted.
 *
 * A footprint is evidence of unusual *executed* flow. The tape carries no order
 * book, so it is not proof of an institution and cannot attribute absorption to
 * a side.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Box,
  Chip,
  CircularProgress,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';

import {
  fetchTradeFlowAnomalies,
  type TradeFlowWindow,
} from '../../lib/services/tradeFlow';

const BUY = 'var(--color-long)';
const SELL = 'var(--color-short)';
const NEUTRAL = 'var(--color-flat)';

// Default cell colour. Explicit white rather than `inherit`, because the app
// theme is `mode: 'light'` while this panel sits on a dark gradient Paper — an
// inherited `text.primary` resolves to near-black and disappears. Columns that
// carry direction (Vol, Imbal, the returns) override this with BUY/SELL.
const TEXT = 'var(--color-text-primary)';

/** How often to re-score while the panel is mounted. */
const POLL_MS = 30_000;
const MAX_ROWS = 200;

const sideColor = (side: number) => (side === 1 ? BUY : side === 2 ? SELL : NEUTRAL);

/** Compact share/lot counts: 1.2M / 340K / 900. */
function fmtQty(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return String(Math.round(v));
}

/** Window start on the tape's own clock. */
function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: 'Asia/Ho_Chi_Minh',
  });
}

function fmtDay(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    timeZone: 'Asia/Ho_Chi_Minh',
  });
}

function fmtPct(v: number | null, digits = 2): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`;
}

const fmtShare = (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(0)}%`);
const fmtNum = (v: number | null, d = 2) => (v == null ? '—' : v.toFixed(d));

/** ISO date N days back. */
function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

const cellSx = {
  py: 0.35,
  px: 0.75,
  borderColor: 'var(--color-border-subtle)',
  fontSize: 11,
  whiteSpace: 'nowrap',
} as const;

const headSx = {
  ...cellSx,
  py: 0.25,
  color: 'text.secondary',
  fontWeight: 700,
  fontSize: 9.5,
  letterSpacing: 0.2,
  textTransform: 'uppercase',
  background: 'transparent',
} as const;

interface Column {
  key: string;
  label: string;
  /** Panel width, in px, at which this column starts being worth the room. */
  min: number;
  numeric?: boolean;
  help?: string;
  render: (w: TradeFlowWindow) => React.ReactNode;
  /** Per-cell colour, where the value carries a direction. */
  color?: (w: TradeFlowWindow) => string | undefined;
}

/**
 * Ordered by usefulness per pixel — the first few answer "when, what, how big",
 * and the rest let a flagged row be audited rather than just trusted.
 */
const COLUMNS: Column[] = [
  {
    key: 'time',
    label: 'Time',
    min: 0,
    render: (w) => (
      <Stack direction="row" spacing={0.6} alignItems="baseline">
        <span style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtTime(w.window_start)}</span>
        <span style={{ fontSize: 9 }}>{fmtDay(w.window_start)}</span>
      </Stack>
    ),
  },
  {
    key: 'volume',
    label: 'Vol',
    min: 0,
    numeric: true,
    render: (w) => fmtQty(w.volume),
    color: (w) => sideColor(w.side),
  },
  { key: 'score', label: 'Score', min: 300, numeric: true, render: (w) => w.anomaly_score.toFixed(3) },
  {
    key: 'burst',
    label: 'Burst',
    min: 380,
    numeric: true,
    help: "Peak trades/sec vs this window's own mean — temporal clustering",
    render: (w) => fmtNum(w.burstiness, 1),
  },
  {
    key: 'imbalance',
    label: 'Imbal',
    min: 510,
    numeric: true,
    help: '(buy − sell) / total, from the real aggressor side',
    render: (w) => fmtNum(w.trade_imbalance),
    color: (w) => sideColor(w.side),
  },
  {
    key: 'fwd5m',
    label: '+5m',
    min: 450,
    numeric: true,
    help: 'VWAP return 5 minutes after this window',
    render: (w) => fmtPct(w.fwd_ret_5m),
    color: (w) => (w.fwd_ret_5m == null ? undefined : w.fwd_ret_5m >= 0 ? BUY : SELL),
  },
  { key: 'trades', label: 'Trades', min: 580, numeric: true, render: (w) => w.trade_count.toLocaleString() },
  {
    key: 'maxTrade',
    label: 'Max trade',
    min: 660,
    numeric: true,
    help: 'Largest single trade in the window',
    render: (w) => fmtQty(w.max_trade_size),
  },
  {
    key: 'topShare',
    label: 'Top %',
    min: 720,
    numeric: true,
    help: 'Largest trade as a share of window volume',
    render: (w) => fmtShare(w.top_trade_share),
  },
  {
    key: 'sameMs',
    label: 'Same ms',
    min: 780,
    numeric: true,
    help: 'Share of trades landing in the same millisecond as the previous one',
    render: (w) => fmtShare(w.same_ms_share),
  },
  {
    key: 'gapMs',
    label: 'Gap ms',
    min: 850,
    numeric: true,
    help: 'Median gap between consecutive trades',
    render: (w) =>
      w.median_interarrival_ms == null
        ? '—'
        : Math.round(w.median_interarrival_ms).toLocaleString(),
  },
  {
    key: 'ret',
    label: 'Ret',
    min: 920,
    numeric: true,
    help: 'Return across the window itself',
    render: (w) => fmtPct(w.ret),
    color: (w) => (w.ret == null ? undefined : w.ret >= 0 ? BUY : SELL),
  },
  {
    key: 'fwd1m',
    label: '+1m',
    min: 980,
    numeric: true,
    render: (w) => fmtPct(w.fwd_ret_1m),
    color: (w) => (w.fwd_ret_1m == null ? undefined : w.fwd_ret_1m >= 0 ? BUY : SELL),
  },
  {
    key: 'fwd15m',
    label: '+15m',
    min: 1040,
    numeric: true,
    render: (w) => fmtPct(w.fwd_ret_15m),
    color: (w) => (w.fwd_ret_15m == null ? undefined : w.fwd_ret_15m >= 0 ? BUY : SELL),
  },
];

export interface AnomalyPanelProps {
  /** Symbol to score — normally the chart's active symbol. */
  symbol: string;
  /** Lookback window in days. */
  days?: number;
  /** Clicking a row hands back the window start (unix seconds) to centre a chart. */
  onSelectWindow?: (epochSeconds: number, symbol: string) => void;
}

export default function AnomalyPanel({
  symbol,
  days = 5,
  onSelectWindow,
}: AnomalyPanelProps) {
  const fromDate = useMemo(() => isoDaysAgo(days), [days]);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);

  // Measure self rather than take a prop, so the panel is correct wherever it
  // is mounted and while the side panel is being dragged.
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) setWidth(entry.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const columns = useMemo(
    // Before the first measurement, show the always-on set rather than everything.
    () => COLUMNS.filter((c) => c.min <= (width || 1)),
    [width],
  );

  const { data, isFetching, isError, error } = useQuery({
    queryKey: ['tradeFlowAnomalies', symbol, fromDate],
    queryFn: () =>
      fetchTradeFlowAnomalies(symbol, { fromDate, limit: MAX_ROWS, onlyFlagged: true }),
    enabled: !!symbol,
    refetchInterval: POLL_MS,
    staleTime: POLL_MS,
  });

  // The API sorts by score; time order reads better beside a chart.
  const rows: TradeFlowWindow[] = useMemo(
    () => (data?.windows ?? []).slice().sort((a, b) => b.time - a.time),
    [data],
  );

  const wide = width >= 450;

  return (
    <Box
      ref={rootRef}
      sx={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, width: '100%' }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ px: 0.75, pb: 0.5, flexShrink: 0, minWidth: 0 }}
      >
        <Typography sx={{ fontSize: 11.5, fontWeight: 700, letterSpacing: 0.3 }}>
          ANOMALIES
        </Typography>
        <Typography sx={{ fontSize: 11.5, color: 'primary.light', fontWeight: 700 }}>
          {symbol}
        </Typography>
        {isFetching && <CircularProgress size={10} />}
        <Box sx={{ flex: 1 }} />
        {data && wide && (
          <Typography sx={{ fontSize: 10, color: 'text.disabled', whiteSpace: 'nowrap' }}>
            {data.windows_scanned.toLocaleString()} windows · {data.window_seconds}s · {days}d
          </Typography>
        )}
        {data && (
          <Tooltip
            title={
              `${data.anomalies_found} unusual of ` +
              `${data.windows_scanned.toLocaleString()} windows of ${data.window_seconds}s scanned`
            }
          >
            <Chip
              label={`${data.anomalies_found} unusual`}
              size="small"
              sx={{
                height: 15,
                fontSize: 9,
                '& .MuiChip-label': { px: 0.5 },
                bgcolor: 'action.selected',
                color: 'primary.main',
              }}
            />
          </Tooltip>
        )}
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, minWidth: 0, overflow: 'auto' }}>
        {isError && (
          <Typography sx={{ fontSize: 11, color: SELL, px: 0.75 }}>
            {String((error as Error)?.message ?? 'failed to load')}
          </Typography>
        )}

        {!isError && data?.note && rows.length === 0 && (
          <Typography sx={{ fontSize: 11, color: 'text.disabled', px: 0.75 }}>
            {data.note}
          </Typography>
        )}

        {!isError && !data?.note && rows.length === 0 && !isFetching && (
          <Typography sx={{ fontSize: 11, color: 'text.disabled', px: 0.75 }}>
            No flagged windows for {symbol} in the last {days} days.
          </Typography>
        )}

        {rows.length > 0 && (
          <Table size="small" stickyHeader sx={{ '& td, & th': { border: 0 } }}>
            <TableHead>
              <TableRow>
                {columns.map((c) => (
                  <TableCell
                    key={c.key}
                    sx={{ ...headSx, textAlign: c.numeric ? 'right' : 'left' }}
                  >
                    {c.help ? (
                      <Tooltip title={c.help}>
                        <span>{c.label}</span>
                      </Tooltip>
                    ) : (
                      c.label
                    )}
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((w) => (
                <TableRow
                  key={`${w.symbol}-${w.time}`}
                  hover
                  onClick={() => onSelectWindow?.(w.time, w.symbol)}
                  sx={{
                    cursor: onSelectWindow ? 'pointer' : 'default',
                    '& td:first-of-type': { borderLeft: `2px solid ${sideColor(w.side)}` },
                  }}
                >
                  {columns.map((c) => (
                    <TableCell
                      key={c.key}
                      sx={{
                        ...cellSx,
                        textAlign: c.numeric ? 'right' : 'left',
                        fontVariantNumeric: c.numeric ? 'tabular-nums' : undefined,
                        color: c.color?.(w) ?? TEXT,
                        fontWeight: c.key === 'volume' ? 600 : undefined,
                      }}
                    >
                      {c.render(w)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Box>
    </Box>
  );
}
