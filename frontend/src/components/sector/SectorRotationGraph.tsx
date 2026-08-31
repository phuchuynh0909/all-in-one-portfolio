import { useEffect, useMemo, useState } from 'react';
import { Box, FormControl, InputLabel, MenuItem, Select, Stack, Typography } from '@mui/material';
import { alpha } from '@mui/material/styles';

import {
  fetchSectorRotation,
  type SectorRotation,
  type SectorRotationRow,
  type SectorRsTimeframe,
} from '../../lib/services/timeseries';
import { fontFamily, useChartTheme } from '../../theme';
import { QueryState } from '../ui';

/** Bars of tail per sector — how far back the trail runs. */
const TAIL_OPTIONS = [4, 8, 13, 26];
const DEFAULT_TAIL = 8;

/** 0 = plot every sector. Levels 3 and 4 are a hairball otherwise. */
const PLOT_OPTIONS = [10, 15, 20, 0];
const DEFAULT_PLOT = 10;

// viewBox units; the SVG scales to the panel width.
const W = 720;
const H = 520;
const M = { top: 28, right: 24, bottom: 40, left: 52 };

const BENCHMARK = 100;

type Point = { x: number; y: number };

type ChartTheme = ReturnType<typeof useChartTheme>;

/** Reading order: where you want to be, then the two transitions, then not. */
const QUADRANTS: { label: string; tone: (ct: ChartTheme) => string }[] = [
  { label: 'Leading', tone: (ct) => ct.up },
  { label: 'Improving', tone: (ct) => ct.textMuted },
  { label: 'Weakening', tone: (ct) => ct.textMuted },
  { label: 'Lagging', tone: (ct) => ct.down },
];

const quadrantOf = (x: number, y: number): string =>
  x >= BENCHMARK
    ? y >= BENCHMARK
      ? 'Leading'
      : 'Weakening'
    : y >= BENCHMARK
      ? 'Improving'
      : 'Lagging';

/** Last non-null pair in a tail — the sector's current position. */
const headOf = (row: SectorRotationRow): Point | null => {
  for (let i = row.ratio.length - 1; i >= 0; i -= 1) {
    const x = row.ratio[i];
    const y = row.momentum[i];
    if (x != null && y != null) return { x, y };
  }
  return null;
};

/**
 * Relative rotation graph: RS-ratio (x) against RS-momentum (y), both centred on
 * 100 — the benchmark.
 *
 * Answers the question the heatmap cannot: not just who is strong, but who is
 * strong *and still strengthening*. A sector travels clockwise through the
 * quadrants — improving, leading, weakening, lagging — and the tail shows which
 * way it is going, which is the whole reason to draw it as a scatter.
 *
 * Hand-rolled SVG: quadrant grounds, one polyline plus a head per sector and
 * per-sector labels are all things a chart library would need fighting, and
 * `useChartTheme` gives the concrete colours inline styles need.
 */
