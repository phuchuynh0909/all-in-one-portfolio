import { useEffect, useRef, useState } from 'react';
import { Box, CircularProgress, Typography, ToggleButton, ToggleButtonGroup } from '@mui/material';
import { createChart, LineSeries, HistogramSeries, CandlestickSeries } from 'lightweight-charts';
import { fetchMarketBreadth, fetchTimeseries, formatChartTime, getDateRange } from '../../lib/services/timeseries';
import type { MarketBreadthResponse, TimeseriesResponse } from '../../lib/services/timeseries';

type ChartView = 'ad_line' | 'mcclellan' | 'breadth';

const CHART_HEIGHT = 600;
const PANE_STRETCH_FACTORS = [3, 1, 1]; // Price, McClellan, A/D or Breadth

export default function MarketBreadthChart() {
  const [data, setData] = useState<MarketBreadthResponse | null>(null);
  const [vnindexData, setVnindexData] = useState<TimeseriesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<ChartView>('mcclellan');

  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);

  // Fetch data
  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true);
        const dateRange = getDateRange(365 * 5); // Last 2 years
        
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
      layout: {
        background: { color: '#0a0a0f' },
        textColor: '#9ca3af',
        fontFamily: "'SF Mono', 'Fira Code', 'Monaco', monospace",
      },
      grid: {
        vertLines: { color: 'rgba(99, 102, 241, 0.05)' },
        horzLines: { color: 'rgba(99, 102, 241, 0.05)' },
      },
      crosshair: {
        mode: 1,
        vertLine: {
          color: 'rgba(99, 102, 241, 0.4)',
          labelBackgroundColor: '#6366f1',
        },
        horzLine: {
          color: 'rgba(99, 102, 241, 0.4)',
          labelBackgroundColor: '#6366f1',
        },
      },
      rightPriceScale: {
        borderColor: 'rgba(99, 102, 241, 0.2)',
      },
      timeScale: {
        borderColor: 'rgba(99, 102, 241, 0.2)',
        timeVisible: true,
      },
    });

    chartRef.current = chart;

    // ===== PANE 0: VNINDEX Price =====
    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
      title: 'VNINDEX',
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: '#6366f1',
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
        ? 'rgba(34, 197, 94, 0.5)' 
        : 'rgba(239, 68, 68, 0.5)',
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
        color: value >= 0 ? 'rgba(34, 197, 94, 0.8)' : 'rgba(239, 68, 68, 0.8)',
      };
    });

    oscillatorSeries.setData(oscillatorData);

    // Zero line for McClellan
    const zeroLine = chart.addSeries(LineSeries, {
      color: 'rgba(156, 163, 175, 0.4)',
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
        color: '#6366f1',
        lineWidth: 2,
        title: 'A/D Line',
        priceScaleId: 'right',
      }, 2);

      const adLineData = data.timestamps.map((ts, i) => ({
        time: formatChartTime(ts),
        value: data.ad_line[i] ?? 0,
      }));

      adLineSeries.setData(adLineData);

    } else if (view === 'mcclellan') {
      // Summation Index
      const summationSeries = chart.addSeries(LineSeries, {
        color: '#f59e0b',
        lineWidth: 2,
        title: 'Summation Index',
        priceScaleId: 'right',
      }, 2);

      const summationData = data.timestamps.map((ts, i) => ({
        time: formatChartTime(ts),
        value: data.mcclellan_summation[i] ?? 0,
      }));

      summationSeries.setData(summationData);

      // Zero line for Summation
      const summationZeroLine = chart.addSeries(LineSeries, {
        color: 'rgba(156, 163, 175, 0.4)',
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
        color: 'rgba(34, 197, 94, 0.7)',
        title: 'Advances',
        priceScaleId: 'right',
      }, 2);

      const declinesSeries = chart.addSeries(HistogramSeries, {
        color: 'rgba(239, 68, 68, 0.7)',
        title: 'Declines',
        priceScaleId: 'right',
      }, 2);

      const advancesData = data.timestamps.map((ts, i) => ({
        time: formatChartTime(ts),
        value: data.advances[i],
        color: 'rgba(34, 197, 94, 0.7)',
      }));

      const declinesData = data.timestamps.map((ts, i) => ({
        time: formatChartTime(ts),
        value: -data.declines[i],
        color: 'rgba(239, 68, 68, 0.7)',
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
  }, [vnindexData, data, loading, view]);

  const handleViewChange = (_: React.MouseEvent<HTMLElement>, newView: ChartView | null) => {
    if (newView) setView(newView);
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: CHART_HEIGHT }}>
        <CircularProgress sx={{ color: '#6366f1' }} />
      </Box>
    );
  }

  if (error) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: CHART_HEIGHT }}>
        <Typography color="error">⚠️ {error}</Typography>
      </Box>
    );
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

  return (
    <Box>
      {/* Header with stats and toggle */}
      <Box sx={{ 
        display: 'flex', 
        justifyContent: 'space-between', 
        alignItems: 'center', 
        mb: 2,
        flexWrap: 'wrap',
        gap: 2,
      }}>
        {/* VNINDEX info */}
        <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 2 }}>
          <Typography variant="h5" sx={{ color: '#e2e8f0', fontWeight: 700 }}>
            VNINDEX
          </Typography>
          <Typography 
            variant="h5" 
            sx={{ 
              color: vnChange >= 0 ? '#22c55e' : '#ef4444',
              fontFamily: 'monospace',
              fontWeight: 600,
            }}
          >
            {vnClose.toFixed(2)}
          </Typography>
          <Typography 
            variant="body1" 
            sx={{ 
              color: vnChange >= 0 ? '#22c55e' : '#ef4444',
              fontFamily: 'monospace',
            }}
          >
            {vnChange >= 0 ? '+' : ''}{vnChange.toFixed(2)} ({vnChangePercent >= 0 ? '+' : ''}{vnChangePercent.toFixed(2)}%)
          </Typography>
        </Box>

        {/* Stats cards */}
        <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'center' }}>
          <Box sx={{ 
            px: 2, 
            py: 1, 
            bgcolor: 'rgba(34, 197, 94, 0.1)', 
            borderRadius: 1,
            border: '1px solid rgba(34, 197, 94, 0.2)',
          }}>
            <Typography variant="caption" sx={{ color: '#6b7280' }}>Advances</Typography>
            <Typography variant="h6" sx={{ color: '#22c55e', fontFamily: 'monospace', fontWeight: 600 }}>
              {latestAdvances}
            </Typography>
          </Box>
          <Box sx={{ 
            px: 2, 
            py: 1, 
            bgcolor: 'rgba(239, 68, 68, 0.1)', 
            borderRadius: 1,
            border: '1px solid rgba(239, 68, 68, 0.2)',
          }}>
            <Typography variant="caption" sx={{ color: '#6b7280' }}>Declines</Typography>
            <Typography variant="h6" sx={{ color: '#ef4444', fontFamily: 'monospace', fontWeight: 600 }}>
              {latestDeclines}
            </Typography>
          </Box>
          <Box sx={{ 
            px: 2, 
            py: 1, 
            bgcolor: 'rgba(99, 102, 241, 0.1)', 
            borderRadius: 1,
            border: '1px solid rgba(99, 102, 241, 0.2)',
          }}>
            <Typography variant="caption" sx={{ color: '#6b7280' }}>McClellan Osc</Typography>
            <Typography 
              variant="h6" 
              sx={{ 
                color: latestOscillator >= 0 ? '#22c55e' : '#ef4444',
                fontFamily: 'monospace',
                fontWeight: 600,
              }}
            >
              {latestOscillator?.toFixed(1)}
            </Typography>
          </Box>
          <Box sx={{ 
            px: 2, 
            py: 1, 
            bgcolor: 'rgba(245, 158, 11, 0.1)', 
            borderRadius: 1,
            border: '1px solid rgba(245, 158, 11, 0.2)',
          }}>
            <Typography variant="caption" sx={{ color: '#6b7280' }}>Summation Index</Typography>
            <Typography 
              variant="h6" 
              sx={{ 
                color: latestSummation >= 0 ? '#22c55e' : '#ef4444',
                fontFamily: 'monospace',
                fontWeight: 600,
              }}
            >
              {latestSummation?.toFixed(0)}
            </Typography>
          </Box>

          <ToggleButtonGroup
            value={view}
            exclusive
            onChange={handleViewChange}
            size="small"
            sx={{
              ml: 2,
              '& .MuiToggleButton-root': {
                color: '#9ca3af',
                borderColor: 'rgba(99, 102, 241, 0.3)',
                '&.Mui-selected': {
                  color: '#fff',
                  bgcolor: 'rgba(99, 102, 241, 0.3)',
                },
                '&:hover': {
                  bgcolor: 'rgba(99, 102, 241, 0.1)',
                },
              },
            }}
          >
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
          borderRadius: 2,
          overflow: 'hidden',
          bgcolor: '#0a0a0f',
          border: '1px solid rgba(99, 102, 241, 0.2)',
        }}
      />
    </Box>
  );
}

