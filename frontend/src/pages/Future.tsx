import { useEffect, useRef, useState } from 'react';
import {
  Box, Typography, Paper, Stack, CircularProgress, Alert,
  Slider, TextField, Button, Collapse, IconButton, Divider, Chip, Tooltip,
  ToggleButton, ToggleButtonGroup,
} from '@mui/material';
import TuneIcon from '@mui/icons-material/Tune';
import PsychologyIcon from '@mui/icons-material/Psychology';
import ClearIcon from '@mui/icons-material/Clear';
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, ISeriesMarkersPluginApi, SeriesMarker, UTCTimestamp } from 'lightweight-charts';
import { fetchFutureOhlc, fetchRlExits } from '../lib/services/future';
import type { FutureOhlcResponse, RlTrade } from '../lib/services/future';
import BsiPanel from '../components/chart/panels/BsiPanel';
import KamaPanel from '../components/chart/panels/KamaPanel';

const SYMBOL      = 'VN30F1M';
const STORAGE_KEY = 'future_params';
const DATE_KEY    = 'future_from_date';
const AUTO_REFRESH_MS = 10_000;

interface Params {
  kappa: number;
  quantile_lookback: number;
  q_lo_pct: number;
  q_hi_pct: number;
  kama_period: number;
}

const DEFAULTS: Params = {
  kappa: 0.4,
  quantile_lookback: 17,
  q_lo_pct: 5,
  q_hi_pct: 95,
  kama_period: 10,
};

function loadParams(): Params {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch {}
  return DEFAULTS;
}

type Signal = SeriesMarker<UTCTimestamp>;

// ── Signal state machine (entries + rule exits) ───────────────────────────

function computeSignals(data: FutureOhlcResponse): Signal[] {
  const { timestamps, ohlc, indicators } = data;
  const n = timestamps.length;
  const { bsi, q_lo, q_hi, kama } = indicators;

  const signals: Signal[] = [];
  let pos = 0;
  let inUpperZone = false;
  let inLowerZone = false;
  let lastBelowPrice = NaN;

  for (let i = 0; i < n - 1; i++) {
    const b  = bsi[i];
    const lo = q_lo[i];
    const hi = q_hi[i];
    if (b === null || lo === null || hi === null) continue;

    const nextTime = (new Date(timestamps[i + 1]).getTime() / 1000) as UTCTimestamp;
    const close = ohlc.close[i];
    const k = kama[i];
    const kamaOk = k === null || Number.isNaN(k as number);

    if (pos === 1 && b < lo) {
      signals.push({ time: nextTime, position: 'aboveBar', color: '#888888', shape: 'arrowDown', text: 'Exit L' });
      pos = 0;
    }
    if (pos === -1 && b > hi) {
      signals.push({ time: nextTime, position: 'belowBar', color: '#888888', shape: 'arrowUp', text: 'Exit S' });
      pos = 0;
    }

    if (b >= hi) {
      inLowerZone = false;
      if (!inUpperZone) inUpperZone = true;
    } else if (b <= lo) {
      inUpperZone = false;
      lastBelowPrice = close;
      if (!inLowerZone) inLowerZone = true;
    } else {
      inUpperZone = false;
      inLowerZone = false;
    }

    if (pos !== 0) continue;

    if (b >= hi && inUpperZone) {
      const priceChange = close - lastBelowPrice;
      const longOk = !Number.isNaN(lastBelowPrice)
        && priceChange > 0
        && (kamaOk || close > (k as number));
      if (longOk) {
        signals.push({ time: nextTime, position: 'belowBar', color: '#26a69a', shape: 'arrowUp', text: 'Long' });
        pos = 1;
        inUpperZone = false;
      }
    }

    if (b <= lo && inLowerZone) {
      const shortOk = kamaOk || close < (k as number);
      if (shortOk) {
        signals.push({ time: nextTime, position: 'aboveBar', color: '#ef5350', shape: 'arrowDown', text: 'Short' });
        pos = -1;
        inLowerZone = false;
      }
    }
  }

  return signals;
}

