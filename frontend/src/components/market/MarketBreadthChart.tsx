import { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Typography, ToggleButton, ToggleButtonGroup } from '@mui/material';
import { createTvWidget, LIBRARY_PATH } from '../../lib/tv';
import type { EntityId, IChartingLibraryWidget, LanguageCode, ResolutionString } from '../../lib/tv';
import {
  BREADTH_STUDIES,
  MarketBreadthStore,
  createMarketBreadthDatafeed,
  marketBreadthIndicatorsGetter,
  studyForView,
  type BreadthView,
} from '../../lib/tv/breadth';
import { fetchMarketBreadth, fetchTimeseries, getDateRange } from '../../lib/services/timeseries';
import type { MarketBreadthResponse, TimeseriesResponse } from '../../lib/services/timeseries';
import { tvOverrides } from '../../lib/tv/theme';
import { useColorMode } from '../../theme';
import { Numeric, LoadingState, ErrorState } from '../ui';

const CHART_HEIGHT = 800;
const DEFAULT_RSI_PERIOD = 14;
const MIN_RSI_PERIOD = 2;
const MAX_RSI_PERIOD = 100;
const DAILY = '1D' as ResolutionString;

export default function MarketBreadthChart() {
  const [data, setData] = useState<MarketBreadthResponse | null>(null);
  const [vnindexData, setVnindexData] = useState<TimeseriesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<BreadthView>('mcclellan');
  const [rsiPeriod, setRsiPeriod] = useState(DEFAULT_RSI_PERIOD);
  const { mode } = useColorMode();
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const widgetRef = useRef<IChartingLibraryWidget | null>(null);

  useEffect(() => {
    let cancelled = false;

    const loadData = async () => {
      try {
        setLoading(true);
        setError(null);
        const dateRange = getDateRange(365 * 10);
        const [breadthResult, vnindexResult] = await Promise.all([
          fetchMarketBreadth({ ...dateRange, rsi_period: rsiPeriod }),
          fetchTimeseries('VNINDEX', {
            interval: '1d',
            ...dateRange,
            indicators: [],
          }),
        ]);
        if (cancelled) return;
        setData(breadthResult);
        setVnindexData(vnindexResult);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load data');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void loadData();
    return () => {
      cancelled = true;
    };
  }, [rsiPeriod]);

  const store = useMemo(
    () => data && vnindexData ? new MarketBreadthStore(data, vnindexData) : null,
    [data, vnindexData],
  );

  useEffect(() => {
    if (!chartContainerRef.current || !store || loading) return;
    let disposed = false;
    let rsiStudyId: EntityId | null = null;
    const handleRsiConfigChange = (entityId: EntityId) => {
      if (!rsiStudyId || entityId !== rsiStudyId) return;
      try {
        const input = widgetRef.current
          ?.activeChart()
          .getStudyById(entityId)
          .getInputValues()
          .find(({ id }) => String(id) === 'period');
        const requestedPeriod = Number(input?.value);
        if (!Number.isFinite(requestedPeriod)) return;
        const nextPeriod = Math.min(
          MAX_RSI_PERIOD,
          Math.max(MIN_RSI_PERIOD, Math.round(requestedPeriod)),
        );
        if (nextPeriod !== rsiPeriod) setRsiPeriod(nextPeriod);
      } catch {
        // The study can disappear while a configuration event is in flight.
      }
    };

    createTvWidget({
      container: chartContainerRef.current,
      datafeed: createMarketBreadthDatafeed(store),
      library_path: LIBRARY_PATH,
      symbol: 'VNINDEX',
      interval: DAILY,
      timeframe: '60M',
      locale: 'en' as LanguageCode,
      autosize: true,
      theme: mode,
      timezone: 'Asia/Ho_Chi_Minh',
      custom_indicators_getter: marketBreadthIndicatorsGetter(store),
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
      widget.subscribe('study_properties_changed', handleRsiConfigChange);
      widget.onChartReady(() => {
        if (disposed) return;
        const chart = widget.activeChart();
        void Promise.all([
          chart.createStudy(BREADTH_STUDIES.oscillator, false, true),
          chart.createStudy(studyForView(view), false, true),
          chart.createStudy(
            BREADTH_STUDIES.rsiDistribution,
            false,
            false,
            { period: rsiPeriod },
          ),
        ]).then(([, , rsiEntityId]) => {
          rsiStudyId = rsiEntityId;
          if (disposed) return;
          const currentHeights = chart.getAllPanesHeight();
          if (currentHeights.length < 2) return;
          const totalHeight = currentHeights.reduce((sum, height) => sum + height, 0);
          const studyHeight = Math.floor(totalHeight / (currentHeights.length + 2));
          const mainHeight = totalHeight - studyHeight * (currentHeights.length - 1);
          chart.setAllPanesHeight([
            mainHeight,
            ...currentHeights.slice(1).map(() => studyHeight),
          ]);
        }).catch((cause) => {
          if (!disposed) setError(cause instanceof Error ? cause.message : 'Failed to add breadth studies');
        });
      });
    }).catch((cause) => {
      if (!disposed) setError(cause instanceof Error ? cause.message : 'Failed to create market breadth chart');
    });

    return () => {
      disposed = true;
      if (widgetRef.current) {
        widgetRef.current.unsubscribe('study_properties_changed', handleRsiConfigChange);
        try {
          widgetRef.current.remove();
        } catch {
          // Already removed by the charting library.
        }
        widgetRef.current = null;
      }
    };
  }, [loading, mode, rsiPeriod, store, view]);

  const handleViewChange = (_: React.MouseEvent<HTMLElement>, newView: BreadthView | null) => {
    if (newView) setView(newView);
  };

  if (loading) {
    return (
      <Box sx={{ height: CHART_HEIGHT, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <LoadingState label="Loading market breadth" />
      </Box>
    );
  }

  if (error) {
    return <ErrorState error={error} title="Could not load market breadth" />;
  }

  // Get latest values for display
  const latestIdx = data ? data.timestamps.length - 1 : 0;
  const latestOscillator = data?.mcclellan_oscillator[latestIdx] ?? 0;
  const latestSummation = data?.mcclellan_summation[latestIdx] ?? 0;
  const latestAdvances = data?.advances[latestIdx] ?? 0;
  const latestDeclines = data?.declines[latestIdx] ?? 0;

  // VNINDEX latest values
  const vnLatestIdx = vnindexData ? vnindexData.timestamps.length - 1 : 0;
  const vnClose = vnindexData?.timeseries.close[vnLatestIdx] ?? 0;
  const vnPrevClose = vnindexData?.timeseries.close[vnLatestIdx - 1] ?? vnClose;
  const vnChange = vnClose - vnPrevClose;
  const vnChangePercent = vnPrevClose ? ((vnChange / vnPrevClose) * 100) : 0;

  const readings = [
    { label: 'Advances', value: latestAdvances, decimals: 0, signed: false, color: 'market.long' },
    { label: 'Declines', value: latestDeclines, decimals: 0, signed: false, color: 'market.short' },
    { label: 'McClellan Osc', value: latestOscillator, decimals: 1, signed: true, color: undefined },
    { label: 'Summation', value: latestSummation, decimals: 0, signed: true, color: undefined },
  ];


  return (
    <Box>
      {/* Header: index quote, breadth readings, pane selector */}
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          mb: 1.5,
          flexWrap: 'wrap',
          gap: 2,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1.5 }}>
          <Typography variant="h5" sx={{ color: 'text.primary' }}>
            VNINDEX
          </Typography>
          <Numeric
            value={vnClose}
            sx={{ fontSize: '1.375rem', fontWeight: 600, color: vnChange >= 0 ? 'market.long' : 'market.short' }}
          />
          <Numeric value={vnChange} signed showSign sx={{ fontSize: '0.8125rem' }} />
          <Numeric value={vnChangePercent} format="percent" signed showSign sx={{ fontSize: '0.8125rem' }} />
        </Box>

        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
          {readings.map((r) => (
            <Box
              key={r.label}
              sx={{
                px: 1.5,
                py: 0.75,
                minWidth: 96,
                borderRadius: 1,
                bgcolor: 'surface.inset',
                border: 1,
                borderColor: 'line.subtle',
              }}
            >
              <Typography variant="overline2" sx={{ fontSize: '0.5625rem' }}>
                {r.label}
              </Typography>
              <Numeric
                value={r.value}
                decimals={r.decimals}
                signed={r.signed}
                sx={{ fontSize: '1rem', fontWeight: 600, color: r.color }}
              />
            </Box>
          ))}

          <ToggleButtonGroup value={view} exclusive onChange={handleViewChange} size="small" sx={{ ml: 1 }}>
            <ToggleButton value="mcclellan">Summation</ToggleButton>
            <ToggleButton value="ad_line">A/D Line</ToggleButton>
            <ToggleButton value="breadth">Adv/Dec</ToggleButton>
          </ToggleButtonGroup>
        </Box>
      </Box>

      {/* Unified Chart with Panes */}
      <Box
        ref={chartContainerRef}
        sx={{
          width: '100%',
          height: CHART_HEIGHT,
          borderRadius: 1,
          overflow: 'hidden',
          bgcolor: 'surface.inset',
          border: 1,
          borderColor: 'line.subtle',
        }}
      />

    </Box>
  );
}

