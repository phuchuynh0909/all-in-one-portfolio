import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Container,
  InputAdornment,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { Search } from '@mui/icons-material';
import { createChart, CandlestickSeries, LineSeries, HistogramSeries, ColorType } from 'lightweight-charts';
import type { IChartApi, UTCTimestamp } from 'lightweight-charts';
import { format, subDays } from 'date-fns';
import { fetchRegime, type RegimeResponse } from '../lib/services/regime';

// ── colours ──────────────────────────────────────────────────────────────────
const REGIME_COLORS: Record<number, string> = {
  2:  '#089981',  // Bullish_High_Var
  1:  '#26a69a',  // Bullish_Low_Var
  0:  '#9ca3af',  // Neutral
  [-1]: '#ef5350',  // Bearish_Low_Var
  [-2]: '#f23645',  // Bearish_High_Var
};

const yzColor = (pct: number | null): string => {
  if (pct == null) return '#9ca3af';
  if (pct > 90)  return '#f23645';
  if (pct > 75)  return '#ff9800';
  if (pct < 25)  return '#2196f3';
  return '#089981';
};

const toTs = (dateStr: string): UTCTimestamp =>
  (Date.UTC(
    Number(dateStr.slice(0, 4)),
    Number(dateStr.slice(5, 7)) - 1,
    Number(dateStr.slice(8, 10)),
  ) / 1000) as UTCTimestamp;

const CHART_OPTIONS = {
  layout: {
    background: { type: ColorType.Solid, color: '#0a0a0f' },
    textColor: '#9ca3af',
    fontFamily: "'SF Mono','Fira Code','Monaco',monospace",
  },
  grid: {
    vertLines: { color: 'rgba(99,102,241,0.05)' },
    horzLines: { color: 'rgba(99,102,241,0.05)' },
  },
  crosshair: {
    vertLine: { color: 'rgba(99,102,241,0.4)', width: 1 as const, style: 2 as const, labelBackgroundColor: '#6366f1' },
    horzLine: { color: 'rgba(99,102,241,0.4)', width: 1 as const, style: 2 as const, labelBackgroundColor: '#6366f1' },
  },
  rightPriceScale: { borderColor: 'rgba(99,102,241,0.2)' },
  timeScale:       { borderColor: 'rgba(99,102,241,0.2)', timeVisible: true, secondsVisible: false },
};

// ── panel labels ─────────────────────────────────────────────────────────────
const PANEL_LABELS = [
  { text: 'Price + KAMA',        color: '#a5b4fc' },
  { text: 'Markov-KAMA Regime',  color: '#a5b4fc' },
  { text: 'Regime Probability',  color: '#a5b4fc' },
  { text: 'YZ Vol Percentile',   color: '#a5b4fc' },
];

