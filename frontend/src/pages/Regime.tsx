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
import { alpha } from '@mui/material/styles';
import { primitives } from '../theme/tokens';
import { useChartTheme } from '../theme';

// ── colours ──────────────────────────────────────────────────────────────────
// Regime identities are categorical, so they come from the token primitives
// and stay stable across colour modes — a regime should not change hue when
// the theme flips. Only the chart chrome follows the mode.
const REGIME_COLORS: Record<number, string> = {
  2: primitives.green[600],   // Bullish_High_Var
  1: primitives.teal[500],    // Bullish_Low_Var
  0: primitives.neutral[400], // Neutral
  [-1]: primitives.red[400],  // Bearish_Low_Var
  [-2]: primitives.red[600],  // Bearish_High_Var
};
const REGIME_FALLBACK = primitives.neutral[400];

// TICA+HMM regime label → colour, ramped from calm to crisis.
const TICA_LABEL_COLORS: Record<string, string> = {
  'Risk-On': primitives.green[500],
  Caution: primitives.amber[500],
  'Risk-Off': primitives.orange[600],
  Crisis: primitives.red[700],
};

/** Yang-Zhang volatility percentile → severity colour. */
const yzColor = (pct: number | null): string => {
  if (pct == null) return primitives.neutral[400];
  if (pct > 90) return primitives.red[500];
  if (pct > 75) return primitives.orange[500];
  if (pct < 25) return primitives.blue[500];
  return primitives.green[500];
};

const toTs = (dateStr: string): UTCTimestamp =>
  (Date.UTC(
    Number(dateStr.slice(0, 4)),
    Number(dateStr.slice(5, 7)) - 1,
    Number(dateStr.slice(8, 10)),
  ) / 1000) as UTCTimestamp;

type ChartTheme = ReturnType<typeof useChartTheme>;

const chartOptions = (ct: ChartTheme) => ({
  ...ct.lightweightChartOptions,
  layout: {
    ...ct.lightweightChartOptions.layout,
    background: { type: ColorType.Solid, color: ct.insetBackground },
  },
  timeScale: { ...ct.lightweightChartOptions.timeScale, timeVisible: true, secondsVisible: false },
});

// ── panel labels ─────────────────────────────────────────────────────────────
const PANEL_LABELS = [
  { text: 'Price + KAMA', accent: false },
  { text: 'Markov-KAMA Regime', accent: false },
  { text: 'Regime Probability', accent: false },
  { text: 'YZ Vol Percentile', accent: false },
  { text: 'TICA+HMM Regime', accent: true },
];

