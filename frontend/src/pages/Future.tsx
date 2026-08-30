import { useEffect, useRef, useState } from 'react';
import {
  Box, Typography, Paper, Stack, CircularProgress, Alert,
  Slider, TextField, Button, Collapse, IconButton, Divider, Chip, Tooltip,
  ToggleButton, ToggleButtonGroup,
} from '@mui/material';
import TuneIcon from '@mui/icons-material/Tune';
import PsychologyIcon from '@mui/icons-material/Psychology';
import ClearIcon from '@mui/icons-material/Clear';
import { fetchFutureOhlc, fetchRlExits } from '../lib/services/future';
import type { FutureOhlcResponse, RlTrade } from '../lib/services/future';
import { createTvWidget, LIBRARY_PATH } from '../lib/tv';
import { studyPalette, tvOverrides } from '../lib/tv/theme';
import { useColorMode } from '../theme';
import type {
  IChartingLibraryWidget,
  IExecutionLineAdapter,
  LanguageCode,
} from '../lib/tv';
import {
  futureStore,
  createFutureDatafeed,
  futureIndicatorsGetter,
  FUTURE_KAMA_STUDY,
  FUTURE_BSI_STUDY,
  TF_TO_RESOLUTION,
  barTimeMs,
} from '../lib/tv/future';

const SYMBOL      = 'VN30F1M';
const STORAGE_KEY = 'future_params';
const DATE_KEY    = 'future_from_date';

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

/** One buy/sell execution mark to draw on the price pane. */
interface Signal {
  timeSec: number;
  price: number;
  side: 'buy' | 'sell';
  color: string;
  text: string;
}

// Marks must share the bars' time convention (naive VN wall-clock → UTC), else
// the execution shape anchors to the wrong bar (or none).
const timeSec = (ts: string) => Math.floor(barTimeMs(ts) / 1000);

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

    const nextTime  = timeSec(timestamps[i + 1]);
    const nextPrice = ohlc.close[i + 1];
    const close = ohlc.close[i];
    const k = kama[i];
    const kamaOk = k === null || Number.isNaN(k as number);

    if (pos === 1 && b < lo) {
      signals.push({ timeSec: nextTime, price: nextPrice, side: 'sell', color: studyPalette.neutral, text: 'Exit L' });
      pos = 0;
    }
    if (pos === -1 && b > hi) {
      signals.push({ timeSec: nextTime, price: nextPrice, side: 'buy', color: studyPalette.neutral, text: 'Exit S' });
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
        signals.push({ timeSec: nextTime, price: nextPrice, side: 'buy', color: studyPalette.teal, text: 'Long' });
        pos = 1;
        inUpperZone = false;
      }
    }

    if (b <= lo && inLowerZone) {
      const shortOk = kamaOk || close < (k as number);
      if (shortOk) {
        signals.push({ timeSec: nextTime, price: nextPrice, side: 'sell', color: studyPalette.red, text: 'Short' });
        pos = -1;
        inLowerZone = false;
      }
    }
  }

  return signals;
}

// ── RL exit markers ───────────────────────────────────────────────────────

function computeRlMarkers(trades: RlTrade[]): Signal[] {
  return trades.map(t => {
    const pnl = t.rl_pnl_pct;
    const sign = pnl >= 0 ? '+' : '';
    return {
      timeSec: timeSec(t.rl_exit_time),
      price: t.rl_exit_price,
      // A long exit sits above the bar (a "sell" arrow), a short exit below.
      side: (t.direction === 1 ? 'sell' : 'buy') as 'buy' | 'sell',
      color: studyPalette.rsiSignal,
      text: `RL ${sign}${pnl.toFixed(2)}%`,
    };
  });
}

// ── Component ─────────────────────────────────────────────────────────────

