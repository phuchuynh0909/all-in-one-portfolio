import { useEffect, useRef, useState } from 'react';
import {
  Box, Typography, Paper, Stack, CircularProgress, Alert,
  Slider, TextField, Button, Collapse, IconButton, Divider,
} from '@mui/material';
import TuneIcon from '@mui/icons-material/Tune';
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, ISeriesMarkersPluginApi, SeriesMarker, UTCTimestamp } from 'lightweight-charts';
import { fetchFutureOhlc } from '../lib/services/future';
import type { FutureOhlcResponse } from '../lib/services/future';
import BsiPanel from '../components/chart/panels/BsiPanel';
import KamaPanel from '../components/chart/panels/KamaPanel';

const SYMBOL = 'VN30F1M';

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

type Signal = SeriesMarker<UTCTimestamp>;

function computeSignals(data: FutureOhlcResponse): Signal[] {
  const { timestamps, ohlc, indicators } = data;
  const n = timestamps.length;
  const { bsi, q_lo, q_hi, kama } = indicators;

  const signals: Signal[] = [];
  let pos = 0;               // +1 long, -1 short, 0 flat
  let inUpperZone = false;   // BSI >= q_hi (gate for long re-entry)
  let inLowerZone = false;   // BSI <= q_lo (gate for short re-entry)
  let lastBelowPrice = NaN;  // close when BSI last exited below q_lo (for long direction filter)

  for (let i = 0; i < n - 1; i++) {
    const b  = bsi[i];
    const lo = q_lo[i];
    const hi = q_hi[i];
    if (b === null || lo === null || hi === null) continue;

    const nextTime = (new Date(timestamps[i + 1]).getTime() / 1000) as UTCTimestamp;
    const close = ohlc.close[i];
    const k = kama[i];
    const kamaOk = k === null || Number.isNaN(k as number);

    // ── LONG EXIT: BSI drops below q_lo ──────────────────────────────────
    if (pos === 1 && b < lo) {
      signals.push({ time: nextTime, position: 'aboveBar', color: '#888888', shape: 'arrowDown', text: 'Exit L' });
      pos = 0;
    }

    // ── SHORT EXIT: BSI rises above q_hi ─────────────────────────────────
    if (pos === -1 && b > hi) {
      signals.push({ time: nextTime, position: 'belowBar', color: '#888888', shape: 'arrowUp', text: 'Exit S' });
      pos = 0;
    }

    // Track zone transitions (needed even while in a position for zone gates)
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

    // ── LONG ENTRY: first bar BSI crosses above q_hi ─────────────────────
    //    direction filter: close has risen since BSI last left q_lo zone
    if (b >= hi && inUpperZone) {
      const priceChange = close - lastBelowPrice;
      const longOk = !Number.isNaN(lastBelowPrice)
        && priceChange > 0
        && (kamaOk || close > (k as number));
      if (longOk) {
        signals.push({ time: nextTime, position: 'belowBar', color: '#26a69a', shape: 'arrowUp', text: 'Long' });
        pos = 1;
        inUpperZone = false; // consume crossover
      }
    }

    // ── SHORT ENTRY: first bar BSI crosses below q_lo ────────────────────
    //    KAMA gate: close must be below KAMA
    if (b <= lo && inLowerZone) {
      const shortOk = kamaOk || close < (k as number);
      if (shortOk) {
        signals.push({ time: nextTime, position: 'aboveBar', color: '#ef5350', shape: 'arrowDown', text: 'Short' });
        pos = -1;
        inLowerZone = false; // consume crossover
      }
    }
  }

  return signals;
}

