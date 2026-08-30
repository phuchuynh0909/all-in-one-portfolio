import { useMemo, useState } from 'react';
import {
  Box,
  Typography,
  Stack,
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
import { PageContainer, PageHeader, Panel, QueryState } from '../components/ui';
import { useChartTheme } from '../theme';

// Star marker used to highlight the latest / current position on the scatter.
function StarDot(props: { cx?: number; cy?: number; fill?: string; outline?: string }) {
  const { cx, cy, fill = 'currentColor', outline = 'transparent' } = props;
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
  return <polygon points={points.join(' ')} fill={fill} stroke={outline} strokeWidth={0.75} />;
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
        border: 1,
        borderColor: 'line.default',
        backgroundColor: 'surface.overlay',
        boxShadow: 3,
        fontSize: '0.72rem',
        fontFamily: 'var(--font-family-mono)',
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
  const ct = useChartTheme();
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
    <PageContainer>
      <PageHeader
        title="Backtest Visual"
        description="Equity curve, MAE/MFE excursion scatter and the parameters behind a single strategy run."
      />

      <Panel dense sx={{ mb: 2.5 }}>
        <Stack direction="row" alignItems="center" gap={2} flexWrap="wrap">
          <Autocomplete<string, false, true, false>
            value={selectedSymbol}
            onChange={(_event, newValue) => {
              if (newValue) setSelectedSymbol(newValue);
            }}
            options={symbols}
            sx={{ minWidth: 150 }}
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
            sx={{ minWidth: 240 }}
            renderInput={(params) => <TextField {...params} label="Strategy" size="small" />}
            disableClearable
            autoHighlight
          />
        </Stack>
      </Panel>

      <QueryState
        isLoading={isLoading}
        error={error}
        isEmpty={!isLoading && !error && !data?.html && scatter.trades.length === 0}
        loadingLabel="Building plot"
        emptyTitle="No plot for this combination"
        emptyDescription="That symbol and strategy pair has no stored backtest output."
      >
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {data?.html && (
            <Panel title="Equity curve" subtitle={`${data.symbol} · ${selectedStrategy}`} flush>
              <Box
                sx={{
                  width: '100%',
                  minHeight: 720,
                  overflow: 'hidden',
                  backgroundColor: 'surface.inset',
                }}
              >
                <iframe
                  title={`Backtest plot for ${data.symbol}`}
                  srcDoc={data.html}
                  style={{ width: '100%', height: 900, border: 'none' }}
                  sandbox="allow-scripts allow-same-origin"
                />
              </Box>
            </Panel>
          )}

          {scatter.trades.length > 0 && (
            <Panel
              title="MAE / MFE scatter"
              subtitle="X = max adverse excursion, Y = max favorable excursion (% of entry). Dots above the dashed line ran further in your favour than against you. ★ marks the latest position."
            >
              <Box sx={{ width: '100%', height: 460 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <ScatterChart margin={{ top: 16, right: 24, bottom: 40, left: 16 }}>
                    <CartesianGrid {...ct.recharts.grid} />
                    <XAxis
                      type="number"
                      dataKey="mae"
                      name="MAE"
                      unit="%"
                      domain={[0, scatter.axisMax]}
                      {...ct.recharts.axis}
                      label={{
                        value: 'MAE [%]',
                        position: 'insideBottom',
                        offset: -20,
                        fill: ct.textMuted,
                      }}
                    />
                    <YAxis
                      type="number"
                      dataKey="mfe"
                      name="MFE"
                      unit="%"
                      domain={[0, scatter.axisMax]}
                      {...ct.recharts.axis}
                      label={{
                        value: 'MFE [%]',
                        angle: -90,
                        position: 'insideLeft',
                        fill: ct.textMuted,
                      }}
                    />
                    <ZAxis type="number" range={[60, 60]} />
                    <ReferenceLine
                      segment={[
                        { x: 0, y: 0 },
                        { x: scatter.axisMax, y: scatter.axisMax },
                      ]}
                      stroke={ct.axis}
                      strokeDasharray="4 4"
                      ifOverflow="hidden"
                    />
                    <Tooltip content={<MaeMfeTooltip />} cursor={{ strokeDasharray: '3 3' }} />
                    <Legend verticalAlign="top" height={28} wrapperStyle={ct.recharts.legend.wrapperStyle} />
                    <Scatter name="Winners" data={scatter.winners} fill={ct.up} fillOpacity={0.8}>
                      {scatter.winners.map((t) => (
                        // Open trades read lighter so they are distinguishable from closed ones.
                        <Cell key={t.index} fill={ct.up} fillOpacity={t.is_open ? 0.5 : 0.9} />
                      ))}
                    </Scatter>
                    <Scatter name="Losers" data={scatter.losers} fill={ct.down} fillOpacity={0.8}>
                      {scatter.losers.map((t) => (
                        <Cell key={t.index} fill={ct.down} fillOpacity={t.is_open ? 0.5 : 0.9} />
                      ))}
                    </Scatter>
                    <Scatter
                      name="Latest position"
                      data={scatter.latest}
                      shape={<StarDot fill={ct.accent} outline={ct.insetBackground} />}
                    />
                  </ScatterChart>
                </ResponsiveContainer>
              </Box>
            </Panel>
          )}

          {data?.params && (
            <Panel title="Strategy parameters">
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                {Object.entries(data.params).map(([key, value]) => (
                  <Box
                    key={key}
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 0.75,
                      px: 1.25,
                      py: 0.5,
                      borderRadius: 1,
                      border: 1,
                      borderColor: 'line.subtle',
                      backgroundColor: 'surface.inset',
                    }}
                  >
                    <Typography component="span" variant="mono" sx={{ fontSize: '0.72rem', color: 'text.tertiary' }}>
                      {key}
                    </Typography>
                    <Typography
                      component="span"
                      variant="mono"
                      sx={{ fontSize: '0.72rem', color: 'primary.main', fontWeight: 600 }}
                    >
                      {formatStatValue(value)}
                    </Typography>
                  </Box>
                ))}
              </Box>
            </Panel>
          )}

          {data?.stats && (
            <Panel title="Backtest stats" flush>
              <TableContainer>
                <Table size="small">
                  <TableBody>
                    {Object.entries(data.stats).map(([key, value]) => (
                      <TableRow key={key}>
                        <TableCell sx={{ color: 'text.secondary' }}>{key}</TableCell>
                        <TableCell align="right">
                          <Typography variant="mono" sx={{ fontSize: '0.75rem' }}>
                            {formatStatValue(value)}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </Panel>
          )}
        </Box>
      </QueryState>
    </PageContainer>
  );
}
