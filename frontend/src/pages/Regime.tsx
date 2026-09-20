import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { format, subDays } from 'date-fns';
import { fetchRegime, type RegimeResponse } from '../lib/services/regime';
import { createTvWidget, LIBRARY_PATH } from '../lib/tv';
import type { IChartingLibraryWidget, LanguageCode, ResolutionString } from '../lib/tv';
import {
  createRegimeDatafeed,
  REGIME_COLORS,
  REGIME_STUDIES,
  RegimeStore,
  regimeIndicatorsGetter,
  TICA_LABEL_COLORS,
} from '../lib/tv/regime';
import { tvOverrides } from '../lib/tv/theme';
import { useColorMode } from '../theme';

const DAILY = '1D' as ResolutionString;

// ── panel labels ─────────────────────────────────────────────────────────────
const PANEL_LABELS = [
  { text: 'Price + KAMA', accent: false },
  { text: 'Markov-KAMA Regime', accent: false },
  { text: 'Regime Probability', accent: false },
  { text: 'YZ Vol Percentile', accent: false },
  { text: 'TICA+HMM Regime', accent: true },
];

export default function RegimePage() {
  const { mode } = useColorMode();
  const [symbol, setSymbol]           = useState('VNINDEX');
  const [inputVal, setInputVal]       = useState('VNINDEX');
  const [isFocused, setIsFocused]     = useState(false);
  const [data, setData]               = useState<RegimeResponse | null>(null);
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const widgetRef = useRef<IChartingLibraryWidget | null>(null);

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

  const store = useMemo(() => data ? new RegimeStore(data) : null, [data]);

  // ── render advanced TradingView chart ─────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || !store || loading) return;
    let disposed = false;

    createTvWidget({
      container: containerRef.current,
      datafeed: createRegimeDatafeed(store),
      library_path: LIBRARY_PATH,
      symbol: store.data.symbol,
      interval: DAILY,
      timeframe: '12M',
      locale: 'en' as LanguageCode,
      autosize: true,
      theme: mode,
      timezone: 'Asia/Ho_Chi_Minh',
      custom_indicators_getter: regimeIndicatorsGetter(store),
      disabled_features: ['header_symbol_search', 'symbol_search_hot_key'],
      overrides: {
        ...tvOverrides(mode),
        'mainSeriesProperties.candleStyle.borderVisible': false,
      },
    }).then((widget) => {
      if (disposed) {
        widget.remove();
        return;
      }
      widgetRef.current = widget;
      widget.onChartReady(() => {
        if (disposed) return;
        const chart = widget.activeChart();
        void Promise.all([
          chart.createStudy(REGIME_STUDIES.kama, true, false),
          chart.createStudy(REGIME_STUDIES.markov, false, false),
          chart.createStudy(REGIME_STUDIES.probabilities, false, false),
          chart.createStudy(REGIME_STUDIES.yzPercentile, false, false),
          chart.createStudy(REGIME_STUDIES.ticaHmm, false, false),
        ]).then(() => {
          if (disposed) return;
          const heights = chart.getAllPanesHeight();
          if (heights.length !== 5) return;
          const total = heights.reduce((sum, height) => sum + height, 0);
          const indicatorHeight = Math.max(70, Math.floor(total / 9));
          chart.setAllPanesHeight([
            total - indicatorHeight * 4,
            indicatorHeight,
            indicatorHeight,
            indicatorHeight,
            indicatorHeight,
          ]);
        }).catch((cause) => {
          if (!disposed) setError(cause instanceof Error ? cause.message : 'Failed to add regime studies');
        });
      });
    }).catch((cause) => {
      if (!disposed) setError(cause instanceof Error ? cause.message : 'Failed to create regime chart');
    });

    return () => {
      disposed = true;
      if (widgetRef.current) {
        try { widgetRef.current.remove(); } catch { /* Already removed. */ }
        widgetRef.current = null;
      }
    };
  }, [loading, mode, store]);

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