export default function RegimePage() {
  const [symbol, setSymbol]           = useState('VNINDEX');
  const [inputVal, setInputVal]       = useState('VNINDEX');
  const [isFocused, setIsFocused]     = useState(false);
  const [data, setData]               = useState<RegimeResponse | null>(null);
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<IChartApi | null>(null);
  const [chartReady, setChartReady]   = useState(false);

  // ── init chart ─────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || chartRef.current) return;

    const chart = createChart(containerRef.current, {
      ...CHART_OPTIONS,
      width:  containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    // stretch panes: price large, 3 indicator panes small
    setTimeout(() => {
      try {
        const panes = chart.panes();
        const factors = [5, 1, 1, 1];
        panes.forEach((p: any, i: number) => {
          if (typeof p.setStretchFactor === 'function' && i < factors.length)
            p.setStretchFactor(factors[i]);
        });
      } catch {}
    }, 100);

    chartRef.current = chart;
    setChartReady(true);

    const handleResize = () => {
      if (containerRef.current)
        chart.resize(containerRef.current.clientWidth, containerRef.current.clientHeight);
    };
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      chartRef.current = null;
      setChartReady(false);
    };
  }, []);

  // ── fetch data ─────────────────────────────────────────────────────────────
  const load = useCallback(async (sym: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchRegime(sym, {
        start_date: format(subDays(new Date(), 365 * 5), 'yyyy-MM-dd'),
        end_date:   format(new Date(), 'yyyy-MM-dd'),
      });
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch regime data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(symbol); }, [symbol, load]);

  // ── render chart ──────────────────────────────────────────────────────────
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !chartReady || !data) return;

    const { timestamps, open, high, low, close, markov_kama, ms_regime, yz_percentile } = data;
    const n = timestamps.length;

    // ── Panel 0: candlestick + KAMA ────────────────────────────────────────
    const priceSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#089981', downColor: '#f23645',
      borderUpColor: '#089981', borderDownColor: '#f23645',
      wickUpColor: '#089981', wickDownColor: '#f23645',
      priceLineVisible: false, lastValueVisible: true,
    });

    const kamaSeries = chart.addSeries(LineSeries, {
      color: '#6366f1', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false,
      title: 'KAMA',
    });

    // ── Panel 1: regime histogram ──────────────────────────────────────────
    const regimeHist = chart.addSeries(HistogramSeries, {
      color: '#9ca3af',
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
      priceScaleId: 'right',
      lastValueVisible: false,
      priceLineVisible: false,
    }, 1);

    // ── Panel 2: probabilities ─────────────────────────────────────────────
    const bullProbSeries = chart.addSeries(LineSeries, {
      color: '#089981', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false,
      title: 'Bull P (MK)',
    }, 2);

    const msProbSeries = chart.addSeries(LineSeries, {
      color: '#f23645', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false,
      title: 'Stress P (MS)',
    }, 2);

    const midLine = chart.addSeries(LineSeries, {
      color: 'rgba(156,163,175,0.4)', lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    }, 2);

    // ── Panel 3: YZ percentile ─────────────────────────────────────────────
    const yzSeries = chart.addSeries(LineSeries, {
      color: '#089981', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false,
      title: 'YZ Pct',
    }, 3);

    // reference lines at 25, 75, 90
    const makeRef = (_val: number, col: string) =>
      chart.addSeries(LineSeries, {
        color: col, lineWidth: 1, lineStyle: 2,
        priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      }, 3);
    const ref25 = makeRef(25, 'rgba(33,150,243,0.5)');
    const ref75 = makeRef(75, 'rgba(255,152,0,0.5)');
    const ref90 = makeRef(90, 'rgba(242,54,69,0.5)');

    // ── build data arrays ──────────────────────────────────────────────────
    const priceData: { time: UTCTimestamp; open: number; high: number; low: number; close: number }[] = [];
    const kamaData:  { time: UTCTimestamp; value: number; color: string }[] = [];
    const histData:  { time: UTCTimestamp; value: number; color: string }[] = [];
    const bullData:  { time: UTCTimestamp; value: number }[]   = [];
    const msData:    { time: UTCTimestamp; value: number }[]   = [];
    const midData:   { time: UTCTimestamp; value: number }[]   = [];
    const yzData:    { time: UTCTimestamp; value: number; color: string }[] = [];
    const r25: { time: UTCTimestamp; value: number }[] = [];
    const r75: { time: UTCTimestamp; value: number }[] = [];
    const r90: { time: UTCTimestamp; value: number }[] = [];

    for (let i = 0; i < n; i++) {
      const t = toTs(timestamps[i]);
      priceData.push({ time: t, open: open[i], high: high[i], low: low[i], close: close[i] });

      const k = markov_kama.kama[i];
      const rc = markov_kama.regime_code[i];
      if (k != null) kamaData.push({ time: t, value: k, color: REGIME_COLORS[rc] ?? '#9ca3af' });

      histData.push({
        time: t,
        value: rc,
        color: REGIME_COLORS[rc] ?? '#9ca3af',
      });

      const hp = markov_kama.high_var_prob[i];
      if (hp != null) bullData.push({ time: t, value: hp });

      const mp = ms_regime.regime_prob[i];
      if (mp != null) msData.push({ time: t, value: mp });

      midData.push({ time: t, value: 0.5 });

      const pct = yz_percentile.pct_rank[i];
      if (pct != null) yzData.push({ time: t, value: pct, color: yzColor(pct) });

      r25.push({ time: t, value: 25 });
      r75.push({ time: t, value: 75 });
      r90.push({ time: t, value: 90 });
    }

    priceSeries.setData(priceData);
    kamaSeries.setData(kamaData);
    regimeHist.setData(histData);
    bullProbSeries.setData(bullData);
    msProbSeries.setData(msData);
    midLine.setData(midData);
    yzSeries.setData(yzData);
    ref25.setData(r25);
    ref75.setData(r75);
    ref90.setData(r90);

    // show last 365 bars
    const ts = chart.timeScale();
    const to   = n - 1 + 20;
    const from = Math.max(0, to - 365 - 20);
    ts.setVisibleLogicalRange({ from, to });

    return () => {
      try {
        chart.removeSeries(priceSeries);
        chart.removeSeries(kamaSeries);
        chart.removeSeries(regimeHist);
        chart.removeSeries(bullProbSeries);
        chart.removeSeries(msProbSeries);
        chart.removeSeries(midLine);
        chart.removeSeries(yzSeries);
        chart.removeSeries(ref25);
        chart.removeSeries(ref75);
        chart.removeSeries(ref90);
      } catch {}
    };
  }, [data, chartReady]);

  // ── symbol input handlers ─────────────────────────────────────────────────
  const commit = () => {
    const s = inputVal.trim().toUpperCase();
    if (s) { setSymbol(s); setIsFocused(false); }
  };

  return (
    <Container maxWidth={false} sx={{ py: 2, height: '100vh', display: 'flex', flexDirection: 'column' }}>

      {/* header bar */}
      <Paper sx={{
        p: 0.5, mb: 1,
        display: 'flex', alignItems: 'center', gap: 1,
        background: 'linear-gradient(135deg,rgba(18,18,28,.98) 0%,rgba(20,20,32,.98) 100%)',
        border: '1px solid rgba(99,102,241,.25)', borderRadius: 2,
      }}>
        <Stack direction="row" spacing={1.5} alignItems="center">
          <TextField
            value={isFocused ? inputVal : symbol}
            onChange={e => setInputVal(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && commit()}
            onFocus={() => { setIsFocused(true); setInputVal(''); }}
            onBlur={() => { setIsFocused(false); if (!inputVal.trim()) setInputVal(symbol); }}
            variant="standard"
            placeholder="Symbol"
            InputProps={{
              disableUnderline: true,
              startAdornment: <InputAdornment position="start"><Search sx={{ color: '#9ca3af' }} /></InputAdornment>,
              sx: { color: '#e5e7eb', fontSize: '1rem', fontWeight: 600, px: 1, py: 0.5, minWidth: 140 },
            }}
            sx={{
              bgcolor: 'rgba(15,15,25,.85)',
              border: '1px solid rgba(99,102,241,.25)', borderRadius: 2,
            }}
          />
          <Chip label={symbol} size="small" sx={{ bgcolor: 'rgba(99,102,241,.18)', color: '#a5b4fc', fontWeight: 600 }} />
        </Stack>

        <Stack direction="row" spacing={1} sx={{ ml: 2 }}>
          {PANEL_LABELS.map(({ text, color }, i) => (
            <Chip
              key={i}
              label={`P${i}: ${text}`}
              size="small"
              variant="outlined"
              sx={{ borderColor: color, color, fontSize: '0.7rem' }}
            />
          ))}
        </Stack>

        {/* regime legend */}
        <Stack direction="row" spacing={0.5} sx={{ ml: 'auto' }}>
          {([-2, -1, 0, 1, 2] as const).map(code => (
            <Box key={code} sx={{
              width: 14, height: 14, borderRadius: '50%',
              bgcolor: REGIME_COLORS[code],
              title: String(code),
            }} />
          ))}
          <Typography variant="caption" sx={{ color: '#6b7280', ml: 0.5 }}>
            Bear→Bull
          </Typography>
        </Stack>
      </Paper>

      {/* chart area */}
      <Paper sx={{
        flex: 1, minHeight: 0, position: 'relative',
        background: '#0a0a0f',
        border: '1px solid rgba(99,102,241,.2)', borderRadius: 2, overflow: 'hidden',
      }}>
        <div ref={containerRef} style={{ width: '100%', height: '100%' }} />

        {loading && (
          <Box sx={{
            position: 'absolute', inset: 0,
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            gap: 2, bgcolor: 'rgba(10,10,15,.7)',
          }}>
            <CircularProgress size={40} sx={{ color: '#6366f1' }} />
            <Typography variant="body2" sx={{ color: '#9ca3af', fontFamily: "'SF Mono',monospace" }}>
              Computing regime models…
            </Typography>
          </Box>
        )}

        {error && (
          <Box sx={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', p: 4 }}>
            <Alert severity="error" sx={{ maxWidth: 500 }}>{error}</Alert>
          </Box>
        )}
      </Paper>
    </Container>
  );
}