export default function RegimePage() {
  const ct = useChartTheme();
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
      ...chartOptions(ct),
      width:  containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    // stretch panes: price large, 4 indicator panes small
    setTimeout(() => {
      try {
        const panes = chart.panes();
        const factors = [5, 1, 1, 1, 1];
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

    const { timestamps, open, high, low, close, markov_kama, ms_regime, yz_percentile, tica_hmm } = data;
    const n = timestamps.length;

    // ── Panel 0: candlestick + KAMA ────────────────────────────────────────
    const priceSeries = chart.addSeries(CandlestickSeries, {
      ...ct.candlestick,
      priceLineVisible: false, lastValueVisible: true,
    });

    const kamaSeries = chart.addSeries(LineSeries, {
      color: 'var(--color-accent)', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false,
      title: 'KAMA',
    });

    // ── Panel 1: regime histogram ──────────────────────────────────────────
    const regimeHist = chart.addSeries(HistogramSeries, {
      color: 'var(--color-text-secondary)',
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
      priceScaleId: 'right',
      lastValueVisible: false,
      priceLineVisible: false,
    }, 1);

    // ── Panel 2: probabilities ─────────────────────────────────────────────
    const bullProbSeries = chart.addSeries(LineSeries, {
      color: ct.up, lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false,
      title: 'Bull P (MK)',
    }, 2);

    const msProbSeries = chart.addSeries(LineSeries, {
      color: ct.down, lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false,
      title: 'Stress P (MS)',
    }, 2);

    const midLine = chart.addSeries(LineSeries, {
      color: alpha(ct.axis, 0.4), lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    }, 2);

    // ── Panel 3: YZ percentile ─────────────────────────────────────────────
    const yzSeries = chart.addSeries(LineSeries, {
      color: ct.up, lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false,
      title: 'YZ Pct',
    }, 3);

    // ── Panel 4: TICA+HMM regime (coloured bars) ──────────────────────────
    const ticaRegimeHist = chart.addSeries(HistogramSeries, {
      color: TICA_LABEL_COLORS['Risk-On'],
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
      priceScaleId: 'right',
      lastValueVisible: false,
      priceLineVisible: false,
    }, 4);

    // reference lines at 25, 75, 90
    const makeRef = (_val: number, col: string) =>
      chart.addSeries(LineSeries, {
        color: col, lineWidth: 1, lineStyle: 2,
        priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      }, 3);
    const ref25 = makeRef(25, alpha(primitives.blue[500], 0.5));
    const ref75 = makeRef(75, alpha(primitives.orange[500], 0.5));
    const ref90 = makeRef(90, alpha(primitives.red[500], 0.5));

    // ── build data arrays ──────────────────────────────────────────────────
    const priceData:    { time: UTCTimestamp; open: number; high: number; low: number; close: number }[] = [];
    const kamaData:     { time: UTCTimestamp; value: number; color: string }[] = [];
    const histData:     { time: UTCTimestamp; value: number; color: string }[] = [];
    const bullData:     { time: UTCTimestamp; value: number }[] = [];
    const msData:       { time: UTCTimestamp; value: number }[] = [];
    const midData:      { time: UTCTimestamp; value: number }[] = [];
    const yzData:       { time: UTCTimestamp; value: number; color: string }[] = [];
    const r25:          { time: UTCTimestamp; value: number }[] = [];
    const r75:          { time: UTCTimestamp; value: number }[] = [];
    const r90:          { time: UTCTimestamp; value: number }[] = [];
    const ticaHistData: { time: UTCTimestamp; value: number; color: string }[] = [];

    for (let i = 0; i < n; i++) {
      const t = toTs(timestamps[i]);
      priceData.push({ time: t, open: open[i], high: high[i], low: low[i], close: close[i] });

      const k = markov_kama.kama[i];
      const rc = markov_kama.regime_code[i];
      if (k != null) kamaData.push({ time: t, value: k, color: REGIME_COLORS[rc] ?? REGIME_FALLBACK });

      histData.push({
        time: t,
        value: rc,
        color: REGIME_COLORS[rc] ?? REGIME_FALLBACK,
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

      // TICA+HMM panel — one full-height bar per bar, coloured by regime
      const lbl = tica_hmm.regime_label[i] ?? '';
      if (lbl) {
        ticaHistData.push({ time: t, value: 1, color: TICA_LABEL_COLORS[lbl] ?? REGIME_FALLBACK });
      }
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
    ticaRegimeHist.setData(ticaHistData);

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
        chart.removeSeries(ticaRegimeHist);
      } catch {}
    };
    // `ct` is a dep so the chart is rebuilt when the colour mode flips.
  }, [data, chartReady, ct]);

  // ── symbol input handlers ─────────────────────────────────────────────────
  const commit = () => {
    const s = inputVal.trim().toUpperCase();
    if (s) { setSymbol(s); setIsFocused(false); }
  };

  return (
    <Container
      maxWidth={false}
      sx={{
        py: 2,
        height: 'calc(100vh - var(--layout-app-bar-height))',
        display: 'flex',
        flexDirection: 'column',
      }}
    >

      {/* header bar */}
      <Paper sx={{
        p: 0.5, mb: 1,
        display: 'flex', alignItems: 'center', gap: 1,
        bgcolor: 'surface.default',
        border: 1, borderColor: 'line.subtle', borderRadius: 1,
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
              startAdornment: <InputAdornment position="start"><Search sx={{ color: 'var(--color-text-secondary)' }} /></InputAdornment>,
              sx: { color: 'var(--color-text-primary)', fontSize: '1rem', fontWeight: 600, px: 1, py: 0.5, minWidth: 140 },
            }}
            sx={{
              bgcolor: 'surface.inset',
              border: 1, borderColor: 'line.default', borderRadius: 1,
            }}
          />
          <Chip label={symbol} size="small" sx={{ bgcolor: 'action.selected', color: 'primary.main', fontWeight: 600 }} />
        </Stack>

        <Stack direction="row" spacing={1} sx={{ ml: 2 }}>
          {PANEL_LABELS.map(({ text, accent }, i) => (
            <Chip
              key={i}
              label={`P${i}: ${text}`}
              size="small"
              variant="outlined"
              sx={{
                borderColor: accent ? 'primary.main' : 'line.default',
                color: accent ? 'primary.main' : 'text.secondary',
                fontSize: '0.7rem',
              }}
            />
          ))}
        </Stack>

        {/* regime legend */}
        <Stack direction="row" spacing={0.5} sx={{ ml: 'auto', alignItems: 'center' }}>
          {([-2, -1, 0, 1, 2] as const).map(code => (
            <Box key={code} sx={{ width: 12, height: 12, borderRadius: '50%', bgcolor: REGIME_COLORS[code] }} />
          ))}
          <Typography variant="caption" sx={{ color: 'var(--color-text-tertiary)', mx: 0.5 }}>B→Bull</Typography>
          {(['Risk-On', 'Caution', 'Risk-Off'] as const).map(lbl => (
            <Chip
              key={lbl}
              label={lbl}
              size="small"
              sx={{ bgcolor: TICA_LABEL_COLORS[lbl] + '33', color: TICA_LABEL_COLORS[lbl], fontSize: '0.65rem', height: 18, fontWeight: 600 }}
            />
          ))}
        </Stack>
      </Paper>

      {/* chart area */}
      <Paper sx={{
        flex: 1, minHeight: 0, position: 'relative',
        background: 'var(--color-bg-inset)',
        border: 1, borderColor: 'line.subtle', borderRadius: 1, overflow: 'hidden',
      }}>
        <div ref={containerRef} style={{ width: '100%', height: '100%' }} />

        {loading && (
          <Box sx={{
            position: 'absolute', inset: 0,
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            gap: 2, bgcolor: 'surface.inset',
          }}>
            <CircularProgress size={40} />
            <Typography variant="body2" sx={{ color: 'var(--color-text-secondary)', fontFamily: 'var(--font-family-mono)' }}>
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
