import { useEffect, useRef, useState, useMemo } from 'react';
import { Box, CircularProgress, Typography } from '@mui/material';
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  BaselineSeries,
  createSeriesMarkers,
} from 'lightweight-charts';
import { alpha } from '@mui/material/styles';
import { useChartTheme } from '../../theme';
import { StatRow, StatTile } from '../ui';
import type {
  SeriesMarker,
  UTCTimestamp,
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  MouseEventParams,
  Time,
} from 'lightweight-charts';
import {
  fetchTimeseries,
  formatIndicatorData,
  formatChartTime,
  type TimeseriesResponse,
} from '../../lib/services/timeseries';

export interface BacktestTrade {
  id: number;
  symbol: string;
  size: number;
  entryTimestamp: string;
  avgEntryPrice: number;
  entryFees: number;
  exitTimestamp: string;
  avgExitPrice: number;
  exitFees: number;
  pnl: number;
  return_pct: number;
  direction: 'Long' | 'Short';
  status: 'Closed' | 'Open';
}

interface EquityPoint {
  time: UTCTimestamp;
  value: number;
}

interface BacktestStats {
  totalReturn: number;
  peakReturn: number;
  maxDrawdown: number;
  maxDrawdownDuration: number;
  winRate: number;
  totalTrades: number;
  avgTrade: number;
  bestTrade: number;
  worstTrade: number;
  sharpeRatio?: number;
}

type BacktestChartProps = {
  symbol: string;
  trades: BacktestTrade[];
  initialCash?: number;
};

// Calculate backtest statistics from trades
const calculateStats = (trades: BacktestTrade[], initialCash: number): BacktestStats => {
  if (trades.length === 0) {
    return {
      totalReturn: 0,
      peakReturn: 0,
      maxDrawdown: 0,
      maxDrawdownDuration: 0,
      winRate: 0,
      totalTrades: 0,
      avgTrade: 0,
      bestTrade: 0,
      worstTrade: 0,
    };
  }

  const sortedTrades = [...trades].sort(
    (a, b) => new Date(a.exitTimestamp).getTime() - new Date(b.exitTimestamp).getTime()
  );

  let equity = initialCash;
  let peak = initialCash;
  let maxDrawdown = 0;
  let maxDrawdownDuration = 0;
  let currentDrawdownStart = 0;

  const equityHistory: { date: Date; equity: number }[] = [];

  for (const trade of sortedTrades) {
    equity += trade.pnl;
    equityHistory.push({ date: new Date(trade.exitTimestamp), equity });

    if (equity > peak) {
      peak = equity;
      if (currentDrawdownStart > 0) {
        const duration = equityHistory.length - currentDrawdownStart;
        if (duration > maxDrawdownDuration) {
          maxDrawdownDuration = duration;
        }
        currentDrawdownStart = 0;
      }
    } else {
      const drawdown = (peak - equity) / peak;
      if (drawdown > maxDrawdown) {
        maxDrawdown = drawdown;
      }
      if (currentDrawdownStart === 0) {
        currentDrawdownStart = equityHistory.length;
      }
    }
  }

  const winningTrades = trades.filter((t) => t.pnl > 0);
  const returns = trades.map((t) => t.return_pct * 100);

  return {
    totalReturn: ((equity - initialCash) / initialCash) * 100,
    peakReturn: ((peak - initialCash) / initialCash) * 100,
    maxDrawdown: maxDrawdown * 100,
    maxDrawdownDuration,
    winRate: (winningTrades.length / trades.length) * 100,
    totalTrades: trades.length,
    avgTrade: returns.reduce((a, b) => a + b, 0) / returns.length,
    bestTrade: Math.max(...returns),
    worstTrade: Math.min(...returns),
  };
};

