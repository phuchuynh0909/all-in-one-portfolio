import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Box,
  Chip,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { getDailyPickHistory } from '../../lib/services/dailyPicks';
import type { DailyPickSnapshot } from '../../lib/services/dailyPicks';
import { useChartTheme } from '../../theme';
import { Panel, QueryState } from '../ui';

const HISTORY_WINDOWS = [20, 60, 120] as const;

type HistoryWindow = (typeof HISTORY_WINDOWS)[number];

function averageScore(snapshot: DailyPickSnapshot): number | null {
  if (snapshot.picks.length === 0) return null;
  return snapshot.picks.reduce((sum, pick) => sum + pick.score, 0) / snapshot.picks.length;
}

export default function DailyPicksHistory() {
  const [limit, setLimit] = useState<HistoryWindow>(60);
  const chartTheme = useChartTheme();
  const historyQuery = useQuery({
    queryKey: ['daily-picks-history', limit],
    queryFn: () => getDailyPickHistory(limit),
  });

  const snapshots = historyQuery.data?.snapshots ?? [];
  const latest = snapshots.at(-1);
  const chartData = useMemo(
    () => snapshots.map((snapshot) => ({
      date: snapshot.as_of,
      picks: snapshot.picks.length,
      candidates: snapshot.candidate_size,
      averageScore: averageScore(snapshot),
      topScore: snapshot.picks[0]?.score ?? null,
    })),
    [snapshots],
  );
  const recent = useMemo(() => [...snapshots].reverse().slice(0, 12), [snapshots]);

  return (
    <Panel
      title="Daily stock picks"
      subtitle={latest
        ? `Signal history through ${latest.as_of} · model ${latest.model_version}`
        : 'Persisted post-close rankings and candidate quality'}
      actions={
        <ToggleButtonGroup
          value={limit}
          exclusive
          size="small"
          onChange={(_, value: HistoryWindow | null) => value && setLimit(value)}
          aria-label="History window"
        >
          {HISTORY_WINDOWS.map((window) => (
            <ToggleButton key={window} value={window}>{window}D</ToggleButton>
          ))}
        </ToggleButtonGroup>
      }
      flush
    >
      <QueryState
        isLoading={historyQuery.isLoading}
        error={historyQuery.error}
        isEmpty={snapshots.length === 0}
        onRetry={() => historyQuery.refetch()}
        loadingLabel="Loading daily picks history"
        emptyTitle="No daily-pick history"
        emptyDescription="Run the daily-picks generation workflow to create the first snapshot."
      >
        <Box sx={{ p: 2 }}>
          {latest && (
            <Box
              sx={{
                display: 'grid',
                gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 2fr) minmax(280px, 1fr)' },
                gap: 2,
              }}
            >
              <Box sx={{ minWidth: 0 }}>
                <Stack direction="row" justifyContent="space-between" alignItems="baseline" sx={{ mb: 1 }}>
                  <Typography variant="overline2">Score and opportunity count</Typography>
                  <Typography variant="caption" sx={{ color: 'text.tertiary' }}>
                    {snapshots.length} session{snapshots.length === 1 ? '' : 's'}
                  </Typography>
                </Stack>
                <Box sx={{ width: '100%', height: 260 }}>
                  <ResponsiveContainer>
                    <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                      <CartesianGrid {...chartTheme.recharts.grid} />
                      <XAxis
                        dataKey="date"
                        minTickGap={28}
                        tickFormatter={(value: string) => value.slice(5)}
                        {...chartTheme.recharts.axis}
                      />
                      <YAxis yAxisId="score" domain={[0, 100]} {...chartTheme.recharts.axis} />
                      <YAxis yAxisId="count" orientation="right" allowDecimals={false} {...chartTheme.recharts.axis} />
                      <RechartsTooltip
                        contentStyle={chartTheme.recharts.tooltip.contentStyle}
                        labelStyle={chartTheme.recharts.tooltip.labelStyle}
                        itemStyle={chartTheme.recharts.tooltip.itemStyle}
                      />
                      <Bar
                        yAxisId="count"
                        dataKey="candidates"
                        name="Candidates"
                        fill={alpha(chartTheme.accent, 0.24)}
                        maxBarSize={18}
                      />
                      <Line
                        yAxisId="score"
                        type="monotone"
                        dataKey="topScore"
                        name="Top score"
                        stroke={chartTheme.up}
                        strokeWidth={2}
                        dot={false}
                        connectNulls
                      />
                      <Line
                        yAxisId="score"
                        type="monotone"
                        dataKey="averageScore"
                        name="Average pick score"
                        stroke={chartTheme.accent}
                        strokeWidth={1.5}
                        strokeDasharray="4 3"
                        dot={false}
                        connectNulls
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                </Box>
              </Box>

              <Box sx={{ minWidth: 0 }}>
                <Stack direction="row" justifyContent="space-between" alignItems="baseline" sx={{ mb: 1 }}>
                  <Typography variant="overline2">Latest · {latest.as_of}</Typography>
                  <Typography variant="caption" sx={{ color: 'text.tertiary' }}>
                    {latest.picks.length}/{latest.candidate_size} selected
                  </Typography>
                </Stack>
                <Stack spacing={0.75}>
                  {latest.picks.map((pick) => (
                    <Box
                      key={pick.symbol}
                      sx={{
                        display: 'grid',
                        gridTemplateColumns: '30px minmax(48px, 1fr) auto auto',
                        alignItems: 'center',
                        gap: 1,
                        px: 1,
                        py: 0.65,
                        border: 1,
                        borderColor: 'line.subtle',
                        borderRadius: 1,
                        bgcolor: 'surface.inset',
                      }}
                    >
                      <Typography variant="mono" sx={{ color: 'text.tertiary' }}>#{pick.rank}</Typography>
                      <Typography variant="mono" sx={{ fontWeight: 700 }}>{pick.symbol}</Typography>
                      <Typography variant="mono" sx={{ color: 'market.long', fontWeight: 600 }}>
                        {pick.score.toFixed(1)}
                      </Typography>
                      <Typography variant="caption" sx={{ color: 'text.tertiary' }}>
                        {(pick.model_probability * 100).toFixed(0)}%
                      </Typography>
                    </Box>
                  ))}
                </Stack>
              </Box>
            </Box>
          )}
        </Box>

        <Box sx={{ borderTop: 1, borderColor: 'line.subtle' }}>
          {recent.map((snapshot) => (
            <Box
              key={`${snapshot.as_of}-${snapshot.generated_at}`}
              sx={{
                display: 'grid',
                gridTemplateColumns: { xs: '92px minmax(0, 1fr)', md: '110px 110px minmax(0, 1fr)' },
                gap: 1.5,
                alignItems: 'center',
                px: 2,
                py: 1,
                borderBottom: 1,
                borderColor: 'line.subtle',
                '&:last-of-type': { borderBottom: 0 },
              }}
            >
              <Typography variant="mono" sx={{ color: 'text.secondary' }}>{snapshot.as_of}</Typography>
              <Typography
                variant="caption"
                sx={{ color: 'text.tertiary', display: { xs: 'none', md: 'block' } }}
              >
                {snapshot.picks.length}/{snapshot.candidate_size} picks
              </Typography>
              <Stack direction="row" spacing={0.75} useFlexGap flexWrap="wrap" sx={{ minWidth: 0 }}>
                {snapshot.picks.length === 0 ? (
                  <Typography variant="caption" sx={{ color: 'text.disabled' }}>No qualifying signals</Typography>
                ) : snapshot.picks.map((pick) => (
                  <Chip
                    key={pick.symbol}
                    size="small"
                    variant="outlined"
                    label={`${pick.rank} ${pick.symbol} · ${pick.score.toFixed(1)}`}
                    sx={{
                      height: 24,
                      fontFamily: 'monospace',
                      borderColor: alpha(chartTheme.up, 0.4),
                      bgcolor: alpha(chartTheme.up, 0.06),
                    }}
                  />
                ))}
              </Stack>
            </Box>
          ))}
        </Box>
      </QueryState>
    </Panel>
  );
}
