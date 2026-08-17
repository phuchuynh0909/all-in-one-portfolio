import { useMemo, useState } from 'react';
import {
  Box,
  Container,
  Typography,
  Paper,
  Stack,
  CircularProgress,
  Alert,
  Autocomplete,
  TextField,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableRow,
} from '@mui/material';
import {
  CartesianGrid,
  Cell,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts';
import {
  useBacktestPlot,
  useBacktestPlotStrategies,
  useWatchlistSymbols,
  type MaeMfeTrade,
} from '../lib/services/backtest';

// Star marker used to highlight the latest / current position on the scatter.
function StarDot(props: { cx?: number; cy?: number; fill?: string }) {
  const { cx, cy, fill = '#facc15' } = props;
  if (cx == null || cy == null) return null;
  const spikes = 5;
  const outer = 9;
  const inner = 4;
  const points: string[] = [];
  for (let i = 0; i < spikes * 2; i += 1) {
    const r = i % 2 === 0 ? outer : inner;
    const angle = (Math.PI / spikes) * i - Math.PI / 2;
    points.push(`${cx + r * Math.cos(angle)},${cy + r * Math.sin(angle)}`);
  }
  return <polygon points={points.join(' ')} fill={fill} stroke="#000" strokeWidth={0.75} />;
}

function MaeMfeTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: MaeMfeTrade }> }) {
  if (!active || !payload?.length) return null;
  const t = payload[0].payload;
  const fmt = (v?: number | null) => (v == null ? '—' : `${v.toFixed(2)}%`);
  const fmtDate = (v?: string | null) => (v ? v.slice(0, 10) : '—');
  return (
    <Box
      sx={{
        p: 1.25,
        borderRadius: 1,
        border: '1px solid rgba(99, 102, 241, 0.4)',
        backgroundColor: 'rgba(10, 10, 20, 0.95)',
        fontSize: '0.72rem',
        fontFamily: 'monospace',
        minWidth: 180,
      }}
    >
      <Typography sx={{ fontSize: '0.75rem', fontWeight: 700, mb: 0.5 }}>
        Trade #{t.index}
        {t.is_latest ? ' · latest' : ''}
        {t.is_open ? ' · open' : ''}
      </Typography>
      <div>MFE: {fmt(t.mfe)}</div>
      <div>MAE: {fmt(t.mae)}</div>
      <div>Return: {fmt(t.return_pct)}</div>
      <div>Dir: {t.direction}</div>
      <div>Entry: {fmtDate(t.entry_time)}</div>
      <div>Exit: {t.is_open ? 'open' : fmtDate(t.exit_time)}</div>
    </Box>
  );
}

// State that persists to localStorage so selections survive a page refresh.
function usePersistedState(key: string, defaultValue: string) {
  const [value, setValue] = useState<string>(() => {
    try {
      return localStorage.getItem(key) ?? defaultValue;
    } catch {
      return defaultValue;
    }
  });
  const set = (next: string) => {
    setValue(next);
    try {
      localStorage.setItem(key, next);
    } catch {
      /* ignore write failures (private mode, quota) */
    }
  };
  return [value, set] as const;
}

