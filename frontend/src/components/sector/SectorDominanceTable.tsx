import { useEffect, useMemo, useState } from 'react';
import { Box, Chip, Tooltip, Typography } from '@mui/material';
import { alpha } from '@mui/material/styles';
import { DataGrid, type GridColDef } from '@mui/x-data-grid';

import {
  fetchSectorDominance,
  type SectorDominance,
  type SectorRsMetric,
  type SectorRsTimeframe,
} from '../../lib/services/timeseries';
import { useChartTheme } from '../../theme';
import { QueryState } from '../ui';

/**
 * Sectors ranked by sustained leadership rather than by the latest RS reading.
 *
 * The top of the heatmap's T-0 column is close to a coin flip — on this data the
 * strongest level-2 sector changed 18 times in 40 sessions across 9 different
 * sectors. So the score blends persistence, constituent breadth, RS momentum and
 * turnover share, each rank-scaled to 0-100. Every component is a sortable
 * column: the score is a starting point, not a verdict.
 */
export default function SectorDominanceTable({
  level,
  metric = 'mansfield',
  timeframe = 'daily',
  lookback = 41,
  selectedSectorId = null,
  onSectorSelect,
}: {
  level: number;
  metric?: SectorRsMetric;
  timeframe?: SectorRsTimeframe;
  lookback?: number;
  /** Highlighted row — the sector whose constituents are on screen. */
  selectedSectorId?: number | null;
  /** Rows become the sector picker for the page when this is given. */
  onSectorSelect?: (sectorId: number, sectorName: string) => void;
}) {
  const ct = useChartTheme();
  const [data, setData] = useState<SectorDominance | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const result = await fetchSectorDominance(level, { lookback, metric, timeframe });
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
  }, [level, metric, timeframe, lookback, reloadKey]);

  const rows = data?.rows ?? [];

  /**
   * Only watchlist symbols reach `ohlc_eod`, so breadth is measured over a
   * fraction of the mapped constituents — heavily so at levels 3 and 4. Say the
   * ratio out loud rather than letting the column imply full coverage.
   */
  const coverage = useMemo(
    () => ({
      rated: rows.reduce((sum, r) => sum + r.constituents_rated, 0),
      total: rows.reduce((sum, r) => sum + r.constituents, 0),
    }),
    [rows],
  );

  const columns = useMemo<GridColDef[]>(
    () => [
      {
        field: 'name',
        headerName: 'Sector',
        flex: 2,
        minWidth: 190,
      },
      {
        field: 'score',
        headerName: 'Score',
        width: 92,
        type: 'number',
        description: 'Mean of the rank-scaled components. Blank when the sector has too few constituents to judge.',
        renderCell: (params) => {
          const value = params.value as number | null;
          if (value == null) {
            return (
              <Tooltip title={`Fewer than ${data?.min_constituents ?? 3} mapped constituents`}>
                <Typography variant="caption" sx={{ color: 'text.disabled' }}>
                  n/a
                </Typography>
              </Tooltip>
            );
          }
          return (
            <Chip
              label={value.toFixed(1)}
              size="small"
              sx={{
                fontFamily: 'monospace',
                fontWeight: 600,
                // Amber is the accent, not a direction: the score is a rank across
                // sectors, not a gain, so green/red would misread it.
                backgroundColor: alpha(ct.accent, 0.08 + (value / 100) * 0.42),
                color: 'text.primary',
              }}
            />
          );
        },
      },
      {
        field: 'rs',
        headerName: 'RS now',
        width: 90,
        type: 'number',
        description: 'Latest relative-strength value — the heatmap’s T-0 column',
        valueFormatter: ({ value }) => (value == null ? '—' : (value as number).toFixed(2)),
        cellClassName: (params) =>
          params.value == null ? '' : (params.value as number) >= 0 ? 'rs-up' : 'rs-down',
      },
      {
        field: 'mean_rank',
        headerName: 'Avg rank',
        width: 96,
        type: 'number',
        description: 'Mean rank across sectors over the window. 1 = strongest, so lower is better.',
        valueFormatter: ({ value }) => (value == null ? '—' : (value as number).toFixed(2)),
      },
      {
        field: 'top_quintile_share',
        headerName: 'In top 20%',
        width: 106,
        type: 'number',
        description: 'Share of bars spent in the strongest fifth of sectors',
        valueFormatter: ({ value }) =>
          value == null ? '—' : `${((value as number) * 100).toFixed(0)}%`,
      },
      {
        field: 'breadth',
        headerName: 'Breadth',
        width: 128,
        type: 'number',
        description:
          'Share of constituents with positive RS, over the constituents that had a reading. A thin denominator is shown in brackets and is excluded from the score.',
        renderCell: (params) => {
          const value = params.value as number | null;
          const { constituents_rated: rated, constituents } = params.row;
          if (value == null) {
            return <Typography variant="caption" sx={{ color: 'text.disabled' }}>—</Typography>;
          }
          return (
            <Typography variant="mono" sx={{ fontSize: '0.75rem' }}>
              {`${(value * 100).toFixed(0)}% `}
              <Box
                component="span"
                sx={{ color: rated < 5 ? 'warning.main' : 'text.tertiary', fontSize: '0.6875rem' }}
              >
                {`(${rated}/${constituents})`}
              </Box>
            </Typography>
          );
        },
      },
      {
        field: 'momentum',
        headerName: 'RS slope',
        width: 100,
        type: 'number',
        description: 'Least-squares slope of the RS line per bar. Negative means the lead is fading.',
        valueFormatter: ({ value }) => (value == null ? '—' : (value as number).toFixed(3)),
        cellClassName: (params) =>
          params.value == null ? '' : (params.value as number) >= 0 ? 'rs-up' : 'rs-down',
      },
      {
        field: 'turnover_share',
        headerName: 'Turnover',
        width: 100,
        type: 'number',
        description: 'Share of this level’s constituent turnover (close × volume) over the window',
        valueFormatter: ({ value }) =>
          value == null ? '—' : `${((value as number) * 100).toFixed(1)}%`,
      },
    ],
    [ct, data?.min_constituents],
  );

  return (
    <QueryState
      isLoading={loading}
      error={error}
      isEmpty={!loading && !error && rows.length === 0}
      onRetry={() => setReloadKey((k) => k + 1)}
      loadingLabel="Scoring sectors"
      emptyTitle="No dominance data"
      emptyDescription={`No sector series at level ${level} to score.`}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        <Typography variant="mono" sx={{ fontSize: '0.75rem', color: 'text.tertiary' }} noWrap>
          {data
            ? `${rows.length} sectors  ·  ${data.lookback} ${data.timeframe === 'weekly' ? 'weeks' : 'sessions'} to ${data.as_of}  ·  ${data.metric} vs ${data.benchmark} (${data.window})  ·  breadth from ${coverage.rated}/${coverage.total} constituents`
            : ''}
        </Typography>

        <Box sx={{ height: 460, width: '100%' }}>
          <DataGrid
            rows={rows}
            columns={columns}
            density="compact"
            initialState={{ sorting: { sortModel: [{ field: 'score', sort: 'desc' }] } }}
            getRowClassName={(params) =>
              params.id === selectedSectorId ? 'sector-selected' : ''
            }
            onRowClick={(params) => onSectorSelect?.(params.row.id, params.row.name)}
            sx={{
              '& .rs-up': { color: ct.up },
              '& .rs-down': { color: ct.down },
              '& .MuiDataGrid-cell': { fontFamily: 'monospace', fontSize: '0.75rem' },
              '& .MuiDataGrid-cell[data-field="name"]': { fontFamily: 'inherit' },
              ...(onSectorSelect && {
                '& .MuiDataGrid-row': { cursor: 'pointer' },
                // Amber marks the selection: it is a UI state, not a direction.
                '& .sector-selected': {
                  backgroundColor: alpha(ct.accent, 0.14),
                  boxShadow: `inset 2px 0 0 ${ct.accent}`,
                },
                '& .sector-selected:hover': { backgroundColor: alpha(ct.accent, 0.2) },
              }),
            }}
          />
        </Box>
      </Box>
    </QueryState>
  );
}
