import { useEffect, useMemo, useState } from 'react';
import { DataGrid } from '@mui/x-data-grid';
import type { GridColDef, GridValueFormatterParams } from '@mui/x-data-grid';
import { Box, Typography } from '@mui/material';

import {
  fetchSectorConstituents,
  type SectorConstituents,
  type SectorRsMetric,
  type SectorRsTimeframe,
} from '../../lib/services/timeseries';
import { useChartTheme } from '../../theme';

/**
 * The stocks inside one sector, each with its own relative strength.
 *
 * RS uses the same measure, window and benchmark as the sector panels above, so
 * a constituent's number is directly comparable to its sector's — which is how
 * you tell a broadly strong sector from one being carried by two names. `rank`
 * percentiles the symbol against every constituent at this level, so 70 means
 * the same thing in Ngân hàng as in Thép.
 */
export default function StockList({
  sectorId,
  level,
  metric = 'mansfield',
  timeframe = 'daily',
}: {
  sectorId: number;
  level: number;
  metric?: SectorRsMetric;
  timeframe?: SectorRsTimeframe;
}) {
  const ct = useChartTheme();
  const [data, setData] = useState<SectorConstituents | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pageSize, setPageSize] = useState(10);

  useEffect(() => {
    if (!sectorId) return;
    let cancelled = false;

    setLoading(true);
    setError(null);
    fetchSectorConstituents(level, sectorId, { metric, timeframe })
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [sectorId, level, metric, timeframe]);

  /**
   * Server order is strongest first. Rows with neither an RS reading nor a
   * market cap are dropped as noise, but a covered symbol is always kept — the
   * old market-cap-only filter would have hidden strong names with a missing
   * cap, which is exactly what this panel is for.
   */
  const rows = useMemo(
    () =>
      (data?.rows ?? []).filter(
        (row) => row.rs != null || (row.vonhoa_d != null && Number(row.vonhoa_d) > 0),
      ),
    [data],
  );

  const columns = useMemo<GridColDef[]>(
    () => [
      { field: 'symbol', headerName: 'Symbol', width: 90 },
      { field: 'name', headerName: 'Name', flex: 1, minWidth: 130 },
      {
        field: 'rs',
        headerName: 'RS',
        width: 88,
        type: 'number',
        description: `${metric} vs ${data?.benchmark ?? 'benchmark'} over ${data?.window ?? ''} bars`,
        valueFormatter: (params: GridValueFormatterParams<number | null>) =>
          params.value == null ? '—' : (params.value as number).toFixed(2),
        cellClassName: (params) =>
          params.value == null ? '' : (params.value as number) >= 0 ? 'rs-up' : 'rs-down',
      },
      {
        field: 'rs_rank',
        headerName: 'Rank',
        width: 76,
        type: 'number',
        description: '1-99 percentile against every constituent at this level',
        valueFormatter: (params: GridValueFormatterParams<number | null>) =>
          params.value == null ? '—' : String(params.value),
      },
      {
        field: 'vonhoa_d',
        headerName: 'Market Cap (B)',
        width: 130,
        type: 'number',
        valueFormatter: (params: GridValueFormatterParams<number | null>) =>
          params.value == null
            ? '—'
            : Number(params.value).toLocaleString('en-US', { maximumFractionDigits: 0 }),
      },
    ],
    [metric, data?.benchmark, data?.window],
  );

  return (
    <Box sx={{ p: 1 }}>
      <Typography
        variant="mono"
        sx={{ fontSize: '0.6875rem', color: 'text.tertiary', display: 'block', mb: 0.5, px: 0.5 }}
        noWrap
      >
        {data
          ? `RS ${data.metric} vs ${data.benchmark} (${data.window})  ·  ${data.covered}/${data.mapped} with a series  ·  ${data.as_of ?? ''}`
          : ''}
      </Typography>

      <Box sx={{ height: 460, width: '100%' }}>
        <DataGrid
          sx={{
            border: 0,
            '& .rs-up': { color: ct.up },
            '& .rs-down': { color: ct.down },
            '& .MuiDataGrid-cell': { fontFamily: 'monospace', fontSize: '0.75rem' },
            '& .MuiDataGrid-cell[data-field="name"]': { fontFamily: 'inherit' },
          }}
          rows={rows}
          getRowId={(row) => row.symbol}
          columns={columns}
          density="compact"
          loading={loading}
          error={error}
          pageSize={pageSize}
          onPageSizeChange={setPageSize}
          rowsPerPageOptions={[10, 25, 50]}
        />
      </Box>
    </Box>
  );
}
