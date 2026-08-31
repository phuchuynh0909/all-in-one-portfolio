import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { alpha } from '@mui/material/styles';

import {
  fetchSectorRelativeStrength,
  type SectorRelativeStrength,
  type SectorRelativeStrengthRow,
  type SectorRsMetric,
  type SectorRsTimeframe,
} from '../../lib/services/timeseries';
import { fontFamily, useChartTheme } from '../../theme';
import { QueryState } from '../ui';

/**
 * The two relative-strength measures the backend can return, both centred on
 * zero so they share one colour ramp. Mansfield asks whether a sector is above
 * its own recent strength; outperformance asks by how much it beat the index
 * over the window.
 */
const METRICS: { value: SectorRsMetric; label: string }[] = [
  { value: 'mansfield', label: 'Mansfield' },
  { value: 'outperformance', label: 'Outperformance' },
];

/**
 * Daily bars, or one bar per calendar week. The window is not sent — the server
 * picks the default for the timeframe (50 sessions / 10 weeks, the same reach)
 * and reports it back, so the two never drift.
 */
const TIMEFRAMES: { value: SectorRsTimeframe; label: string; tick: string; unit: string }[] = [
  { value: 'daily', label: 'Daily', tick: 'T', unit: 'd' },
  { value: 'weekly', label: 'Weekly', tick: 'W', unit: 'w' },
];

const describeMetric = (metric: SectorRsMetric, window?: number, unit?: string): string => {
  const span = window ? `${window}${unit ?? ''}` : 'the window';
  return metric === 'mansfield'
    ? `vs its own ${span} mean strength`
    : `vs the index over ${span}`;
};

/** How far back the columns run: T-0 .. T-`range`. */
const RANGE_OPTIONS = [20, 40, 60, 120];
const DEFAULT_RANGE = 40;
/** Fetched once at the widest range, then sliced client-side. */
const MAX_RANGE = RANGE_OPTIONS[RANGE_OPTIONS.length - 1];

/** 0 = every sector. */
const TOP_N_OPTIONS = [5, 10, 15, 20, 25, 0];
const DEFAULT_TOP_N = 15;

const ROW_HEIGHT = 20;
const LABEL_WIDTH = 210;
const MIN_CELL_WIDTH = 9;
/** Every Nth column gets a tick label — 121 of them will not fit. */
const TICK_EVERY = 5;

/**
 * Widths at which an in-cell number still fits at CELL_FONT_SIZE. Below the
 * narrower one the cell carries colour only — a clipped half-digit is worse
 * than none, and the hover readout still gives the exact value.
 */
const CELL_FONT_SIZE = 9;
const CELL_WIDTH_FOR_DECIMAL = 30;
const CELL_WIDTH_FOR_INTEGER = 19;

/**
 * Colour ceiling for the ramp: the 95th percentile of |RS| rather than the max,
 * so a single runaway sector cannot flatten every other cell to grey.
 */
const colourBound = (values: number[]): number => {
  if (values.length === 0) return 1;
  const sorted = values.map(Math.abs).sort((a, b) => a - b);
  const p95 = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))];
  return Math.max(p95, 0.5);
};

const formatRs = (value: number): string => `${value > 0 ? '+' : ''}${value.toFixed(2)}`;

/** Sort key that keeps sectors with no reading at T-0 at the bottom. */
const sortKey = (value: number | null | undefined): number =>
  value == null || !Number.isFinite(value) ? Number.NEGATIVE_INFINITY : value;

/**
 * Sector relative strength as a heatmap: one row per sector, one column per
 * session, T-0 on the left running back to T-`range` on the right.
 *
 * Cells are Mansfield RS against VNINDEX — positive means the sector is above
 * its own recent average strength versus the index, so a row reads as a
 * rotation track rather than a return. Green/red is the market-direction
 * palette, which is what this is; the ramp is built from `useChartTheme`
 * values because inline styles cannot resolve CSS variables.
 *
 * The grid is plain DOM with inline styles on purpose: at 120 columns times 60
 * sectors this mounts over 7,000 cells, and one emotion class per cell (each
 * with its own background) locks the page up for seconds.
 */