export default function Future() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef          = useRef<IChartApi | null>(null);
  const candleSeriesRef   = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markersPluginRef  = useRef<ISeriesMarkersPluginApi<any> | null>(null);

  const [data, setData]             = useState<FutureOhlcResponse | null>(null);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  // editable draft while the panel is open; committed on Apply
  const [draft, setDraft]       = useState<Params>(DEFAULTS);
  const [applied, setApplied]   = useState<Params>(DEFAULTS);
  const [panelOpen, setPanelOpen] = useState(false);

  // fetch whenever applied params change
  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true);
        const response = await fetchFutureOhlc(SYMBOL, applied);
        setData(response);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch future data');
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, [applied]);

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
      chartRef.current     = null;
      candleSeriesRef.current = null;
      markersPluginRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!candleSeriesRef.current || !data) return;

    const candleData = data.timestamps.map((ts, i) => ({
      time: (new Date(ts).getTime() / 1000) as UTCTimestamp,
      open: data.ohlc.open[i], high: data.ohlc.high[i],
      low:  data.ohlc.low[i],  close: data.ohlc.close[i],
    }));
    candleSeriesRef.current.setData(candleData);

    const markers = computeSignals(data);
    if (markersPluginRef.current) {
      markersPluginRef.current.setMarkers(markers);
    } else {
      markersPluginRef.current = createSeriesMarkers(candleSeriesRef.current, markers);
    }

    chartRef.current?.timeScale().fitContent();

    const times = data.timestamps.map(ts => (new Date(ts).getTime() / 1000) as UTCTimestamp);
    setHoveredIndex(times.length - 1);

    const crosshairHandler = (param: any) => {
      if (!param.time) { setHoveredIndex(times.length - 1); return; }
      const idx = times.indexOf(param.time as UTCTimestamp);
      if (idx !== -1) setHoveredIndex(idx);
    };

    chartRef.current?.subscribeCrosshairMove(crosshairHandler);
    return () => { chartRef.current?.unsubscribeCrosshairMove(crosshairHandler); };
  }, [data]);

  const sliderSx = { color: '#10a4f4', '& .MuiSlider-thumb': { width: 14, height: 14 } };

  return (
    <Box sx={{ p: 3 }}>
      <Stack spacing={2}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography variant="h4" sx={{ flex: 1 }}>⚡ Future — {SYMBOL} (5M)</Typography>
          <IconButton onClick={() => setPanelOpen(v => !v)} title="Parameters">
            <TuneIcon sx={{ color: panelOpen ? '#10a4f4' : 'inherit' }} />
          </IconButton>
        </Box>

        {/* ── Parameter panel ── */}
        <Collapse in={panelOpen}>
          <Paper sx={{ p: 2 }}>
            <Stack spacing={2}>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>

                {/* kappa */}
                <Box sx={{ minWidth: 220, flex: 1 }}>
                  <Typography variant="caption" color="text.secondary">
                    κ (kappa) — Hawkes decay
                  </Typography>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Slider
                      sx={sliderSx}
                      min={0.01} max={0.5} step={0.01}
                      value={draft.kappa}
                      onChange={(_, v) => setDraft(d => ({ ...d, kappa: v as number }))}
                    />
                    <Typography variant="body2" sx={{ minWidth: 36, textAlign: 'right' }}>
                      {draft.kappa.toFixed(2)}
                    </Typography>
                  </Box>
                </Box>

                {/* quantile_lookback */}
                <Box sx={{ minWidth: 220, flex: 1 }}>
                  <Typography variant="caption" color="text.secondary">
                    Quantile lookback (bars)
                  </Typography>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Slider
                      sx={sliderSx}
                      min={20} max={800} step={10}
                      value={draft.quantile_lookback}
                      onChange={(_, v) => setDraft(d => ({ ...d, quantile_lookback: v as number }))}
                    />
                    <Typography variant="body2" sx={{ minWidth: 36, textAlign: 'right' }}>
                      {draft.quantile_lookback}
                    </Typography>
                  </Box>
                </Box>

                {/* q_lo / q_hi */}
                <Box sx={{ minWidth: 180, flex: 1 }}>
                  <Typography variant="caption" color="text.secondary">
                    Quantile band (lo% / hi%)
                  </Typography>
                  <Box sx={{ display: 'flex', gap: 1, mt: 0.5 }}>
                    <TextField
                      size="small" type="number" label="lo%"
                      value={draft.q_lo_pct}
                      inputProps={{ min: 1, max: 49, step: 1 }}
                      onChange={e => setDraft(d => ({ ...d, q_lo_pct: Number(e.target.value) }))}
                      sx={{ width: 80 }}
                    />
                    <TextField
                      size="small" type="number" label="hi%"
                      value={draft.q_hi_pct}
                      inputProps={{ min: 51, max: 99, step: 1 }}
                      onChange={e => setDraft(d => ({ ...d, q_hi_pct: Number(e.target.value) }))}
                      sx={{ width: 80 }}
                    />
                  </Box>
                </Box>

                {/* kama_period */}
                <Box sx={{ minWidth: 160, flex: 1 }}>
                  <Typography variant="caption" color="text.secondary">
                    KAMA period
                  </Typography>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Slider
                      sx={sliderSx}
                      min={2} max={50} step={1}
                      value={draft.kama_period}
                      onChange={(_, v) => setDraft(d => ({ ...d, kama_period: v as number }))}
                    />
                    <Typography variant="body2" sx={{ minWidth: 28, textAlign: 'right' }}>
                      {draft.kama_period}
                    </Typography>
                  </Box>
                </Box>
              </Box>

              <Divider />

              <Box sx={{ display: 'flex', gap: 1, justifyContent: 'flex-end' }}>
                <Button
                  variant="outlined" size="small"
                  onClick={() => setDraft(DEFAULTS)}
                >
                  Reset
                </Button>
                <Button
                  variant="contained" size="small"
                  disabled={loading}
                  onClick={() => setApplied({ ...draft })}
                >
                  Apply
                </Button>
              </Box>
            </Stack>
          </Paper>
        </Collapse>

        {loading && <CircularProgress />}
        {error && <Alert severity="error">{error}</Alert>}

        <Paper sx={{ p: 0, position: 'relative' }}>
          <div ref={chartContainerRef} style={{ width: '100%' }} />
          {data && hoveredIndex !== null && hoveredIndex >= 0 && (
            <Box
              sx={{
                position: 'absolute', top: 12, left: 12, zIndex: 10,
                pointerEvents: 'none', display: 'flex', flexDirection: 'column', gap: 0.5,
              }}
            >
              <Typography variant="body2" sx={{ fontWeight: 'bold', color: '#f97316' }}>
                KAMA <span style={{ color: '#fff' }}>
                  {data.indicators.kama[hoveredIndex]?.toFixed(2) ?? 'N/A'}
                </span>
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 'bold', color: '#10a4f4' }}>
                BSI <span style={{ color: '#fff' }}>
                  {data.indicators.bsi[hoveredIndex]?.toFixed(0) ?? 'N/A'}
                </span>
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 'bold', color: '#26a69a' }}>
                q_lo ({applied.q_lo_pct}%) <span style={{ color: '#fff' }}>
                  {data.indicators.q_lo[hoveredIndex]?.toFixed(0) ?? 'N/A'}
                </span>
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 'bold', color: '#ef5350' }}>
                q_hi ({applied.q_hi_pct}%) <span style={{ color: '#fff' }}>
                  {data.indicators.q_hi[hoveredIndex]?.toFixed(0) ?? 'N/A'}
                </span>
              </Typography>
            </Box>
          )}
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