export default function Future() {
  const { mode } = useColorMode();
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetRef    = useRef<IChartingLibraryWidget | null>(null);
  const readyRef     = useRef(false);
  const execShapesRef = useRef<IExecutionLineAdapter[]>([]);
  const redrawRef    = useRef<() => void>(() => {});
  // Signature of the currently drawn marks, so a real-time poll that changes
  // nothing doesn't tear down and recreate (which otherwise stacks duplicates).
  const marksSigRef  = useRef<string>('');

  const [data, setData]                 = useState<FutureOhlcResponse | null>(null);
  const [loading, setLoading]           = useState(true);
  const [error, setError]               = useState<string | null>(null);
  const [ready, setReady]               = useState(false);

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

  const [showRl, setShowRl]       = useState(false);
  const [rlTrades, setRlTrades]   = useState<RlTrade[]>([]);
  const [rlLoading, setRlLoading] = useState(false);
  const [rlError, setRlError]     = useState<string | null>(null);

  // fetch OHLC whenever applied params change → feed the store for the studies.
  // The same query is registered as the store's real-time fetcher, so the
  // library's `subscribeBars` polls with the current params (no page-side
  // interval + resetData churn).
  useEffect(() => {
    const query = () => fetchFutureOhlc(SYMBOL, { ...applied, timeframe, start_date: fromDate || undefined });
    futureStore.setFetcher(query);
    const load = async () => {
      try {
        setLoading(true);
        const res = await query();
        futureStore.set(res);
        setData(res);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Fetch failed');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [applied, timeframe, fromDate]);

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
  }, [showRl, applied, fromDate]);

  // persist applied params + date filter to localStorage
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(applied));
  }, [applied]);

  useEffect(() => {
    if (fromDate) localStorage.setItem(DATE_KEY, fromDate);
    else localStorage.removeItem(DATE_KEY);
  }, [fromDate]);

  // ── Draw entry / exit / RL execution marks ──────────────────────────────
  // Reads the live store (not React `data`) so real-time polls refresh marks
  // without a re-render/reload.
  const redraw = () => {
    const widget = widgetRef.current;
    if (!widget || !readyRef.current) return;

    const chartData = futureStore.data;
    if (!chartData) return;

    // Collapse exact duplicates (e.g. several still-open RL trades the backend
    // clamps to the same final timestamp) so they don't stack on one bar.
    const seen = new Set<string>();
    const marks: Signal[] = [...computeSignals(chartData), ...(showRl ? computeRlMarkers(rlTrades) : [])]
      .filter(m => {
        const key = `${m.timeSec}|${m.price}|${m.side}|${m.text}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });

    // Nothing changed since the last draw → leave the existing shapes in place.
    const sig = JSON.stringify(marks);
    if (sig === marksSigRef.current) return;
    marksSigRef.current = sig;

    execShapesRef.current.forEach(s => { try { s.remove(); } catch { /* gone */ } });
    execShapesRef.current = [];

    const chart = widget.activeChart();
    marks.forEach(m => {
      try {
        const shape = chart.createExecutionShape()
          .setTime(m.timeSec)
          .setPrice(m.price)
          .setDirection(m.side)
          .setText(m.text)
          .setTooltip(m.text)
          .setArrowColor(m.color)
          .setTextColor(m.color);
        execShapesRef.current.push(shape);
      } catch { /* time not on chart yet */ }
    });
  };
  redrawRef.current = redraw;

  // ── Create the widget once on mount ──────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;

    createTvWidget({
      container: containerRef.current,
      datafeed: createFutureDatafeed(futureStore),
      library_path: LIBRARY_PATH,
      symbol: SYMBOL,
      interval: TF_TO_RESOLUTION[timeframe],
      locale: 'en' as LanguageCode,
      autosize: true,
      theme: mode,
      timezone: 'Asia/Ho_Chi_Minh',
      custom_indicators_getter: futureIndicatorsGetter(futureStore),
      disabled_features: ['header_symbol_search', 'symbol_search_hot_key'],
      overrides: {
        ...tvOverrides(mode),
        'paneProperties.backgroundType': 'solid',
        'mainSeriesProperties.candleStyle.borderVisible': false,
      },
    }).then((widget) => {
      if (disposed) { widget.remove(); return; }
      widgetRef.current = widget;
      widget.onChartReady(() => {
        if (disposed) return;
        const chart = widget.activeChart();
        // KAMA overlays the price pane; BSI drops into its own oscillator pane.
        void chart.createStudy(FUTURE_KAMA_STUDY, false, false);
        void chart.createStudy(FUTURE_BSI_STUDY, false, false);
        // Redraw execution marks whenever bars (re)load.
        chart.onDataLoaded().subscribe(null, () => redrawRef.current());
        readyRef.current = true;
        setReady(true);
        redrawRef.current();
      });
    }).catch((e) => console.error('Failed to create Future TradingView widget:', e));

    return () => {
      disposed = true;
      readyRef.current = false;
      execShapesRef.current = [];
      if (widgetRef.current) {
        try { widgetRef.current.remove(); } catch { /* already gone */ }
        widgetRef.current = null;
      }
    };
    // Mount-only: data/timeframe handled by dedicated effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Push new data / timeframe into the chart ─────────────────────────────
  useEffect(() => {
    const widget = widgetRef.current;
    if (!ready || !widget || !data) return;
    const chart = widget.activeChart();
    const target = TF_TO_RESOLUTION[timeframe];
    try {
      if (chart.resolution() !== target) chart.setResolution(target);
      else chart.resetData();
    } catch { /* chart not ready */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, ready]);

  // ── Redraw marks when signals / RL change ────────────────────────────────
  useEffect(() => {
    if (ready) redrawRef.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rlTrades, showRl, data, ready]);

  // Real-time polls update the store in place (no React re-render); redraw the
  // marks off the fresh store data when they land.
  useEffect(() => futureStore.onUpdate(() => redrawRef.current()), []);

  const sliderSx = { color: 'primary.main', '& .MuiSlider-thumb': { width: 14, height: 14 } };

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
            <TuneIcon sx={{ color: panelOpen ? 'primary.main' : 'inherit' }} />
          </IconButton>
        </Box>

        {/* ── RL summary bar ── */}
        {showRl && rlStats && (
          <Paper sx={{ px: 2, py: 1, bgcolor: 'warning.main', borderColor: 'warning.main' }}>
            <Stack direction="row" spacing={3} flexWrap="wrap">
              <Typography variant="caption" sx={{ color: 'warning.main' }}>
                RL exits &nbsp;<strong>{rlStats.n}</strong> trades
              </Typography>
              <Typography variant="caption">
                RL total&nbsp;
                <strong style={{ color: rlStats.total >= 0 ? 'var(--color-long)' : 'var(--color-short)' }}>
                  {rlStats.total >= 0 ? '+' : ''}{rlStats.total.toFixed(2)}%
                </strong>
              </Typography>
              <Typography variant="caption">
                Rule total&nbsp;
                <strong style={{ color: rlStats.ruleTotal >= 0 ? 'var(--color-long)' : 'var(--color-short)' }}>
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
        <Paper sx={{ p: 0, position: 'relative', overflow: 'hidden' }}>
          <div ref={containerRef} style={{ width: '100%', height: 600 }} />

          {/* Marker legend */}
          <Box sx={{
            position: 'absolute', bottom: 8, left: 12, zIndex: 10,
            pointerEvents: 'none', display: 'flex', gap: 2, flexWrap: 'wrap',
          }}>
            {[
              { color: 'var(--color-long)', label: '▲ Long entry' },
              { color: 'var(--color-short)', label: '▼ Short entry' },
              { color: 'var(--color-flat)', label: '✕ Rule exit' },
              ...(showRl ? [{ color: 'var(--color-warning)', label: '■ RL exit' }] : []),
            ].map(({ color, label }) => (
              <Typography key={label} variant="caption" sx={{ color, fontWeight: 600 }}>
                {label}
              </Typography>
            ))}
          </Box>
        </Paper>
      </Stack>
    </Box>
  );
}