export default function BacktestVisualization() {
  const [selectedSymbol, setSelectedSymbol] = usePersistedState('backtest.symbol', 'VCG');
  const [selectedStrategy, setSelectedStrategy] = usePersistedState('backtest.strategy', 'Breakout TTM');

  const { data: strategiesData } = useBacktestPlotStrategies();
  const strategies = strategiesData || [];

  const { data: symbolsData } = useWatchlistSymbols();
  const symbols = symbolsData || [];

  const { data, isLoading, error } = useBacktestPlot(selectedSymbol, undefined, selectedStrategy);

  // Split trades into winners / losers / the latest (current) position so each
  // gets its own colour and the current dot can be drawn with a star marker.
  const scatter = useMemo(() => {
    const trades = data?.mae_mfe_trades ?? [];
    const winners = trades.filter((t) => !t.is_latest && (t.return_pct ?? 0) >= 0);
    const losers = trades.filter((t) => !t.is_latest && (t.return_pct ?? 0) < 0);
    const latest = trades.filter((t) => t.is_latest);
    const maxVal = trades.reduce((m, t) => Math.max(m, t.mae, t.mfe), 0);
    const axisMax = Math.ceil((maxVal || 1) * 1.1);
    return { trades, winners, losers, latest, axisMax };
  }, [data?.mae_mfe_trades]);

  const formatStatValue = (value: unknown) => {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'number') return value.toFixed(4);
    return String(value);
  };

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      <Paper
        sx={{
          p: 2,
          mb: 3,
          background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
          border: '1px solid rgba(99, 102, 241, 0.2)',
          borderRadius: 2,
        }}
      >
        <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={2}>
          <Autocomplete<string, false, true, false>
            value={selectedSymbol}
            onChange={(_event, newValue) => {
              if (newValue) setSelectedSymbol(newValue);
            }}
            options={symbols}
            sx={{
              minWidth: 150,
              '& .MuiOutlinedInput-notchedOutline': {
                borderColor: 'rgba(99, 102, 241, 0.3)',
              },
            }}
            renderInput={(params) => <TextField {...params} label="Symbol" size="small" />}
            disableClearable
            autoHighlight
          />
          <Autocomplete<string, false, true, false>
            value={selectedStrategy}
            onChange={(_event, newValue) => {
              if (newValue) setSelectedStrategy(newValue);
            }}
            options={strategies}
            sx={{
              minWidth: 220,
              '& .MuiOutlinedInput-notchedOutline': {
                borderColor: 'rgba(99, 102, 241, 0.3)',
              },
            }}
            renderInput={(params) => <TextField {...params} label="Strategy" size="small" />}
            disableClearable
            autoHighlight
          />
        </Stack>
      </Paper>

      {isLoading && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
          <CircularProgress />
        </Box>
      )}

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error instanceof Error ? error.message : 'Failed to load backtest plot'}
        </Alert>
      )}

      {data?.html && !isLoading && (
        <Paper
          sx={{
            p: 2,
            background: 'transparent',
            border: 'none',
            boxShadow: 'none',
          }}
        >
          <Box
            sx={{
              width: '100%',
              minHeight: 720,
              borderRadius: 2,
              overflow: 'hidden',
              border: '1px solid rgba(99, 102, 241, 0.15)',
              backgroundColor: 'rgba(10, 10, 20, 0.6)',
            }}
          >
            <iframe
              title={`Backtest plot for ${data.symbol}`}
              srcDoc={data.html}
              style={{ width: '100%', height: 900, border: 'none' }}
              sandbox="allow-scripts allow-same-origin"
            />
          </Box>
        </Paper>
      )}

      {scatter.trades.length > 0 && !isLoading && (
        <Paper
          sx={{
            p: 2,
            mt: 3,
            border: '1px solid rgba(99, 102, 241, 0.2)',
            borderRadius: 2,
            background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
          }}
        >
          <Stack direction="row" alignItems="baseline" justifyContent="space-between" flexWrap="wrap" gap={1}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
              MAE / MFE Scatter
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              X = Max Adverse Excursion, Y = Max Favorable Excursion (% of entry). Dots above the
              dashed line ran more in your favour than against you. ★ = latest position.
            </Typography>
          </Stack>
          <Box sx={{ width: '100%', height: 460, mt: 1 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 16, right: 24, bottom: 40, left: 16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(99, 102, 241, 0.15)" />
                <XAxis
                  type="number"
                  dataKey="mae"
                  name="MAE"
                  unit="%"
                  domain={[0, scatter.axisMax]}
                  tick={{ fill: 'rgba(200,200,220,0.75)', fontSize: 12 }}
                  label={{ value: 'MAE [%]', position: 'insideBottom', offset: -20, fill: 'rgba(200,200,220,0.75)' }}
                />
                <YAxis
                  type="number"
                  dataKey="mfe"
                  name="MFE"
                  unit="%"
                  domain={[0, scatter.axisMax]}
                  tick={{ fill: 'rgba(200,200,220,0.75)', fontSize: 12 }}
                  label={{ value: 'MFE [%]', angle: -90, position: 'insideLeft', fill: 'rgba(200,200,220,0.75)' }}
                />
                <ZAxis type="number" range={[60, 60]} />
                <ReferenceLine
                  segment={[
                    { x: 0, y: 0 },
                    { x: scatter.axisMax, y: scatter.axisMax },
                  ]}
                  stroke="rgba(148, 163, 184, 0.5)"
                  strokeDasharray="4 4"
                  ifOverflow="hidden"
                />
                <Tooltip content={<MaeMfeTooltip />} cursor={{ strokeDasharray: '3 3' }} />
                <Legend verticalAlign="top" height={28} wrapperStyle={{ fontSize: 12 }} />
                <Scatter name="Winners" data={scatter.winners} fill="#22c55e" fillOpacity={0.8}>
                  {scatter.winners.map((t) => (
                    <Cell key={t.index} fill={t.is_open ? '#4ade80' : '#22c55e'} />
                  ))}
                </Scatter>
                <Scatter name="Losers" data={scatter.losers} fill="#ef4444" fillOpacity={0.8}>
                  {scatter.losers.map((t) => (
                    <Cell key={t.index} fill={t.is_open ? '#f87171' : '#ef4444'} />
                  ))}
                </Scatter>
                <Scatter name="Latest position" data={scatter.latest} shape={<StarDot />} />
              </ScatterChart>
            </ResponsiveContainer>
          </Box>
        </Paper>
      )}

      {data?.params && !isLoading && (
        <Paper
          sx={{
            p: 2,
            mt: 3,
            border: '1px solid rgba(99, 102, 241, 0.2)',
            borderRadius: 2,
            background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
          }}
        >
          <Typography variant="subtitle1" sx={{ mb: 1.5, fontWeight: 600 }}>
            Strategy Parameters
          </Typography>
          <Box
            sx={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 1,
            }}
          >
            {Object.entries(data.params).map(([key, value]) => (
              <Box
                key={key}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                  px: 1.5,
                  py: 0.5,
                  borderRadius: 1,
                  border: '1px solid rgba(99, 102, 241, 0.25)',
                  backgroundColor: 'rgba(99, 102, 241, 0.08)',
                  fontSize: '0.75rem',
                }}
              >
                <Typography
                  component="span"
                  sx={{ fontSize: '0.72rem', color: 'text.secondary', fontFamily: 'monospace' }}
                >
                  {key}
                </Typography>
                <Typography
                  component="span"
                  sx={{ fontSize: '0.72rem', color: 'rgba(99, 102, 241, 0.9)', fontFamily: 'monospace', fontWeight: 600 }}
                >
                  {formatStatValue(value)}
                </Typography>
              </Box>
            ))}
          </Box>
        </Paper>
      )}

      {data?.stats && !isLoading && (
        <Paper
          sx={{
            p: 2,
            mt: 3,
            border: '1px solid rgba(99, 102, 241, 0.2)',
            borderRadius: 2,
            background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.9) 0%, rgba(30, 30, 40, 0.95) 100%)',
          }}
        >
          <Typography variant="subtitle1" sx={{ mb: 1.5, fontWeight: 600 }}>
            Backtest Stats
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableBody>
                {Object.entries(data.stats).map(([key, value]) => (
                  <TableRow key={key}>
                    <TableCell sx={{ borderBottom: '1px solid rgba(99, 102, 241, 0.1)' }}>
                      {key}
                    </TableCell>
                    <TableCell
                      align="right"
                      sx={{ borderBottom: '1px solid rgba(99, 102, 241, 0.1)', fontFamily: 'monospace' }}
                    >
                      {formatStatValue(value)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      )}
    </Container>
  );
}
