import { useEffect, useRef, useState } from 'react';
import { Box, Typography, ToggleButton, ToggleButtonGroup } from '@mui/material';
import { alpha } from '@mui/material/styles';
import { createChart, LineSeries, HistogramSeries, CandlestickSeries } from 'lightweight-charts';
import { fetchMarketBreadth, fetchTimeseries, formatChartTime, getDateRange } from '../../lib/services/timeseries';
import type { MarketBreadthResponse, TimeseriesResponse } from '../../lib/services/timeseries';
import { useChartTheme } from '../../theme';
import { Numeric, LoadingState, ErrorState } from '../ui';

type ChartView = 'ad_line' | 'mcclellan' | 'breadth';

function calcSMA(values: (number | null)[], period: number): (number | null)[] {
  return values.map((_, i) => {
    if (i < period - 1) return null;
    const slice = values.slice(i - period + 1, i + 1);
    if (slice.some(v => v == null)) return null;
    return (slice as number[]).reduce((a, b) => a + b, 0) / period;
  });
}

const CHART_HEIGHT = 600;
const PANE_STRETCH_FACTORS = [3, 1, 1]; // Price, McClellan, A/D or Breadth

export default function MarketBreadthChart() {
  const [data, setData] = useState<MarketBreadthResponse | null>(null);
  const [vnindexData, setVnindexData] = useState<TimeseriesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<ChartView>('mcclellan');
  const ct = useChartTheme();

  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);

  // Fetch data
  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true);
        const dateRange = getDateRange(365 * 10); // Last 2 years
        
        // Fetch both VNINDEX and market breadth data in parallel
        const [breadthResult, vnindexResult] = await Promise.all([
          fetchMarketBreadth(dateRange),
          fetchTimeseries('VNINDEX', {
            interval: '1d',
            ...dateRange,
            indicators: [],
          }),
        ]);
        
        setData(breadthResult);
        setVnindexData(vnindexResult);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load data');
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, []);

  // Helper to apply pane stretch factors
  const applyPaneHeights = () => {
    if (!chartRef.current) return;
    try {
      const panes = chartRef.current.panes();
      panes.forEach((pane: any, index: number) => {
        if (index < PANE_STRETCH_FACTORS.length && typeof pane.setStretchFactor === 'function') {
          pane.setStretchFactor(PANE_STRETCH_FACTORS[index]);
        }
      });
    } catch (e) {
      console.warn('Could not set pane stretch factors:', e);
    }
  };

  // Create unified chart with multiple panes
  useEffect(() => {
    if (!chartContainerRef.current || !vnindexData || !data || loading) return;

    // Clean up existing chart
    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const chart = createChart(chartContainerRef.current, {
      height: CHART_HEIGHT,
      width: chartContainerRef.current.clientWidth,
      ...ct.lightweightChartOptions,
      crosshair: { ...ct.lightweightChartOptions.crosshair, mode: 1 },
      timeScale: { ...ct.lightweightChartOptions.timeScale, timeVisible: true },
    });

    chartRef.current = chart;

    // ===== PANE 0: VNINDEX Price =====
    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      ...ct.candlestick,
      borderVisible: false,
      title: 'VNINDEX',
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: ct.accent,
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const candleData = vnindexData.timestamps.map((ts, i) => ({
      time: formatChartTime(ts),
      open: vnindexData.timeseries.open[i],
      high: vnindexData.timeseries.high[i],
      low: vnindexData.timeseries.low[i],
      close: vnindexData.timeseries.close[i],
    }));

    const volumeData = vnindexData.timestamps.map((ts, i) => ({
      time: formatChartTime(ts),
      value: vnindexData.timeseries.volume[i],
      color: vnindexData.timeseries.close[i] >= vnindexData.timeseries.open[i] 
        ? alpha(ct.up, 0.5)
        : alpha(ct.down, 0.5),
    }));

    candlestickSeries.setData(candleData);
    volumeSeries.setData(volumeData);

    // ===== PANE 1: McClellan Oscillator =====
    const oscillatorSeries = chart.addSeries(HistogramSeries, {
      title: 'McClellan Osc',
      priceScaleId: 'right',
    }, 1);

    const oscillatorData = data.timestamps.map((ts, i) => {
      const value = data.mcclellan_oscillator[i] ?? 0;
      return {
        time: formatChartTime(ts),
        value,
        color: value >= 0 ? alpha(ct.up, 0.8) : alpha(ct.down, 0.8),
      };
    });

    oscillatorSeries.setData(oscillatorData);

    // Zero line for McClellan
    const zeroLine = chart.addSeries(LineSeries, {
      color: alpha(ct.axis, 0.5),
      lineWidth: 1,
      lineStyle: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      priceScaleId: 'right',
    }, 1);

    zeroLine.setData(data.timestamps.map(ts => ({
      time: formatChartTime(ts),
      value: 0,
    })));

    // ===== PANE 2: Based on view selection =====
    if (view === 'ad_line') {
      // A/D Line
      const adLineSeries = chart.addSeries(LineSeries, {
        color: ct.accent,
        lineWidth: 2,
        title: 'A/D Line',
        priceScaleId: 'right',
      }, 2);

      const adLineData = data.timestamps.map((ts, i) => ({
        time: formatChartTime(ts),
        value: data.ad_line[i] ?? 0,
      }));

      adLineSeries.setData(adLineData);

      const adSMA20 = calcSMA(data.ad_line, 20);
      const adSMASeries = chart.addSeries(LineSeries, {
        color: ct.seriesColor(1),
        lineWidth: 1,
        lineStyle: 0,
        title: 'SMA 20',
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        priceScaleId: 'right',
      }, 2);
      adSMASeries.setData(
        data.timestamps
          .map((ts, i) => ({ time: formatChartTime(ts), value: adSMA20[i] }))
          .filter(d => d.value != null) as { time: any; value: number }[]
      );

    } else if (view === 'mcclellan') {
      // Summation Index
      const summationSeries = chart.addSeries(LineSeries, {
        color: ct.accent,
        lineWidth: 2,
        title: 'Summation Index',
        priceScaleId: 'right',
      }, 2);

      const summationData = data.timestamps.map((ts, i) => ({
        time: formatChartTime(ts),
        value: data.mcclellan_summation[i] ?? 0,
      }));

      summationSeries.setData(summationData);

      const summSMA20 = calcSMA(data.mcclellan_summation, 20);
      const summSMASeries = chart.addSeries(LineSeries, {
        color: ct.seriesColor(1),
        lineWidth: 1,
        lineStyle: 0,
        title: 'SMA 20',
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        priceScaleId: 'right',
      }, 2);
      summSMASeries.setData(
        data.timestamps
          .map((ts, i) => ({ time: formatChartTime(ts), value: summSMA20[i] }))
          .filter(d => d.value != null) as { time: any; value: number }[]
      );

      // Zero line for Summation
      const summationZeroLine = chart.addSeries(LineSeries, {
        color: alpha(ct.axis, 0.5),
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        priceScaleId: 'right',
      }, 2);

      summationZeroLine.setData(data.timestamps.map(ts => ({
        time: formatChartTime(ts),
        value: 0,
      })));

    } else if (view === 'breadth') {
      // Advances/Declines
      const advancesSeries = chart.addSeries(HistogramSeries, {
        color: alpha(ct.up, 0.7),
        title: 'Advances',
        priceScaleId: 'right',
      }, 2);

      const declinesSeries = chart.addSeries(HistogramSeries, {
        color: alpha(ct.down, 0.7),
        title: 'Declines',
        priceScaleId: 'right',
      }, 2);

      const advancesData = data.timestamps.map((ts, i) => ({
        time: formatChartTime(ts),
        value: data.advances[i],
        color: alpha(ct.up, 0.7),
      }));

      const declinesData = data.timestamps.map((ts, i) => ({
        time: formatChartTime(ts),
        value: -data.declines[i],
        color: alpha(ct.down, 0.7),
      }));

      advancesSeries.setData(advancesData);
      declinesSeries.setData(declinesData);
    }

    chart.timeScale().fitContent();

    // Apply pane heights
    setTimeout(() => applyPaneHeights(), 100);

    // Handle resize
    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.resize(chartContainerRef.current.clientWidth, CHART_HEIGHT);
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
    // `ct` is in the deps so the chart is rebuilt when the colour mode flips.
  }, [vnindexData, data, loading, view, ct]);

  const handleViewChange = (_: React.MouseEvent<HTMLElement>, newView: ChartView | null) => {
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