export default function SectorRotationGraph({
  level,
  timeframe = 'daily',
}: {
  level: number;
  timeframe?: SectorRsTimeframe;
}) {
  const ct = useChartTheme();
  const [data, setData] = useState<SectorRotation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [tail, setTail] = useState<number>(DEFAULT_TAIL);
  const [plotCount, setPlotCount] = useState<number>(DEFAULT_PLOT);
  const [hovered, setHovered] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const result = await fetchSectorRotation(level, { tail, timeframe });
        if (!cancelled) setData(result);
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
  }, [level, tail, timeframe, reloadKey]);

  /**
   * The server orders leading → improving → weakening → lagging, then boldest
   * within each. Honour that rather than re-sorting by distance from the centre:
   * distance alone fills a capped plot with the deepest laggards, which is the
   * opposite of what someone hunting the dominant sector opened this for.
   */
  const plotted = useMemo(() => {
    const rows = data?.rows ?? [];
    return plotCount > 0 ? rows.slice(0, plotCount) : rows;
  }, [data, plotCount]);

  /**
   * Axes stay symmetric around 100 so the quadrant lines sit dead centre —
   * otherwise a strong tape shifts the crosshair and every quadrant reading
   * silently changes meaning.
   */
  const scale = useMemo(() => {
    let halfX = 1.5;
    let halfY = 1.5;
    for (const row of plotted) {
      for (let i = 0; i < row.ratio.length; i += 1) {
        const x = row.ratio[i];
        const y = row.momentum[i];
        if (x != null) halfX = Math.max(halfX, Math.abs(x - BENCHMARK));
        if (y != null) halfY = Math.max(halfY, Math.abs(y - BENCHMARK));
      }
    }
    halfX *= 1.12;
    halfY *= 1.12;
    const innerW = W - M.left - M.right;
    const innerH = H - M.top - M.bottom;
    return {
      halfX,
      halfY,
      px: (x: number) => M.left + ((x - (BENCHMARK - halfX)) / (2 * halfX)) * innerW,
      py: (y: number) => M.top + (1 - (y - (BENCHMARK - halfY)) / (2 * halfY)) * innerH,
      innerW,
      innerH,
    };
  }, [plotted]);

  const cx = scale.px(BENCHMARK);
  const cy = scale.py(BENCHMARK);

  const quadrants = [
    { label: 'Leading', x: cx, y: M.top, w: M.left + scale.innerW - cx, h: cy - M.top, tone: ct.up },
    { label: 'Weakening', x: cx, y: cy, w: M.left + scale.innerW - cx, h: M.top + scale.innerH - cy, tone: ct.flat },
    { label: 'Lagging', x: M.left, y: cy, w: cx - M.left, h: M.top + scale.innerH - cy, tone: ct.down },
    { label: 'Improving', x: M.left, y: M.top, w: cx - M.left, h: cy - M.top, tone: ct.flat },
  ];

  const readout = (() => {
    if (!data) return null;
    if (hovered != null) {
      const row = data.rows.find((r) => r.id === hovered);
      const head = row ? headOf(row) : null;
      if (row && head) {
        return (
          <>
            <Box component="span" sx={{ color: 'text.primary' }}>
              {row.name}
            </Box>
            {`  ·  ratio ${head.x.toFixed(2)}  ·  momentum ${head.y.toFixed(2)}  ·  ${quadrantOf(head.x, head.y)}`}
          </>
        );
      }
    }
    const shown = plotCount > 0 ? `top ${plotted.length}` : `${plotted.length} sectors`;
    return `${shown} of ${data.rows.length}  ·  ${data.dates.length}-bar tail to ${data.dates[data.dates.length - 1]}  ·  vs ${data.benchmark} (ratio ${data.window}, momentum ${data.momentum_window})`;
  })();

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
        <FormControl size="small" sx={{ minWidth: 120 }}>
          <InputLabel>Sectors</InputLabel>
          <Select value={plotCount} label="Sectors" onChange={(e) => setPlotCount(Number(e.target.value))}>
            {PLOT_OPTIONS.map((n) => (
              <MenuItem key={n} value={n}>
                {n === 0 ? 'All' : `Top ${n}`}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl size="small" sx={{ minWidth: 120 }}>
          <InputLabel>Tail</InputLabel>
          <Select
            value={tail}
            label="Tail"
            onChange={(e) => {
              setTail(Number(e.target.value));
              setHovered(null);
            }}
          >
            {TAIL_OPTIONS.map((n) => (
              <MenuItem key={n} value={n}>
                {`${n} ${timeframe === 'weekly' ? 'weeks' : 'bars'}`}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>

      <QueryState
        isLoading={loading}
        error={error}
        isEmpty={!loading && !error && plotted.length === 0}
        onRetry={() => setReloadKey((k) => k + 1)}
        loadingLabel="Computing rotation"
        emptyTitle="No rotation data"
        emptyDescription={`No sector series at level ${level} to plot.`}
      >
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          <Typography
            variant="mono"
            sx={{ fontSize: '0.75rem', color: 'text.tertiary', minHeight: 18 }}
            noWrap
          >
            {readout}
          </Typography>

          <Box
            sx={{
              display: 'flex',
              flexDirection: { xs: 'column', md: 'row' },
              alignItems: 'flex-start',
              justifyContent: 'space-between',
              gap: 2,
              minWidth: 0,
            }}
          >
          <Box sx={{ flex: '1 1 0', minWidth: 0, maxWidth: 900 }}>
          <Box
            component="svg"
            viewBox={`0 0 ${W} ${H}`}
            // A rotation graph has to stay roughly square — both axes are
            // "distance from 100", so a stretched aspect misreads the angle of
            // travel. It takes the width it needs and the legend uses the rest.
            sx={{
              width: '100%',
              height: 'auto',
              maxHeight: 620,
              display: 'block',
              overflow: 'visible',
            }}
            onMouseLeave={() => setHovered(null)}
          >
            {quadrants.map((q) => (
              <g key={q.label}>
                <rect x={q.x} y={q.y} width={q.w} height={q.h} fill={alpha(q.tone, 0.05)} />
                <text
                  x={q.label === 'Leading' || q.label === 'Weakening' ? q.x + q.w - 8 : q.x + 8}
                  y={q.label === 'Leading' || q.label === 'Improving' ? q.y + 16 : q.y + q.h - 8}
                  textAnchor={q.label === 'Leading' || q.label === 'Weakening' ? 'end' : 'start'}
                  fill={alpha(q.tone, 0.55)}
                  style={{ fontFamily: fontFamily.mono, fontSize: 11, letterSpacing: 0.5 }}
                >
                  {q.label.toUpperCase()}
                </text>
              </g>
            ))}

            {/* The benchmark crosshair: both axes are 100. */}
            <line x1={cx} y1={M.top} x2={cx} y2={M.top + scale.innerH} stroke={ct.grid} strokeWidth={1} />
            <line x1={M.left} y1={cy} x2={M.left + scale.innerW} y2={cy} stroke={ct.grid} strokeWidth={1} />
            <rect
              x={M.left}
              y={M.top}
              width={scale.innerW}
              height={scale.innerH}
              fill="none"
              stroke={ct.border}
              strokeWidth={1}
            />

            {plotted.map((row, index) => {
              const colour = ct.seriesColor(index);
              const head = headOf(row);
              if (!head) return null;
              const points = row.ratio
                .map((x, i) => ({ x, y: row.momentum[i] }))
                .filter((p): p is Point => p.x != null && p.y != null)
                .map((p) => `${scale.px(p.x).toFixed(1)},${scale.py(p.y).toFixed(1)}`)
                .join(' ');
              const isHovered = hovered === row.id;
              const dim = hovered != null && !isHovered;

              return (
                <g
                  key={row.id}
                  opacity={dim ? 0.22 : 1}
                  onMouseEnter={() => setHovered(row.id)}
                  style={{ cursor: 'crosshair' }}
                >
                  <polyline
                    points={points}
                    fill="none"
                    stroke={colour}
                    strokeWidth={isHovered ? 2 : 1.25}
                    strokeOpacity={0.75}
                    strokeLinecap="round"
                  />
                  {/* Fat invisible hit line — a 1px stroke is unhittable. */}
                  <polyline points={points} fill="none" stroke="transparent" strokeWidth={10} />
                  <circle
                    cx={scale.px(head.x)}
                    cy={scale.py(head.y)}
                    r={isHovered ? 6 : 4.5}
                    fill={colour}
                    stroke={ct.background}
                    strokeWidth={1.5}
                  />
                  <text
                    x={scale.px(head.x) + 8}
                    y={scale.py(head.y) + 3.5}
                    fill={isHovered ? ct.text : ct.textMuted}
                    style={{
                      fontFamily: fontFamily.sans,
                      fontSize: 10,
                      fontWeight: isHovered ? 700 : 400,
                      pointerEvents: 'none',
                    }}
                  >
                    {row.name.length > 26 ? `${row.name.slice(0, 25)}…` : row.name}
                  </text>
                </g>
              );
            })}

            <text
              x={M.left + scale.innerW / 2}
              y={H - 10}
              textAnchor="middle"
              fill={ct.textMuted}
              style={{ fontFamily: fontFamily.mono, fontSize: 11 }}
            >
              RS-Ratio — relative strength vs {data?.benchmark ?? 'benchmark'} →
            </text>
            <text
              x={14}
              y={M.top + scale.innerH / 2}
              textAnchor="middle"
              transform={`rotate(-90 14 ${M.top + scale.innerH / 2})`}
              fill={ct.textMuted}
              style={{ fontFamily: fontFamily.mono, fontSize: 11 }}
            >
              RS-Momentum — is it strengthening? ↑
            </text>
          </Box>
          </Box>

          {/* Who sits where, in words — the answer to "which sector is
              dominant" is the top of the Leading list. */}
          <Box
            sx={{
              flex: '0 0 auto',
              width: { xs: '100%', md: 260 },
              display: 'flex',
              flexDirection: 'column',
              gap: 1.5,
            }}
          >
            {QUADRANTS.map((quadrant) => {
              const members = plotted
                .map((row, index) => ({ row, index, head: headOf(row) }))
                .filter(({ head }) => head && quadrantOf(head.x, head.y) === quadrant.label);
              return (
                <Box key={quadrant.label}>
                  <Typography
                    variant="mono"
                    sx={{
                      fontSize: '0.6875rem',
                      letterSpacing: 0.5,
                      color: quadrant.tone(ct),
                      display: 'block',
                      mb: 0.5,
                    }}
                  >
                    {`${quadrant.label.toUpperCase()}  ${members.length}`}
                  </Typography>
                  {members.length === 0 && (
                    <Typography variant="caption" sx={{ color: 'text.disabled' }}>
                      —
                    </Typography>
                  )}
                  {members.map(({ row, index, head }) => (
                    <Box
                      key={row.id}
                      onMouseEnter={() => setHovered(row.id)}
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 0.75,
                        py: 0.15,
                        cursor: 'crosshair',
                        opacity: hovered != null && hovered !== row.id ? 0.4 : 1,
                      }}
                    >
                      <Box
                        sx={{
                          width: 8,
                          height: 8,
                          borderRadius: '50%',
                          flexShrink: 0,
                          backgroundColor: ct.seriesColor(index),
                        }}
                      />
                      <Typography variant="caption" sx={{ color: 'text.secondary', minWidth: 0 }} noWrap>
                        {row.name}
                      </Typography>
                      <Box sx={{ flexGrow: 1 }} />
                      <Typography
                        variant="mono"
                        sx={{ fontSize: '0.625rem', color: 'text.tertiary', flexShrink: 0 }}
                      >
                        {head ? head.x.toFixed(1) : '—'}
                      </Typography>
                    </Box>
                  ))}
                </Box>
              );
            })}
          </Box>
          </Box>
        </Box>
      </QueryState>
    </Box>
  );
}