// Calculate equity curve from trades
const calculateEquityCurve = (
  trades: BacktestTrade[],
  initialCash: number
): EquityPoint[] => {
  if (trades.length === 0) return [];

  const sortedTrades = [...trades].sort(
    (a, b) => new Date(a.exitTimestamp).getTime() - new Date(b.exitTimestamp).getTime()
  );

  let equity = initialCash;
  const curve: EquityPoint[] = [
    {
      time: formatChartTime(sortedTrades[0].entryTimestamp),
      value: ((equity - initialCash) / initialCash) * 100,
    },
  ];

  for (const trade of sortedTrades) {
    equity += trade.pnl;
    curve.push({
      time: formatChartTime(trade.exitTimestamp),
      value: ((equity - initialCash) / initialCash) * 100,
    });
  }

  return curve;
};

export default function BacktestChart({
  symbol,
  trades,
  initialCash = 100,
}: BacktestChartProps) {
  const ct = useChartTheme();
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  // Series refs
  const equitySeriesRef = useRef<ISeriesApi<'Baseline'> | null>(null);
  const peakSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const sma10SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const sma20SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const atrTrailingSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const vwapHighestSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const vwapLowestSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Crosshair data state for OHLC display
  interface CrosshairData {
    open: number | null;
    high: number | null;
    low: number | null;
    close: number | null;
    volume: number | null;
    change: number | null;
    changePercent: number | null;
  }

  const [crosshairData, setCrosshairData] = useState<CrosshairData | null>(null);

  // Series visibility state
  type SeriesVisibilityState = {
    sma10: boolean;
    sma20: boolean;
    atrTrailing: boolean;
    vwapHighest: boolean;
    vwapLowest: boolean;
  };

  const [seriesVisibility, setSeriesVisibility] = useState<SeriesVisibilityState>({
    sma10: false,
    sma20: false,
    atrTrailing: true,
    vwapHighest: true,
    vwapLowest: true,
  });

  // Toggle series visibility
  const toggleSeries = (seriesKey: keyof SeriesVisibilityState) => {
    const seriesMap = {
      sma10: sma10SeriesRef,
      sma20: sma20SeriesRef,
      atrTrailing: atrTrailingSeriesRef,
      vwapHighest: vwapHighestSeriesRef,
      vwapLowest: vwapLowestSeriesRef,
    };

    const series = seriesMap[seriesKey]?.current;
    if (series) {
      const newVisibility = !seriesVisibility[seriesKey];
      series.applyOptions({ visible: newVisibility });
      setSeriesVisibility((prev: SeriesVisibilityState) => ({ ...prev, [seriesKey]: newVisibility }));
    }
  };

  // Calculate stats and curves
  const stats = useMemo(() => calculateStats(trades, initialCash), [trades, initialCash]);
  const equityCurve = useMemo(() => calculateEquityCurve(trades, initialCash), [trades, initialCash]);

  // Chart configuration
  // Layout: Pane 0 (Main OHLC) | Pane 1 (Equity)
  const chartConfig = {
    totalHeight: 850,
    panelHeights: {
      main: 600,    // OHLC + Volume (Pane 0)
      equity: 200,  // Equity curve (Pane 1)
    },
  };

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current || chartRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      height: chartConfig.totalHeight,
      width: chartContainerRef.current.clientWidth,
      ...ct.lightweightChartOptions,
      crosshair: { ...ct.lightweightChartOptions.crosshair, mode: 0 },
      rightPriceScale: {
        borderColor: ct.border,
        scaleMargins: { top: 0.05, bottom: 0.15 },
      },
      leftPriceScale: { visible: false, borderColor: ct.border },
      timeScale: {
        ...ct.lightweightChartOptions.timeScale,
        timeVisible: true,
        secondsVisible: false,
      },
    });

    // ═══════════════════════════════════════════════════════════════════════
    // PANE 0: Main OHLC Chart (largest panel)
    // ═══════════════════════════════════════════════════════════════════════
    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      ...ct.candlestick,
      borderVisible: false,
      priceScaleId: 'right',
      priceLineVisible: false,
    });

    // SMA lines on main chart
    const sma10Series = chart.addSeries(LineSeries, {
      color: ct.seriesColor(5),
      lineWidth: 1,
      title: 'SMA(10)',
      priceScaleId: 'right',
      crosshairMarkerVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
    });

    const sma20Series = chart.addSeries(LineSeries, {
      color: ct.seriesColor(0),
      lineWidth: 1,
      title: 'SMA(20)',
      priceScaleId: 'right',
      crosshairMarkerVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
    });

    // ATR Trailing Stop line on main chart
    const atrTrailingSeries = chart.addSeries(LineSeries, {
      color: ct.seriesColor(3),
      lineWidth: 2,
      lineStyle: 2, // Dashed
      title: 'ATR Stop',
      priceScaleId: 'right',
      crosshairMarkerVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
    });

    // VWAP Highest line (resistance)
    const vwapHighestSeries = chart.addSeries(LineSeries, {
      color: ct.seriesColor(4),
      lineWidth: 1,
      lineStyle: 2, // Dashed
      title: 'VWAP High',
      priceScaleId: 'right',
      crosshairMarkerVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
    });

    // VWAP Lowest line (support)
    const vwapLowestSeries = chart.addSeries(LineSeries, {
      color: ct.seriesColor(1),
      lineWidth: 1,
      lineStyle: 2, // Dashed
      title: 'VWAP Low',
      priceScaleId: 'right',
      crosshairMarkerVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
    });

    // Volume overlay on main chart (bottom portion)
    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: ct.accent,
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
      borderVisible: false,
    });

    // ═══════════════════════════════════════════════════════════════════════
    // PANE 1: Equity Curve (separate panel at top)
    // ═══════════════════════════════════════════════════════════════════════
    const equitySeries = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      topLineColor: ct.up,
      topFillColor1: alpha(ct.up, 0.28),
      topFillColor2: alpha(ct.up, 0.05),
      bottomLineColor: ct.down,
      bottomFillColor1: alpha(ct.down, 0.05),
      bottomFillColor2: alpha(ct.down, 0.28),
      lineWidth: 2,
      priceScaleId: 'right',
      title: 'Equity %',
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => `${price.toFixed(1)}%`,
      },
    }, 1); // Pane 1

    const peakSeries = chart.addSeries(LineSeries, {
      color: ct.seriesColor(1),
      lineWidth: 1,
      lineStyle: 2,
      priceScaleId: 'right',
      title: 'Peak',
      crosshairMarkerVisible: false,
    }, 1); // Pane 1

    // Store refs
    chartRef.current = chart;
    equitySeriesRef.current = equitySeries;
    peakSeriesRef.current = peakSeries;
    candlestickSeriesRef.current = candlestickSeries;
    volumeSeriesRef.current = volumeSeries;
    sma10SeriesRef.current = sma10Series;
    sma20SeriesRef.current = sma20Series;
    atrTrailingSeriesRef.current = atrTrailingSeries;
    vwapHighestSeriesRef.current = vwapHighestSeries;
    vwapLowestSeriesRef.current = vwapLowestSeries;

    // Subscribe to crosshair move for OHLC display
    const handleCrosshairMove = (param: MouseEventParams<Time>) => {
      if (!param.time || !param.seriesData) {
        setCrosshairData(null);
        return;
      }

      // Get candlestick data at crosshair position
      const candleData = param.seriesData.get(candlestickSeries) as {
        open: number;
        high: number;
        low: number;
        close: number;
      } | undefined;

      // Get volume data at crosshair position
      const volData = param.seriesData.get(volumeSeries) as {
        value: number;
      } | undefined;

      if (candleData) {
        const change = candleData.close - candleData.open;
        const changePercent = (change / candleData.open) * 100;

        setCrosshairData({
          open: candleData.open,
          high: candleData.high,
          low: candleData.low,
          close: candleData.close,
          volume: volData?.value ?? null,
          change,
          changePercent,
        });
      } else {
        setCrosshairData(null);
      }
    };

    chart.subscribeCrosshairMove(handleCrosshairMove);

    return () => {
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, []);

  // Re-theme in place when the colour mode flips. Rebuilding the chart here
  // would drop the loaded series data, so options are applied instead.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    chart.applyOptions({
      ...ct.lightweightChartOptions,
      crosshair: { ...ct.lightweightChartOptions.crosshair, mode: 0 },
      rightPriceScale: { borderColor: ct.border },
      leftPriceScale: { visible: false, borderColor: ct.border },
      timeScale: { ...ct.lightweightChartOptions.timeScale, timeVisible: true },
    });

    candlestickSeriesRef.current?.applyOptions(ct.candlestick);
    volumeSeriesRef.current?.applyOptions({ color: ct.accent });
    sma10SeriesRef.current?.applyOptions({ color: ct.seriesColor(5) });
    sma20SeriesRef.current?.applyOptions({ color: ct.seriesColor(0) });
    atrTrailingSeriesRef.current?.applyOptions({ color: ct.seriesColor(3) });
    vwapHighestSeriesRef.current?.applyOptions({ color: ct.seriesColor(4) });
    vwapLowestSeriesRef.current?.applyOptions({ color: ct.seriesColor(1) });
    peakSeriesRef.current?.applyOptions({ color: ct.seriesColor(1) });
    equitySeriesRef.current?.applyOptions({
      topLineColor: ct.up,
      topFillColor1: alpha(ct.up, 0.28),
      topFillColor2: alpha(ct.up, 0.05),
      bottomLineColor: ct.down,
      bottomFillColor1: alpha(ct.down, 0.05),
      bottomFillColor2: alpha(ct.down, 0.28),
    });
  }, [ct]);

  // Load data and update chart
  useEffect(() => {
    if (!symbol || !chartRef.current || trades.length === 0) return;

    const loadData = async () => {
      try {
        setLoading(true);
        setError(null);

        // Clear existing markers
        if (markersRef.current) {
          markersRef.current.setMarkers([]);
        }

        // Get date range - default to 2010 start, today end
        const today = new Date();
        let minDate = new Date('2010-01-01');
        let maxDate = today;

        // If there are trades, use trade dates to set range
        if (trades.length > 0) {
          const tradeDates = trades.flatMap((t) => [
            new Date(t.entryTimestamp),
            new Date(t.exitTimestamp),
          ]);
          const earliestTrade = new Date(Math.min(...tradeDates.map((d) => d.getTime())));
          // Extend range for context (60 days before first trade)
          minDate = new Date(earliestTrade);
          minDate.setDate(minDate.getDate() - 60);
          // Ensure minDate is not before 2010
          if (minDate < new Date('2010-01-01')) {
            minDate = new Date('2010-01-01');
          }
        }

        // Fetch OHLC data with indicators from API
        const result: TimeseriesResponse = await fetchTimeseries(symbol, {
          interval: '1d',
          start_date: minDate.toISOString().split('T')[0],
          end_date: maxDate.toISOString().split('T')[0],
          indicators: [
            { name: 'rsi', params: { timeperiod: 14 } },
            { name: 'atr_trailing', params: { timeperiod: 10 } },
            { name: 'vwap', params: { window: 200 } },
          ],
        });

        // Format OHLC data
        const candleData = result.timestamps.map((timestamp: string, i: number) => ({
          time: formatChartTime(timestamp),
          open: result.timeseries.open[i],
          high: result.timeseries.high[i],
          low: result.timeseries.low[i],
          close: result.timeseries.close[i],
        }));

        const volumeData = result.timestamps.map((timestamp: string, i: number) => ({
          time: formatChartTime(timestamp),
          value: result.timeseries.volume[i],
          color:
            result.timeseries.close[i] >= result.timeseries.open[i]
              ? alpha(ct.up, 0.5)
              : alpha(ct.down, 0.5),
        }));

        // Calculate SMAs client-side (API only supports one SMA per call)
        const sma10Data = formatIndicatorData(
          result.timestamps,
          calculateSMA(result.timeseries.close, 10)
        );
        const sma20Data = formatIndicatorData(
          result.timestamps,
          calculateSMA(result.timeseries.close, 20)
        );

        // Format ATR trailing stop from API response
        const atrTrailingData = result.indicators?.atr_trailing
          ? formatIndicatorData(result.timestamps, result.indicators.atr_trailing)
          : [];

        // Format VWAP highest/lowest from API response
        const vwapHighestData = result.indicators?.vwap_highest
          ? formatIndicatorData(result.timestamps, result.indicators.vwap_highest)
          : [];
        const vwapLowestData = result.indicators?.vwap_lowest
          ? formatIndicatorData(result.timestamps, result.indicators.vwap_lowest)
          : [];

        // Update series
        candlestickSeriesRef.current?.setData(candleData);
        volumeSeriesRef.current?.setData(volumeData);
        sma10SeriesRef.current?.setData(sma10Data);
        sma20SeriesRef.current?.setData(sma20Data);
        atrTrailingSeriesRef.current?.setData(atrTrailingData);
        vwapHighestSeriesRef.current?.setData(vwapHighestData);
        vwapLowestSeriesRef.current?.setData(vwapLowestData);

        // Update equity curve
        if (equityCurve.length > 0) {
          equitySeriesRef.current?.setData(equityCurve);

          // Calculate peak line
          let peak = 0;
          const peakData = equityCurve.map((point: EquityPoint) => {
            peak = Math.max(peak, point.value);
            return { time: point.time, value: peak };
          });
          peakSeriesRef.current?.setData(peakData);
        }

        // Create trade markers with entry/exit visualization
        const markers: SeriesMarker<UTCTimestamp>[] = trades.flatMap((trade) => {
          const entryMarker: SeriesMarker<UTCTimestamp> = {
            time: formatChartTime(trade.entryTimestamp),
            position: 'belowBar',
            color: trade.direction === 'Long' ? ct.up : ct.down,
            shape: 'arrowUp',
            text: `BUY @${trade.avgEntryPrice.toFixed(2)}`,
          };

          const exitMarker: SeriesMarker<UTCTimestamp> = {
            time: formatChartTime(trade.exitTimestamp),
            position: 'aboveBar',
            color: trade.return_pct >= 0 ? ct.up : ct.down,
            shape: 'arrowDown',
            text: `SELL ${trade.return_pct >= 0 ? '+' : ''}${(trade.return_pct * 100).toFixed(1)}%`,
          };

          return [entryMarker, exitMarker];
        });

        if (candlestickSeriesRef.current && markers.length > 0) {
          markersRef.current = createSeriesMarkers(candlestickSeriesRef.current, markers);
        }

        // Fit content to show all data
        chartRef.current?.timeScale().fitContent();
      } catch (err) {
        console.error('Error loading backtest data:', err);
        setError(err instanceof Error ? err.message : 'An error occurred');
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, [symbol, trades, equityCurve]);

  // Handle resize
  useEffect(() => {
    const handleResize = () => {
      if (chartRef.current && chartContainerRef.current) {
        chartRef.current.resize(
          chartContainerRef.current.clientWidth,
          chartConfig.totalHeight
        );
      }
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return (
    <Box sx={{ width: '100%', position: 'relative' }}>
      {/* Stats Header */}
      <Box sx={{ mb: 2 }}>
        <StatRow min={130}>
        <StatTile
            label="Total return"
            value={stats.totalReturn}
            format="percent"
            decimals={1}
            signed
            showSign
            accent={stats.totalReturn >= 0 ? 'long' : 'short'}
          />
        <StatTile label="Peak" value={stats.peakReturn} format="percent" decimals={1} />
        <StatTile
            label="Max drawdown"
            value={-Math.abs(stats.maxDrawdown)}
            format="percent"
            decimals={1}
            signed
            accent="short"
          />
        <StatTile
            label="Win rate"
            value={stats.winRate}
            format="percent"
            decimals={1}
            accent={stats.winRate >= 50 ? 'long' : 'warning'}
          />
        <StatTile label="Trades" value={stats.totalTrades} decimals={0} />
        <StatTile label="Avg trade" value={stats.avgTrade} format="percent" signed showSign />
        <StatTile label="Best trade" value={stats.bestTrade} format="percent" decimals={1} signed showSign />
        <StatTile label="Worst trade" value={stats.worstTrade} format="percent" decimals={1} signed showSign />
        </StatRow>
      </Box>

      {/* Chart Container */}
      <Box
        sx={{
          height: chartConfig.totalHeight,
          position: 'relative',
          bgcolor: 'surface.inset',
          borderRadius: 2,
          overflow: 'hidden',
          border: 1,
          borderColor: 'line.subtle',
        }}
      >
        {/* Legend */}
        <Box
          sx={{
            position: 'absolute',
            top: 8,
            left: 12,
            zIndex: 10,
            display: 'flex',
            flexDirection: 'column',
            gap: 1,
            bgcolor: 'surface.overlay',
            border: 1,
            borderColor: 'line.subtle',
            p: 1.5,
            borderRadius: 1,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
          <Typography
            sx={{
              fontSize: 18,
              fontWeight: 600,
              color: 'text.primary',
              fontFamily: 'var(--font-family-mono)',
            }}
          >
            {symbol}
          </Typography>
            
            {/* OHLC Data Display */}
            {crosshairData && (
              <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', ml: 1 }}>
                <OHLCLabel label="O" value={crosshairData.open} />
                <OHLCLabel label="H" value={crosshairData.high} color="#22c55e" />
                <OHLCLabel label="L" value={crosshairData.low} color="#ef4444" />
                <OHLCLabel label="C" value={crosshairData.close} />
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 0.5,
                    px: 1,
                    py: 0.25,
                    borderRadius: 0.5,
                    bgcolor: crosshairData.changePercent !== null && crosshairData.changePercent >= 0
                      ? 'market.longSubtle'
                      : 'market.shortSubtle',
                  }}
                >
                  <Typography
                    sx={{
                      fontSize: 12,
                      fontWeight: 600,
                      fontFamily: 'var(--font-family-mono)',
                      color: crosshairData.changePercent !== null && crosshairData.changePercent >= 0
                        ? 'market.long'
                        : 'market.short',
                    }}
                  >
                    {crosshairData.change !== null
                      ? `${crosshairData.change >= 0 ? '+' : ''}${crosshairData.change.toFixed(2)}`
                      : '-'}
                  </Typography>
                  <Typography
                    sx={{
                      fontSize: 12,
                      fontWeight: 600,
                      fontFamily: 'var(--font-family-mono)',
                      color: crosshairData.changePercent !== null && crosshairData.changePercent >= 0
                        ? 'market.long'
                        : 'market.short',
                    }}
                  >
                    ({crosshairData.changePercent !== null
                      ? `${crosshairData.changePercent >= 0 ? '+' : ''}${crosshairData.changePercent.toFixed(2)}%`
                      : '-'})
                  </Typography>
                </Box>
                {crosshairData.volume !== null && (
                  <Typography
                    sx={{
                      fontSize: 11,
                      color: 'text.tertiary',
                      fontFamily: 'var(--font-family-mono)',
                    }}
                  >
                    Vol: {formatVolume(crosshairData.volume)}
                  </Typography>
                )}
              </Box>
            )}
          </Box>
          <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
            <LegendItem
              color="#3b82f6"
              label="SMA(10)"
              visible={seriesVisibility.sma10}
              onClick={() => toggleSeries('sma10')}
            />
            <LegendItem
              color="#f59e0b"
              label="SMA(20)"
              visible={seriesVisibility.sma20}
              onClick={() => toggleSeries('sma20')}
            />
            <LegendItem
              color="#10b981"
              label="ATR Stop"
              dashed
              visible={seriesVisibility.atrTrailing}
              onClick={() => toggleSeries('atrTrailing')}
            />
            <LegendItem
              color="#f472b6"
              label="VWAP High"
              dashed
              visible={seriesVisibility.vwapHighest}
              onClick={() => toggleSeries('vwapHighest')}
            />
            <LegendItem
              color="#38bdf8"
              label="VWAP Low"
              dashed
              visible={seriesVisibility.vwapLowest}
              onClick={() => toggleSeries('vwapLowest')}
            />
          </Box>
          <Typography sx={{ fontSize: 10, color: 'text.tertiary', mt: 0.5 }}>
            Panel 1: Equity Curve
          </Typography>
        </Box>

        <div
          ref={chartContainerRef}
          style={{ width: '100%', height: '100%' }}
        />

        {loading && (
          <Box
            sx={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              bgcolor: 'surface.overlay',
              p: 3,
              borderRadius: 2,
              display: 'flex',
              alignItems: 'center',
              gap: 2,
            }}
          >
            <CircularProgress size={24} />
            <Typography sx={{ color: 'text.secondary' }}>Loading chart…</Typography>
          </Box>
        )}

        {error && (
          <Box
            sx={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              bgcolor: 'error.main',
              border: '1px solid #ef4444',
              p: 3,
              borderRadius: 2,
            }}
          >
            <Typography color="error">{error}</Typography>
          </Box>
        )}
      </Box>
    </Box>
  );
}

// Helper component for stat boxes
// Helper component for legend items with toggle functionality
function LegendItem({
  color,
  label,
  dashed = false,
  visible = true,
  onClick,
}: {
  color: string;
  label: string;
  dashed?: boolean;
  visible?: boolean;
  onClick?: () => void;
}) {
  return (
    <Box
      onClick={onClick}
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 0.5,
        cursor: onClick ? 'pointer' : 'default',
        opacity: visible ? 1 : 0.4,
        transition: 'opacity 0.2s ease',
        userSelect: 'none',
        '&:hover': onClick ? {
          opacity: visible ? 0.8 : 0.6,
        } : {},
      }}
    >
      <Box
        sx={{
          width: 16,
          height: 2,
          bgcolor: color,
          borderStyle: dashed ? 'dashed' : 'solid',
          opacity: visible ? 1 : 0.5,
        }}
      />
      <Typography
        sx={{
          fontSize: 11,
          color: visible ? 'text.secondary' : 'text.tertiary',
          textDecoration: visible ? 'none' : 'line-through',
        }}
      >
        {label}
      </Typography>
    </Box>
  );
}

// Helper component for OHLC labels
function OHLCLabel({
  label,
  value,
  color = 'text.secondary',
}: {
  label: string;
  value: number | null;
  color?: string;
}) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
      <Typography
        sx={{
          fontSize: 11,
          color: 'text.tertiary',
          fontFamily: 'var(--font-family-mono)',
        }}
      >
        {label}:
      </Typography>
      <Typography
        sx={{
          fontSize: 12,
          fontWeight: 500,
          color,
          fontFamily: 'var(--font-family-mono)',
        }}
      >
        {value !== null ? value.toFixed(2) : '-'}
      </Typography>
    </Box>
  );
}

// Helper to format volume with K/M suffixes
function formatVolume(volume: number): string {
  if (volume >= 1_000_000_000) {
    return `${(volume / 1_000_000_000).toFixed(1)}B`;
  }
  if (volume >= 1_000_000) {
    return `${(volume / 1_000_000).toFixed(1)}M`;
  }
  if (volume >= 1_000) {
    return `${(volume / 1_000).toFixed(1)}K`;
  }
  return volume.toFixed(0);
}

// Helper to calculate SMA if not provided by API
function calculateSMA(data: number[], period: number): (number | null)[] {
  const result: (number | null)[] = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(null);
    } else {
      const sum = data.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0);
      result.push(sum / period);
    }
  }
  return result;
}

