/**
 * Research reports for the chart's active symbol.
 *
 * Scoped to the current symbol by default — that is the filter this context
 * implies — with an escape hatch to the whole feed and a source filter, since
 * one ticker can attract notes from several brokers. Brings its own header so
 * the chart's right-hand rail can swap it in beside Watchlist/Anomalies/Notes.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box,
  Chip,
  CircularProgress,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import { fetchReports, type Report } from '../../lib/services/report';
import { EmptyState, ErrorState } from '../ui';

type Scope = 'symbol' | 'all';
const ALL_SOURCES = '__all__';

export default function ReportsPanel({
  symbol,
  onSelect,
}: {
  symbol: string;
  /** Opens the report detail dialog owned by the page. */
  onSelect: (report: Report) => void;
}) {
  const [scope, setScope] = useState<Scope>('symbol');
  const [source, setSource] = useState<string>(ALL_SOURCES);
  const [reports, setReports] = useState<Report[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (refresh = false) => {
      const sym = symbol.trim().toUpperCase();
      setLoading(true);
      setError(null);
      try {
        // The backend filters by symbol; scope 'all' simply omits it.
        setReports(await fetchReports(scope === 'symbol' && sym ? sym : undefined, refresh));
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load reports');
      } finally {
        setLoading(false);
      }
    },
    [symbol, scope],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // Source options come from what actually came back, so the filter can never
  // offer a broker with no reports in the current scope.
  const sources = useMemo(
    () => Array.from(new Set(reports.map((r) => r.nguon).filter(Boolean))).sort(),
    [reports],
  );

  // A source selected under one scope may not exist in the next one.
  useEffect(() => {
    if (source !== ALL_SOURCES && !sources.includes(source)) setSource(ALL_SOURCES);
  }, [sources, source]);

  const visible = useMemo(
    () => (source === ALL_SOURCES ? reports : reports.filter((r) => r.nguon === source)),
    [reports, source],
  );

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, width: '100%' }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ px: 0.75, pb: 0.5, flexShrink: 0, minWidth: 0 }}
      >
        <Typography sx={{ fontSize: 11.5, fontWeight: 700, letterSpacing: 0.3 }}>REPORTS</Typography>
        {scope === 'symbol' && (
          <Typography sx={{ fontSize: 11.5, color: 'primary.light', fontWeight: 700 }}>
            {symbol}
          </Typography>
        )}
        {loading && <CircularProgress size={10} />}
        <Box sx={{ flex: 1 }} />
        {!loading && (
          <Typography sx={{ fontSize: 10, color: 'text.disabled', whiteSpace: 'nowrap' }}>
            {visible.length}
            {visible.length !== reports.length ? ` / ${reports.length}` : ''}
          </Typography>
        )}
        <Tooltip title="Refresh">
          <span>
            <IconButton
              size="small"
              onClick={() => void load(true)}
              disabled={loading}
              sx={{ p: 0.25 }}
            >
              <RefreshIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>

      {/* Filters */}
      <Stack spacing={0.5} sx={{ px: 0.75, pb: 0.75, flexShrink: 0 }}>
        <ToggleButtonGroup
          value={scope}
          exclusive
          size="small"
          fullWidth
          onChange={(_, next: Scope | null) => next && setScope(next)}
        >
          <ToggleButton value="symbol" sx={{ py: 0.25, fontSize: 10 }}>
            This symbol
          </ToggleButton>
          <ToggleButton value="all" sx={{ py: 0.25, fontSize: 10 }}>
            All
          </ToggleButton>
        </ToggleButtonGroup>

        <TextField
          select
          size="small"
          value={source}
          onChange={(e) => setSource(e.target.value)}
          disabled={sources.length === 0}
          SelectProps={{ sx: { fontSize: 11 } }}
          inputProps={{ 'aria-label': 'Filter by source' }}
        >
          <MenuItem value={ALL_SOURCES} sx={{ fontSize: 11 }}>
            All sources
          </MenuItem>
          {sources.map((s) => (
            <MenuItem key={s} value={s} sx={{ fontSize: 11 }}>
              {s}
            </MenuItem>
          ))}
        </TextField>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflowY: 'auto', px: 0.75, pb: 0.75 }}>
        {error ? (
          <ErrorState error={error} title="Could not load reports" onRetry={() => void load(true)} />
        ) : !loading && visible.length === 0 ? (
          <EmptyState
            compact
            title="No reports"
            description={
              scope === 'symbol'
                ? `Nothing published for ${symbol}.`
                : 'No reports match this filter.'
            }
          />
        ) : (
          <Stack spacing={0.5}>
            {visible.map((report) => (
              <Box
                key={report.id}
                onClick={() => onSelect(report)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onSelect(report);
                  }
                }}
                sx={{
                  p: 0.75,
                  border: 1,
                  borderColor: 'line.subtle',
                  borderRadius: 1,
                  bgcolor: 'surface.inset',
                  cursor: 'pointer',
                  '&:hover': { borderColor: 'line.strong', bgcolor: 'action.hover' },
                  '&:focus-visible': { outline: '2px solid', outlineColor: 'primary.main' },
                }}
              >
                <Stack direction="row" spacing={0.5} alignItems="flex-start">
                  <Typography
                    sx={{ fontSize: 11.5, fontWeight: 600, lineHeight: 1.35, flex: 1, minWidth: 0 }}
                  >
                    {report.tenbaocao}
                  </Typography>
                  <Tooltip title="Open source PDF">
                    <IconButton
                      size="small"
                      component="a"
                      href={report.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      // The row itself opens the detail dialog.
                      onClick={(e) => e.stopPropagation()}
                      sx={{ p: 0.25, flexShrink: 0 }}
                    >
                      <OpenInNewIcon sx={{ fontSize: 12 }} />
                    </IconButton>
                  </Tooltip>
                </Stack>

                <Stack direction="row" spacing={0.5} alignItems="center" sx={{ mt: 0.5 }} flexWrap="wrap" useFlexGap>
                  {scope === 'all' && report.mack && (
                    <Chip label={report.mack} size="small" color="primary" variant="outlined" />
                  )}
                  {report.nguon && <Chip label={report.nguon} size="small" variant="outlined" />}
                  <Box sx={{ flex: 1 }} />
                  {report.ngaykn && (
                    <Typography variant="mono" sx={{ fontSize: 9.5, color: 'text.tertiary' }}>
                      {new Date(report.ngaykn).toLocaleDateString()}
                    </Typography>
                  )}
                </Stack>
              </Box>
            ))}
          </Stack>
        )}
      </Box>
    </Box>
  );
}