// ── RL exit markers ───────────────────────────────────────────────────────

function computeRlMarkers(trades: RlTrade[]): Signal[] {
  return trades
    .map(t => {
      const pnl = t.rl_pnl_pct;
      const sign = pnl >= 0 ? '+' : '';
      return {
        time: (new Date(t.rl_exit_time).getTime() / 1000) as UTCTimestamp,
        position: (t.direction === 1 ? 'aboveBar' : 'belowBar') as 'aboveBar' | 'belowBar',
        color: '#f59e0b',
        shape: 'square' as const,
        text: `RL ${sign}${pnl.toFixed(2)}%`,
      };
    })
    .sort((a, b) => (a.time as number) - (b.time as number));
}

// ── Component ─────────────────────────────────────────────────────────────

export default function Future() {
  const chartContainerRef   = useRef<HTMLDivElement>(null);
  const chartRef            = useRef<IChartApi | null>(null);
  const candleSeriesRef     = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const ruleMarkersPluginRef = useRef<ISeriesMarkersPluginApi<any> | null>(null);
  const rlMarkersPluginRef   = useRef<ISeriesMarkersPluginApi<any> | null>(null);

  const [data, setData]                 = useState<FutureOhlcResponse | null>(null);
  const [loading, setLoading]           = useState(true);
  const [error, setError]               = useState<string | null>(null);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  const [timeframe, setTimeframe] = useState<string>('5m');
  const [fromDate, setFromDate]   = useState<string>(() => {
    const stored = localStorage.getItem(DATE_KEY);
    if (stored) return stored;
    const d = new Date();
    d.setDate(d.getDate() - 10);
    return d.toISOString().slice(0, 10);
  });

  const [draft, setDraft]         = useState<Params>(loadParams);
  const [applied, setApplied]     = useState<Params>(loadParams);
  const [panelOpen, setPanelOpen] = useState(false);

  const [refreshTick, setRefreshTick] = useState(0);

  const [showRl, setShowRl]       = useState(false);
  const [rlTrades, setRlTrades]   = useState<RlTrade[]>([]);
  const [rlLoading, setRlLoading] = useState(false);
  const [rlError, setRlError]     = useState<string | null>(null);

  // fetch OHLC whenever applied params change
  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true);
        setData(await fetchFutureOhlc(SYMBOL, { ...applied, timeframe, start_date: fromDate || undefined }));
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Fetch failed');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [applied, timeframe, fromDate, refreshTick]);

  // fetch RL exits when enabled or params change
  useEffect(() => {
    if (!showRl) return;
    const load = async () => {
      try {
        setRlLoading(true);
        setRlError(null);
        const res = await fetchRlExits(SYMBOL, { ...applied, start_date: fromDate || undefined });
        setRlTrades(res.trades);
      } catch (e) {
        setRlError(e instanceof Error ? e.message : 'RL fetch failed');
        setRlTrades([]);
      } finally {
        setRlLoading(false);
      }
    };
    load();
  }, [showRl, applied, fromDate, refreshTick]);

  // clear RL markers when disabled
  useEffect(() => {
    if (!showRl && rlMarkersPluginRef.current) {
      rlMarkersPluginRef.current.setMarkers([]);
    }
  }, [showRl]);

  // persist applied params + date filter to localStorage
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(applied));
  }, [applied]);

  useEffect(() => {
    if (fromDate) localStorage.setItem(DATE_KEY, fromDate);
    else localStorage.removeItem(DATE_KEY);
  }, [fromDate]);

  // auto-refresh every 10 s
  useEffect(() => {
    const id = setInterval(() => setRefreshTick(t => t + 1), AUTO_REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  // create chart once
  useEffect(() => {
    if (!chartContainerRef.current) return;
    const chart = createChart(chartContainerRef.current, {
      layout: { background: { color: '#1e1e1e' }, textColor: '#fff' },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.05)' },
        horzLines: { color: 'rgba(255,255,255,0.05)' },
      },
      width: chartContainerRef.current.clientWidth,
      height: 600,
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a', downColor: '#ef5350',
      borderUpColor: '#26a69a', borderDownColor: '#ef5350',
      wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    }, 0);
    candleSeriesRef.current = candleSeries;

    return () => {
      chart.remove();
      chartRef.current = candleSeriesRef.current = null;
      ruleMarkersPluginRef.current = rlMarkersPluginRef.current = null;
    };
  }, []);

  // update candle data + rule markers
  useEffect(() => {
    if (!candleSeriesRef.current || !data) return;

    candleSeriesRef.current.setData(data.timestamps.map((ts, i) => ({
      time:  (new Date(ts).getTime() / 1000) as UTCTimestamp,
      open:  data.ohlc.open[i],  high: data.ohlc.high[i],
      low:   data.ohlc.low[i],   close: data.ohlc.close[i],
    })));

    const markers = computeSignals(data);
    if (ruleMarkersPluginRef.current) {
      ruleMarkersPluginRef.current.setMarkers(markers);
    } else {
      ruleMarkersPluginRef.current = createSeriesMarkers(candleSeriesRef.current, markers);
    }

    const n = data.timestamps.length;
    chartRef.current?.timeScale().setVisibleLogicalRange({
      from: Math.max(0, n - 100),
      to: n - 1 + 20,
    });

    const times = data.timestamps.map(ts => (new Date(ts).getTime() / 1000) as UTCTimestamp);
    setHoveredIndex(times.length - 1);

    const handler = (param: any) => {
      if (!param.time) { setHoveredIndex(times.length - 1); return; }
      const idx = times.indexOf(param.time as UTCTimestamp);
      if (idx !== -1) setHoveredIndex(idx);
    };
    chartRef.current?.subscribeCrosshairMove(handler);
    return () => { chartRef.current?.unsubscribeCrosshairMove(handler); };
  }, [data]);

  // update RL markers
  useEffect(() => {
    if (!candleSeriesRef.current) return;
    const markers = showRl ? computeRlMarkers(rlTrades) : [];
    if (rlMarkersPluginRef.current) {
      rlMarkersPluginRef.current.setMarkers(markers);
    } else if (markers.length > 0) {
      rlMarkersPluginRef.current = createSeriesMarkers(candleSeriesRef.current, markers);
    }
  }, [rlTrades, showRl]);

  const sliderSx = { color: '#10a4f4', '& .MuiSlider-thumb': { width: 14, height: 14 } };

  const rlStats = rlTrades.length > 0 ? {
    n: rlTrades.length,
    total: rlTrades.reduce((s, t) => s + t.rl_pnl_pct, 0),
    wr: rlTrades.filter(t => t.rl_pnl_pct > 0).length / rlTrades.length * 100,
    ruleTotal: rlTrades.reduce((s, t) => s + t.rule_pnl_pct, 0),
  } : null;

  return (
    <Box sx={{ p: 3 }}>
      <Stack spacing={2}>
        {/* ── Header ── */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          <Typography variant="h4" sx={{ flex: 1 }}>⚡ Future — {SYMBOL} ({timeframe.toUpperCase()})</Typography>

          {/* Timeframe selector */}
          <ToggleButtonGroup
            value={timeframe}
            exclusive
            size="small"
            onChange={(_, v) => { if (v) setTimeframe(v); }}
            sx={{ '& .MuiToggleButton-root': { px: 1.5, py: 0.5, fontSize: '0.75rem', fontWeight: 600 } }}
          >
            {['5m', '15m', '30m', '1h'].map(tf => (
              <ToggleButton key={tf} value={tf}>{tf.toUpperCase()}</ToggleButton>
            ))}
          </ToggleButtonGroup>

          {/* Date filter */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <TextField
              type="date"
              size="small"
              label="From"
              value={fromDate}
              onChange={e => setFromDate(e.target.value)}
              InputLabelProps={{ shrink: true }}
              inputProps={{ max: new Date().toISOString().slice(0, 10) }}
              sx={{ width: 150 }}
            />
            {fromDate && (
              <Tooltip title="Clear date filter">
                <IconButton size="small" onClick={() => setFromDate('')}>
                  <ClearIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
          </Box>

          {/* RL toggle */}
          <Tooltip title={showRl ? 'Hide RL exits' : 'Show RL exit predictions'}>
            <span>
              <Chip
                icon={<PsychologyIcon />}
                label={rlLoading ? 'Loading…' : 'RL Exits'}
                onClick={() => setShowRl(v => !v)}
                color={showRl ? 'warning' : 'default'}
                variant={showRl ? 'filled' : 'outlined'}
                size="small"
                sx={{ fontWeight: 600 }}
              />
            </span>
          </Tooltip>

          <IconButton onClick={() => setPanelOpen(v => !v)} title="Parameters">
            <TuneIcon sx={{ color: panelOpen ? '#10a4f4' : 'inherit' }} />
          </IconButton>
        </Box>

        {/* ── RL summary bar ── */}
        {showRl && rlStats && (
          <Paper sx={{ px: 2, py: 1, bgcolor: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)' }}>
            <Stack direction="row" spacing={3} flexWrap="wrap">
              <Typography variant="caption" sx={{ color: '#f59e0b' }}>
                RL exits &nbsp;<strong>{rlStats.n}</strong> trades
              </Typography>
              <Typography variant="caption">
                RL total&nbsp;
                <strong style={{ color: rlStats.total >= 0 ? '#26a69a' : '#ef5350' }}>
                  {rlStats.total >= 0 ? '+' : ''}{rlStats.total.toFixed(2)}%
                </strong>
              </Typography>
              <Typography variant="caption">
                Rule total&nbsp;
                <strong style={{ color: rlStats.ruleTotal >= 0 ? '#26a69a' : '#ef5350' }}>
                  {rlStats.ruleTotal >= 0 ? '+' : ''}{rlStats.ruleTotal.toFixed(2)}%
                </strong>
              </Typography>
              <Typography variant="caption">
                RL win rate&nbsp;<strong>{rlStats.wr.toFixed(1)}%</strong>
              </Typography>
            </Stack>
          </Paper>
        )}
        {showRl && rlError && <Alert severity="error">{rlError}</Alert>}

        {/* ── Parameter panel ── */}
        <Collapse in={panelOpen}>
          <Paper sx={{ p: 2 }}>
            <Stack spacing={2}>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                <Box sx={{ minWidth: 220, flex: 1 }}>
                  <Typography variant="caption" color="text.secondary">κ (kappa) — Hawkes decay</Typography>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Slider sx={sliderSx} min={0.01} max={0.5} step={0.01}
                      value={draft.kappa}
                      onChange={(_, v) => setDraft(d => ({ ...d, kappa: v as number }))} />
                    <Typography variant="body2" sx={{ minWidth: 36, textAlign: 'right' }}>
                      {draft.kappa.toFixed(2)}
                    </Typography>
                  </Box>
                </Box>

                <Box sx={{ minWidth: 220, flex: 1 }}>
                  <Typography variant="caption" color="text.secondary">Quantile lookback (bars)</Typography>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Slider sx={sliderSx} min={20} max={800} step={10}
                      value={draft.quantile_lookback}
                      onChange={(_, v) => setDraft(d => ({ ...d, quantile_lookback: v as number }))} />
                    <Typography variant="body2" sx={{ minWidth: 36, textAlign: 'right' }}>
                      {draft.quantile_lookback}
                    </Typography>
                  </Box>
                </Box>

                <Box sx={{ minWidth: 180, flex: 1 }}>
                  <Typography variant="caption" color="text.secondary">Quantile band (lo% / hi%)</Typography>
                  <Box sx={{ display: 'flex', gap: 1, mt: 0.5 }}>
                    <TextField size="small" type="number" label="lo%"
                      value={draft.q_lo_pct} inputProps={{ min: 1, max: 49, step: 1 }}
                      onChange={e => setDraft(d => ({ ...d, q_lo_pct: Number(e.target.value) }))}
                      sx={{ width: 80 }} />
                    <TextField size="small" type="number" label="hi%"
                      value={draft.q_hi_pct} inputProps={{ min: 51, max: 99, step: 1 }}
                      onChange={e => setDraft(d => ({ ...d, q_hi_pct: Number(e.target.value) }))}
                      sx={{ width: 80 }} />
                  </Box>
                </Box>

                <Box sx={{ minWidth: 160, flex: 1 }}>
                  <Typography variant="caption" color="text.secondary">KAMA period</Typography>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Slider sx={sliderSx} min={2} max={50} step={1}
                      value={draft.kama_period}
                      onChange={(_, v) => setDraft(d => ({ ...d, kama_period: v as number }))} />
                    <Typography variant="body2" sx={{ minWidth: 28, textAlign: 'right' }}>
                      {draft.kama_period}
                    </Typography>
                  </Box>
                </Box>
              </Box>

              <Divider />

              <Box sx={{ display: 'flex', gap: 1, justifyContent: 'flex-end' }}>
                <Button variant="outlined" size="small" onClick={() => setDraft(DEFAULTS)}>Reset</Button>
                <Button variant="contained" size="small" disabled={loading}
                  onClick={() => setApplied({ ...draft })}>Apply</Button>
              </Box>
            </Stack>
          </Paper>
        </Collapse>

        {loading && <CircularProgress />}
        {error && <Alert severity="error">{error}</Alert>}

        {/* ── Chart ── */}
        <Paper sx={{ p: 0, position: 'relative' }}>
          <div ref={chartContainerRef} style={{ width: '100%' }} />

          {/* Legend */}
          {data && hoveredIndex !== null && hoveredIndex >= 0 && (
            <Box sx={{
              position: 'absolute', top: 12, left: 12, zIndex: 10,
              pointerEvents: 'none', display: 'flex', flexDirection: 'column', gap: 0.5,
            }}>
              <Typography variant="body2" sx={{ fontWeight: 'bold', color: '#f97316' }}>
                KAMA <span style={{ color: '#fff' }}>{data.indicators.kama[hoveredIndex]?.toFixed(2) ?? 'N/A'}</span>
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 'bold', color: '#10a4f4' }}>
                BSI <span style={{ color: '#fff' }}>{data.indicators.bsi[hoveredIndex]?.toFixed(0) ?? 'N/A'}</span>
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 'bold', color: '#26a69a' }}>
                q_lo ({applied.q_lo_pct}%) <span style={{ color: '#fff' }}>{data.indicators.q_lo[hoveredIndex]?.toFixed(0) ?? 'N/A'}</span>
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 'bold', color: '#ef5350' }}>
                q_hi ({applied.q_hi_pct}%) <span style={{ color: '#fff' }}>{data.indicators.q_hi[hoveredIndex]?.toFixed(0) ?? 'N/A'}</span>
              </Typography>
            </Box>
          )}

          {/* Marker legend */}
          <Box sx={{
            position: 'absolute', bottom: 8, left: 12, zIndex: 10,
            pointerEvents: 'none', display: 'flex', gap: 2, flexWrap: 'wrap',
          }}>
            {[
              { color: '#26a69a', label: '▲ Long entry' },
              { color: '#ef5350', label: '▼ Short entry' },
              { color: '#888888', label: '✕ Rule exit' },
              ...(showRl ? [{ color: '#f59e0b', label: '■ RL exit' }] : []),
            ].map(({ color, label }) => (
              <Typography key={label} variant="caption" sx={{ color, fontWeight: 600 }}>
                {label}
              </Typography>
            ))}
          </Box>

          {chartRef.current && data && (
            <>
              <KamaPanel chart={chartRef.current} data={data} />
              <BsiPanel  chart={chartRef.current} data={data} />
            </>
          )}
        </Paper>
      </Stack>
    </Box>
  );
}