export default function SectorRelativeStrengthHeatmap({ level }: { level: number }) {
  const ct = useChartTheme();
  const [data, setData] = useState<SectorRelativeStrength | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [metric, setMetric] = useState<SectorRsMetric>('mansfield');
  const [timeframe, setTimeframe] = useState<SectorRsTimeframe>('daily');
  const [range, setRange] = useState<number>(DEFAULT_RANGE);
  const [topN, setTopN] = useState<number>(DEFAULT_TOP_N);
  const [hovered, setHovered] = useState<{ row: number; col: number } | null>(null);

  // Each (level, metric) is one fetch, kept so flipping the switch back is
  // instant — level 3 reads a Delta table and takes seconds.
  const cache = useRef(new Map<string, SectorRelativeStrength>());

  useEffect(() => {
    let cancelled = false;
    const key = `${level}:${metric}:${timeframe}`;

    const cached = cache.current.get(key);
    if (cached) {
      setData(cached);
      setError(null);
      setLoading(false);
      return;
    }

    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const result = await fetchSectorRelativeStrength(level, {
          lookback: MAX_RANGE + 1,
          metric,
          timeframe,
        });
        if (cancelled) return;
        cache.current.set(key, result);
        setData(result);
      } catch (e) {
        if (!cancelled) setError(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [level, metric, timeframe, reloadKey]);

  /** The visible slice: newest column first, strongest row first, top N kept. */
  const view = useMemo(() => {
    if (!data || data.dates.length === 0) return null;

    const take = Math.min(range + 1, data.dates.length);
    const dates = data.dates.slice(-take).reverse();
    const ranked: SectorRelativeStrengthRow[] = data.rows
      .map((row) => ({ ...row, values: row.values.slice(-take).reverse() }))
      .sort((a, b) => sortKey(b.values[0]) - sortKey(a.values[0]));

    return { dates, rows: topN > 0 ? ranked.slice(0, topN) : ranked };
  }, [data, range, topN]);

  /** Bound follows the visible slice, so the ramp re-scales with the range. */
  const bound = useMemo(() => {
    if (!view) return 1;
    return colourBound(
      view.rows.flatMap((row) => row.values.filter((v): v is number => v != null && Number.isFinite(v))),
    );
  }, [view]);

  const cellColour = (value: number | null): string => {
    if (value == null || !Number.isFinite(value)) return alpha(ct.textMuted, 0.07);
    const magnitude = Math.min(Math.abs(value) / bound, 1);
    if (magnitude < 0.02) return alpha(ct.flat, 0.14);
    return alpha(value > 0 ? ct.up : ct.down, 0.12 + magnitude * 0.78);
  };

  const dates = view?.dates ?? [];
  const rows = view?.rows ?? [];
  const bar = TIMEFRAMES.find((t) => t.value === timeframe) ?? TIMEFRAMES[0];
  const gridTemplateColumns = `${LABEL_WIDTH}px repeat(${dates.length}, minmax(${MIN_CELL_WIDTH}px, 1fr))`;

  // How much precision a cell can hold depends on how wide the columns end up,
  // which only the laid-out grid knows — 21 columns fit "-4", 45 fit "-4.2".
  // A callback ref rather than an effect: the track is rendered inside
  // QueryState, so it does not exist yet when a mount-time effect would run.
  const [trackWidth, setTrackWidth] = useState(0);
  const trackObserver = useRef<ResizeObserver | null>(null);

  const attachTrack = useCallback((node: HTMLDivElement | null) => {
    trackObserver.current?.disconnect();
    trackObserver.current = null;
    if (!node) return;
    setTrackWidth(node.clientWidth);
    const observer = new ResizeObserver(([entry]) => setTrackWidth(entry.contentRect.width));
    observer.observe(node);
    trackObserver.current = observer;
  }, []);

  useEffect(() => () => trackObserver.current?.disconnect(), []);

  const cellWidth = dates.length
    ? (Math.max(trackWidth, LABEL_WIDTH + dates.length * MIN_CELL_WIDTH) - LABEL_WIDTH) / dates.length
    : 0;
  /** Flip to the panel ground once a cell is saturated enough to swallow body text. */
  const cellTextColour = (value: number | null): string => {
    if (value == null || !Number.isFinite(value)) return ct.textMuted;
    return Math.min(Math.abs(value) / bound, 1) >= 0.55 ? ct.background : ct.text;
  };

  // No '+' in-cell: the colour already carries the sign, and dropping it buys
  // the digit that makes an integer fit a 21px column.
  const cellLabel = (value: number | null): string => {
    if (value == null || !Number.isFinite(value)) return '';
    if (cellWidth >= CELL_WIDTH_FOR_DECIMAL) return value.toFixed(1);
    // toFixed(0) turns -0.4 into '-0'; a signed zero in a cell just reads as noise.
    if (cellWidth >= CELL_WIDTH_FOR_INTEGER) return value.toFixed(0).replace('-0', '0');
    return '';
  };

  // One readout line rather than a tooltip per cell — thousands of MUI
  // Tooltips is not a thing you can mount.
  const readout = (() => {
    if (hovered && rows[hovered.row]) {
      const row = rows[hovered.row];
      const value = row.values[hovered.col];
      return (
        <>
          <Box component="span" sx={{ color: 'text.primary' }}>
            {row.name}
          </Box>
          {`  ·  ${dates[hovered.col]} (${bar.tick}-${hovered.col})  ·  `}
          <Box
            component="span"
            sx={{ color: value == null ? 'text.tertiary' : ct.pnlColor(value), fontWeight: 600 }}
          >
            {value == null ? 'no bar' : formatRs(value)}
          </Box>
        </>
      );
    }
    if (dates.length === 0) return null;
    const shown = topN > 0 ? `top ${rows.length}` : `${rows.length} sectors`;
    const measure = METRICS.find((m) => m.value === metric);
    const span = `${bar.tick}-0 ${dates[0]} → ${bar.tick}-${dates.length - 1} ${dates[dates.length - 1]}`;
    return `${shown} by ${bar.tick}-0  ·  ${span}  ·  ${measure?.label} ${describeMetric(metric, data?.window, bar.unit)}`;
  })();

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
        <ToggleButtonGroup
          value={metric}
          exclusive
          size="small"
          onChange={(_, value) => {
            if (value) {
              setMetric(value as SectorRsMetric);
              setHovered(null);
            }
          }}
        >
          {METRICS.map(({ value, label }) => (
            <ToggleButton
              key={value}
              value={value}
              title={`${label} — ${describeMetric(value, data?.window, bar.unit)}`}
            >
              {label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>

        <ToggleButtonGroup
          value={timeframe}
          exclusive
          size="small"
          onChange={(_, value) => {
            if (value) {
              setTimeframe(value as SectorRsTimeframe);
              setHovered(null);
            }
          }}
        >
          {TIMEFRAMES.map(({ value, label }) => (
            <ToggleButton
              key={value}
              value={value}
              title={value === 'weekly' ? 'One bar per calendar week, last traded close' : 'One bar per session'}
            >
              {label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>

        <FormControl size="small" sx={{ minWidth: 120 }}>
          <InputLabel>Sectors</InputLabel>
          <Select
            value={topN}
            label="Sectors"
            onChange={(e) => setTopN(Number(e.target.value))}
          >
            {TOP_N_OPTIONS.map((n) => (
              <MenuItem key={n} value={n}>
                {n === 0 ? 'All' : `Top ${n}`}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl size="small" sx={{ minWidth: 130 }}>
          <InputLabel>Range</InputLabel>
          <Select
            value={range}
            label="Range"
            onChange={(e) => {
              setRange(Number(e.target.value));
              setHovered(null);
            }}
          >
            {RANGE_OPTIONS.map((n) => (
              <MenuItem key={n} value={n}>
                {`${bar.tick}-0 → ${bar.tick}-${n}`}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <Box sx={{ flexGrow: 1 }} />

        {/* Legend */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexShrink: 0 }}>
          <Typography variant="mono" sx={{ fontSize: '0.6875rem', color: 'text.tertiary' }}>
            {formatRs(-bound)}
          </Typography>
          <Box sx={{ display: 'flex', height: 10, borderRadius: 0.5, overflow: 'hidden' }}>
            {Array.from({ length: 13 }, (_, i) => {
              const value = -bound + (i * 2 * bound) / 12;
              return (
                <div key={i} style={{ width: 12, height: '100%', background: cellColour(value) }} />
              );
            })}
          </Box>
          <Typography variant="mono" sx={{ fontSize: '0.6875rem', color: 'text.tertiary' }}>
            {formatRs(bound)}
          </Typography>
        </Box>
      </Stack>

      <QueryState
        isLoading={loading}
        error={error}
        isEmpty={!loading && !error && rows.length === 0}
        onRetry={() => {
          cache.current.clear();
          setReloadKey((k) => k + 1);
        }}
        loadingLabel="Computing relative strength"
        emptyTitle="No relative strength data"
        emptyDescription={`No sector series at level ${level} overlaps the ${data?.benchmark ?? 'benchmark'} history.`}
      >
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          <Typography
            variant="mono"
            sx={{ fontSize: '0.75rem', color: 'text.tertiary', minHeight: 18 }}
            noWrap
          >
            {readout}
          </Typography>

          <Box ref={attachTrack} sx={{ overflowX: 'auto', overflowY: 'hidden' }}>
            <div style={{ minWidth: LABEL_WIDTH + dates.length * MIN_CELL_WIDTH }}>
              {/* Day axis: T-0 at the left edge, oldest on the right. */}
              <div style={{ display: 'grid', gridTemplateColumns, alignItems: 'end', marginBottom: 4 }}>
                <div />
                {dates.map((date, col) => (
                  <div
                    key={date}
                    style={{
                      fontFamily: fontFamily.mono,
                      fontSize: 10,
                      lineHeight: 1.2,
                      whiteSpace: 'nowrap',
                      color: hovered?.col === col ? ct.text : ct.textMuted,
                    }}
                  >
                    {col % TICK_EVERY === 0 ? `${bar.tick}-${col}` : ''}
                  </div>
                ))}
              </div>

              {rows.map((row, rowIndex) => {
                const latest = row.values[0];
                const isHoveredRow = hovered?.row === rowIndex;
                return (
                  <div
                    key={row.id}
                    style={{
                      display: 'grid',
                      gridTemplateColumns,
                      alignItems: 'center',
                      gap: 1,
                      marginBottom: 1,
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'baseline',
                        justifyContent: 'space-between',
                        gap: 8,
                        paddingRight: 8,
                        minWidth: 0,
                        background: isHoveredRow ? alpha(ct.text, 0.06) : 'transparent',
                      }}
                      title={row.name}
                    >
                      <span
                        style={{
                          fontFamily: fontFamily.sans,
                          fontSize: 12,
                          color: ct.textMuted,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          minWidth: 0,
                        }}
                      >
                        {row.name}
                      </span>
                      <span
                        style={{
                          fontFamily: fontFamily.mono,
                          fontSize: 11,
                          fontWeight: 600,
                          flexShrink: 0,
                          color: latest == null ? ct.textMuted : ct.pnlColor(latest),
                        }}
                      >
                        {latest == null ? '—' : formatRs(latest)}
                      </span>
                    </div>

                    {row.values.map((value, col) => (
                      <div
                        key={dates[col] ?? col}
                        onMouseEnter={() => setHovered({ row: rowIndex, col })}
                        style={{
                          height: ROW_HEIGHT,
                          background: cellColour(value),
                          borderRadius: 1,
                          cursor: 'crosshair',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          overflow: 'hidden',
                          fontFamily: fontFamily.mono,
                          fontSize: CELL_FONT_SIZE,
                          lineHeight: 1,
                          color: cellTextColour(value),
                          boxShadow:
                            isHoveredRow && hovered?.col === col
                              ? `inset 0 0 0 1px ${ct.accent}`
                              : 'none',
                        }}
                      >
                        {cellLabel(value)}
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>
          </Box>
        </Box>
      </QueryState>
    </Box>
  );
}
