import { useState } from 'react';
import { Alert, Box, Checkbox, FormControlLabel, MenuItem, Stack, TextField, Typography } from '@mui/material';
import { alpha } from '@mui/material/styles';
import { useQuery } from '@tanstack/react-query';
import { apiPost } from '../../lib/api';
import { useChartTheme } from '../../theme';
import { QueryState } from '../ui';

interface HealthRow {
  symbol: string;
  name: string | null;
  health: number | null;
  strength: number | null;
  previous_strength: number | null;
  financial_period: string | null;
  health_peers: number;
  turnover_vnd: number | null;
  components: Record<string, number>;
  excess_returns: Record<string, number | null>;
  unavailable: string | null;
}
interface HealthStrength { as_of: string | null; universe: number; rows: HealthRow[] }

const W = 760, H = 500;
const x = (score: number) => 55 + score * 6.75;
const y = (score: number) => 445 - score * 3.95;

export default function HealthStrengthChart({ level, sectorId }: { level: number; sectorId: number }) {
  const ct = useChartTheme();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['healthStrength', level, sectorId],
    queryFn: () => apiPost<HealthStrength>(`/timeseries/sector/${level}/${sectorId}/health-strength`, {}),
    staleTime: 300_000,
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [trail, setTrail] = useState(true);
  const rows = data?.rows ?? [];
  const plotted = rows.filter((row) => row.health != null && row.strength != null);
  const active = rows.find((row) => row.symbol === selected) ?? plotted[0] ?? rows[0];
  const quadrants = [
    { label: 'Strong on both', left: 60, right: 100, top: 100, bottom: 70, color: ct.up },
    { label: 'Strong price, weaker financials', left: 0, right: 60, top: 100, bottom: 70, color: ct.accent },
    { label: 'Stronger financials, weaker price', left: 60, right: 100, top: 70, bottom: 0, color: ct.accent },
    { label: 'Weak on both', left: 0, right: 60, top: 70, bottom: 0, color: ct.flat },
  ];
  return (
    <QueryState isLoading={isLoading} error={error} onRetry={() => { void refetch(); }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {plotted.length}/{rows.length} symbols plotted · {data?.as_of ?? 'Date unavailable'} · {data?.universe ?? 0} liquid stocks in the price-ranking universe.
      </Typography>
      <details>
        <summary>How scores are calculated</summary>
        <Typography variant="body2" color="text.secondary" sx={{ my: 1 }}>
          Financial quality is our peer score, not Titan Health: the mean percentile of profit/assets, equity/assets, and year-on-year revenue growth among at least 3 selected-sector companies reporting the same period. It does not measure leadership. Financial periods older than 550 days are excluded.
          Price strength ranks 1/3/6-month outperformance versus VNINDEX, weighted 40/35/25, across the app’s stock universe with average 20-session turnover ≥ VND 5 billion. A month is 21 sessions. Rank is 1–99. Boundaries are 60 and 70. The five-session trail holds the current financial score fixed.
        </Typography>
      </details>
      {plotted.length < rows.length && <Alert severity="info" sx={{ my: 1 }}>
        {rows.length - plotted.length} symbols lack a score or liquidity coverage. Select any symbol to see the reason. Financial scores require imported statements and comparable peers.
      </Alert>}
      <FormControlLabel control={<Checkbox checked={trail} onChange={(event) => setTrail(event.target.checked)} />} label="5-session price trail" />
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1fr) 290px' } }}>
        <Box sx={{ overflowX: 'auto', minWidth: 0 }}>
          <Box component="svg" viewBox={`0 0 ${W} ${H}`} aria-label="Financial quality versus price strength" sx={{ width: '100%', minWidth: 620, fontSize: 11, fontFamily: 'inherit' }}>
            {quadrants.map((q) => <g key={q.label}>
              <rect x={x(q.left)} y={y(q.top)} width={x(q.right) - x(q.left)} height={y(q.bottom) - y(q.top)} fill={alpha(q.color, 0.07)} />
              <text x={x(q.left) + 8} y={q.top === 100 ? y(100) - 12 : y(0) - 12} fill={q.color}>{q.label}</text>
            </g>)}
            {[0, 20, 40, 60, 80, 100].map((tick) => <g key={tick}>
              <line x1={x(tick)} x2={x(tick)} y1={y(0)} y2={y(100)} stroke={ct.grid} />
              <line x1={x(0)} x2={x(100)} y1={y(tick)} y2={y(tick)} stroke={ct.grid} />
              <text x={x(tick)} y={y(0) + 19} textAnchor="middle" fill={ct.textMuted}>{tick}</text>
              <text x={x(0) - 10} y={y(tick) + 4} textAnchor="end" fill={ct.textMuted}>{tick}</text>
            </g>)}
            <line x1={x(60)} x2={x(60)} y1={y(0)} y2={y(100)} stroke={ct.axis} strokeDasharray="5 5" />
            <line x1={x(0)} x2={x(100)} y1={y(70)} y2={y(70)} stroke={ct.axis} strokeDasharray="5 5" />
            {/* shortcut: labels can overlap in dense sectors; the symbol picker keeps every symbol accessible. */}
            {[...plotted.filter((row) => row !== active), ...(active && plotted.includes(active) ? [active] : [])].map((row) => <g key={row.symbol} role="button" tabIndex={0}
              aria-label={`${row.symbol}: financial quality ${row.health}, price strength ${row.strength}`} aria-pressed={row === active}
              onClick={() => setSelected(row.symbol)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(row.symbol); } }} style={{ cursor: 'pointer' }}>
              {trail && row.previous_strength != null && <g>
                <line x1={x(row.health!)} x2={x(row.health!)} y1={y(row.previous_strength)} y2={y(row.strength!)} stroke={row.strength! >= row.previous_strength ? ct.up : ct.down} strokeDasharray="4 4" />
                <circle cx={x(row.health!)} cy={y(row.previous_strength)} r={3} fill={ct.background} stroke={row.strength! >= row.previous_strength ? ct.up : ct.down} />
              </g>}
              <circle cx={x(row.health!)} cy={y(row.strength!)} r={row === active ? 9 : 6} fill={row === active ? ct.accent : ct.seriesColor(rows.indexOf(row))} stroke={ct.background} strokeWidth={2} />
              <text x={x(row.health!) + (row.health! > 90 ? -10 : 10)} textAnchor={row.health! > 90 ? 'end' : 'start'} y={y(row.strength!) + 4} fill={ct.text} stroke={ct.background} strokeWidth={3} paintOrder="stroke">{row.symbol}</text>
            </g>)}
            {!plotted.length && <text x={W / 2} y={H / 2} textAnchor="middle" fill={ct.textMuted}>No symbols have both scores. See coverage details.</text>}
            <text x={W / 2} y={H - 6} textAnchor="middle" fill={ct.textMuted}>Financial quality — peer score →</text>
            <text transform="translate(15 250) rotate(-90)" textAnchor="middle" fill={ct.textMuted}>Price strength vs VNINDEX →</text>
          </Box>
        </Box>
        {active && <Stack spacing={1.5} sx={{ p: 2, border: 1, borderColor: 'divider', borderRadius: 1, alignSelf: 'start' }}>
          <TextField select size="small" label="Symbol" value={active.symbol} onChange={(event) => setSelected(event.target.value)}>
            {rows.map((row) => <MenuItem key={row.symbol} value={row.symbol}>{row.symbol}{row.unavailable ? ' · incomplete' : ''}</MenuItem>)}
          </TextField>
          <Typography variant="h5">{active.symbol}</Typography><Typography variant="body2">{active.name}</Typography>
          {active.unavailable && <Alert severity="info">{active.unavailable}</Alert>}
          <Typography>Financial quality <strong>{active.health ?? '—'}/100</strong></Typography>
          <Typography variant="caption" color="text.secondary">Report {active.financial_period ?? 'unavailable'} · {active.health_peers} comparable peers</Typography>
          {Object.entries(active.components).map(([key, value]) => <Box key={key}>
            <Typography variant="caption">{({ profitability: 'Profitability', capital: 'Equity / assets', growth: 'Revenue growth' } as Record<string, string>)[key]} · {value.toFixed(0)}</Typography>
            <Box sx={{ height: 5, bgcolor: 'action.hover', borderRadius: 1 }}><Box sx={{ height: '100%', width: `${value}%`, bgcolor: ct.accent, borderRadius: 1 }} /></Box>
          </Box>)}
          <Typography>Price strength <strong>{active.strength ?? '—'}/99</strong></Typography>
          {active.strength != null && active.previous_strength != null && <Typography variant="body2" sx={{ color: ct.pnlColor(active.strength - active.previous_strength) }}>{active.strength - active.previous_strength >= 0 ? '+' : ''}{active.strength - active.previous_strength} rank points in 5 sessions</Typography>}
          <Stack direction="row" spacing={1}>
            {[21, 63, 126].map((window, i) => <Box key={window} sx={{ flex: 1, p: 0.5, bgcolor: 'action.hover', textAlign: 'center', borderRadius: 1 }}>
              <Typography variant="caption">{[1, 3, 6][i]}M</Typography>
              <Typography variant="body2" sx={{ color: ct.pnlColor(active.excess_returns[String(window)]) }}>{active.excess_returns[String(window)]?.toFixed(1) ?? '—'} pp</Typography>
            </Box>)}
          </Stack>
          <Typography variant="caption" color="text.secondary">20-session turnover: {active.turnover_vnd == null ? '—' : `${(active.turnover_vnd / 1e9).toFixed(1)} B VND/session`}</Typography>
        </Stack>}
      </Box>
    </QueryState>
  );
}
