import { useEffect, useRef, useState } from 'react';
import { Box, CircularProgress, Typography } from '@mui/material';
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, createSeriesMarkers } from 'lightweight-charts';
import type { SeriesMarker, UTCTimestamp } from 'lightweight-charts';
import { 
  fetchTimeseries, 
  formatIndicatorData, 
  createConstantLine,
  getDateRange,
  formatChartTime
} from '../../lib/services/timeseries';
import { differenceInDays } from 'date-fns';

import type { Report } from '../../lib/services/report';
import { fetchReports } from '../../lib/services/report';

type StockChartProps = {
  symbol: string;
  onReportClick?: (report: Report) => void;
};

export default function StockChart({ symbol, onReportClick }: StockChartProps) {
  const [reports, setReports] = useState<Report[]>([]);
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const candlestickSeriesRef = useRef<any>(null);
  const volumeSeriesRef = useRef<any>(null);
  const rsiSeriesRef = useRef<any>(null);
  const rsi5SeriesRef = useRef<any>(null);
  const overboughtLineRef = useRef<any>(null);
  const oversoldLineRef = useRef<any>(null);
  const atrTrailingRef = useRef<any>(null);
  const zeroLineRef = useRef<any>(null);
  const vwapHighestRef = useRef<any>(null);
  const vwapLowestRef = useRef<any>(null);
  const bvcSeriesRef = useRef<any>(null);
  const kalmanZscoreSeriesRef = useRef<any>(null);
  const kalmanZscoreUpperRef = useRef<any>(null);
  const kalmanZscoreLowerRef = useRef<any>(null);
  const yzVolatilitySeriesRef = useRef<any>(null);
  const rsRating20SeriesRef = useRef<any>(null);
  const rsRating20EmaSeriesRef = useRef<any>(null);
  const rsRating50SeriesRef = useRef<any>(null);
  const rsRating252SeriesRef = useRef<any>(null);
  const markersRef = useRef<any>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isChartReady, setIsChartReady] = useState(false);
  const toolTipWidth = 200;
  const legendWidth = 450;

  // Chart configuration with pane stretch factors (relative ratios)
  // Using setStretchFactor instead of setHeight due to v5 bug
  const chartConfig = {
    totalHeight: 1000, // Total chart height in pixels
    paneStretchFactors: [
      7,   // Panel 0: Main price chart (largest)
      1,   // Panel 1: RSI indicators
      1,   // Panel 2: BVC indicator
      1,   // Panel 3: Volatility indicators
      1,   // Panel 4: RS Rating indicators
    ],
    globalScaleMargins: { top: 0.02, bottom: 0.02 },
  };

  // Helper function to apply pane stretch factors
  const applyPaneHeights = () => {
    if (!chartRef.current) return;
    try {
      const panes = chartRef.current.panes();
      console.log(`Applying stretch factors to ${panes.length} panes:`, chartConfig.paneStretchFactors);
      panes.forEach((pane: any, index: number) => {
        if (index < chartConfig.paneStretchFactors.length) {
          // Use setStretchFactor for relative sizing (workaround for setHeight bug)
          if (typeof pane.setStretchFactor === 'function') {
            pane.setStretchFactor(chartConfig.paneStretchFactors[index]);
            console.log(`Set pane ${index} stretch factor to ${chartConfig.paneStretchFactors[index]}`);
          } else {
            console.warn(`pane.setStretchFactor is not a function for pane ${index}`);
          }
        }
      });
    } catch (e) {
      console.warn('Could not set pane stretch factors:', e);
    }
  };

  // Helper function to calculate percentage change from previous close
  const calculatePercentageChange = (prevClose: number, currentClose: number): string => {
    const change = ((currentClose - prevClose) / prevClose) * 100;
    const sign = change >= 0 ? '+' : '';
    return `${sign}${change.toFixed(2)}%`;
  };

  // Helper function to format price
  const formatPrice = (price: number): string => {
    return price.toFixed(2);
  };

  // Helper function to format date
  const formatDate = (timestamp: number): string => {
    const date = new Date(timestamp * 1000);
    const day = date.getDate().toString().padStart(2, '0');
    const month = (date.getMonth() + 1).toString().padStart(2, '0');
    const year = date.getFullYear();
    return `${day}/${month}/${year}`;
  };

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current || chartRef.current) return; // Prevent re-initialization if chart exists

    const chart = createChart(chartContainerRef.current, {
      leftPriceScale: {
        visible: true,
        borderColor: 'rgba(99, 102, 241, 0.2)',
      },
      height: chartConfig.totalHeight,
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
        mode: 0,
        vertLine: {
          color: 'rgba(99, 102, 241, 0.4)',
          width: 1,
          style: 2,
          labelBackgroundColor: '#6366f1',
        },
        horzLine: {
          color: 'rgba(99, 102, 241, 0.4)',
          width: 1,
          style: 2,
          labelBackgroundColor: '#6366f1',
        },
      },
      localization: {
        locale: 'en-US',
        dateFormat: 'dd/MM/yyyy',
      },
      overlayPriceScales: {
        borderVisible: false,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
      },
      handleScale: {
        mouseWheel: true,
        pinch: true,
      },
      rightPriceScale: {
        borderColor: 'rgba(99, 102, 241, 0.2)',
        scaleMargins: chartConfig.globalScaleMargins,
      },
      timeScale: {
        borderColor: 'rgba(99, 102, 241, 0.2)',
        timeVisible: true,
        secondsVisible: false,
      },
    });

    // Create the candlestick series
    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });

    // Create the volume series
    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: '#6366f1',
      priceFormat: {
        type: 'volume',
      },
      priceScaleId: 'volume',
    });

    // Configure volume scale
    chart.priceScale('volume').applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    });

    // Create ATR Trailing Stop series
    const atrTrailing = chart.addSeries(LineSeries, {
      color: '#22c55e',
      lineWidth: 2,
      lineStyle: 2,
      title: 'Trailing Stop',
      priceFormat: {
        type: 'price',
      },
      priceLineVisible: false,
    });

    // Create VWAP series
    const vwapHighest = chart.addSeries(LineSeries, {
      color: '#3b82f6',  // Blue
      lineWidth: 2,
      title: 'VWAP High',
      priceFormat: {
        type: 'price',
      },
      priceLineVisible: false,
    });

    const vwapLowest = chart.addSeries(LineSeries, {
      color: '#f97316',  // Orange
      lineWidth: 2,
      title: 'VWAP Low',
      priceFormat: {
        type: 'price',
      },
      priceLineVisible: false,
    });

    // Create RSI series in a separate pane
    const rsiSeries = chart.addSeries(LineSeries, {
      color: '#6366f1',
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => price.toFixed(2),
      },
      title: 'RSI (14)',
      priceScaleId: 'right',
    }, 1);

    // Create RSI 5 series in a separate pane
    const rsi5Series = chart.addSeries(LineSeries, {
      color: '#f59e0b',
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => price.toFixed(2),
      },
      title: 'RSI (5)',
      priceScaleId: 'right',
    }, 1);

    // Add horizontal lines for overbought/oversold levels
    const overboughtLine = chart.addSeries(LineSeries, {
      color: 'rgba(239, 68, 68, 0.5)',
      lineWidth: 1,
      title: 'Overbought (70)',
      priceScaleId: 'right',
    }, 1);

    const oversoldLine = chart.addSeries(LineSeries, {
      color: 'rgba(34, 197, 94, 0.5)',
      lineWidth: 1,
      title: 'Oversold (30)',
      priceScaleId: 'right',
    }, 1);


    // Create BVC series in a separate pane
    const bvcSeries = chart.addSeries(LineSeries, {
      color: '#a855f7',  // Purple
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => price.toFixed(2),
      },
      title: 'BVC',
      priceScaleId: 'right',
    }, 2);
    bvcSeries.moveToPane(2);
    const zeroLine = chart.addSeries(LineSeries, {
      color: 'rgba(156, 163, 175, 0.4)',
      lineWidth: 1,
      title: '0',
      priceScaleId: 'right',
    }, 2);

    // Create Yang-Zhang Volatility series
    const yzVolatilitySeries = chart.addSeries(LineSeries, {
      color: '#ec4899',  // Pink
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => price.toFixed(4),
      },
      title: 'YZ Volatility',
      priceScaleId: 'right',
    }, 3);
    yzVolatilitySeries.moveToPane(3);

    // Create Kalman Z-Score series in a separate pane
    const kalmanZscoreSeries = chart.addSeries(LineSeries, {
      color: '#06b6d4',  // Cyan
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => price.toFixed(2),
      },
      title: 'Kalman Z-Score',
      priceScaleId: 'right',
    }, 3);
    kalmanZscoreSeries.moveToPane(3);

    // Add horizontal lines for upper/lower bounds
    const kalmanZscoreUpper = chart.addSeries(LineSeries, {
      color: 'rgba(239, 68, 68, 0.4)',
      lineWidth: 1,
      title: 'Upper Bound (2)',
      priceScaleId: 'right',
    }, 3);

    const kalmanZscoreLower = chart.addSeries(LineSeries, {
      color: 'rgba(34, 197, 94, 0.4)',
      lineWidth: 1,
      title: 'Lower Bound (-2)',
      priceScaleId: 'right',
    }, 3);

    // Create RS Rating series in a separate pane (Panel 4)
    const rsRating20Series = chart.addSeries(LineSeries, {
      color: '#f97316',  // Orange
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => price.toFixed(0),
      },
      title: 'RS Rating 20',
      priceScaleId: 'right',
    }, 4);
    rsRating20Series.moveToPane(4);

    // Create RS Rating EMA series in the same pane
    const rsRating20EmaSeries = chart.addSeries(LineSeries, {
      color: '#8b5cf6',  // Violet
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => price.toFixed(0),
      },
      title: 'RS Rating 20 EMA',
      priceScaleId: 'right',
    }, 4);
    rsRating20EmaSeries.moveToPane(4);

    // Create RS Rating 50 series in the same pane
    const rsRating50Series = chart.addSeries(LineSeries, {
      color: '#14b8a6',  // Teal
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => price.toFixed(0),
      },
      title: 'RS Rating 50',
      priceScaleId: 'right',
    }, 4);
    rsRating50Series.moveToPane(4);

    // Create RS Rating 252 series in the same pane
    const rsRating252Series = chart.addSeries(LineSeries, {
      color: '#22c55e',  // Green
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => price.toFixed(0),
      },
      title: 'RS Rating 252',
      priceScaleId: 'right',
    }, 4);
    rsRating252Series.moveToPane(4);

    // Configure global chart options after all series are created
    chart.applyOptions({
      overlayPriceScales: {
        borderVisible: false,
      },
      rightPriceScale: {
        borderColor: '#2B2B43',
        scaleMargins: chartConfig.globalScaleMargins,
      },
    });

    // Store reference first so applyPaneHeights can access it
    chartRef.current = chart;
    
    // Apply pane heights using v5 API (with delay to ensure panes are ready)
    setTimeout(() => {
      applyPaneHeights();
    }, 100);

    // Store remaining references (chartRef already stored above)
    candlestickSeriesRef.current = candlestickSeries;
    volumeSeriesRef.current = volumeSeries;
    rsiSeriesRef.current = rsiSeries;
    rsi5SeriesRef.current = rsi5Series;
    overboughtLineRef.current = overboughtLine;
    oversoldLineRef.current = oversoldLine;
    atrTrailingRef.current = atrTrailing;
    vwapHighestRef.current = vwapHighest;
    vwapLowestRef.current = vwapLowest;
    bvcSeriesRef.current = bvcSeries;
    zeroLineRef.current = zeroLine;
    yzVolatilitySeriesRef.current = yzVolatilitySeries;
    kalmanZscoreSeriesRef.current = kalmanZscoreSeries;
    kalmanZscoreUpperRef.current = kalmanZscoreUpper;
    kalmanZscoreLowerRef.current = kalmanZscoreLower;
    rsRating20SeriesRef.current = rsRating20Series;
    rsRating20EmaSeriesRef.current = rsRating20EmaSeries;
    rsRating50SeriesRef.current = rsRating50Series;
    rsRating252SeriesRef.current = rsRating252Series;
    // Cleanup

    // Signal that chart is ready
    setIsChartReady(true);

    return () => {
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
      candlestickSeriesRef.current = null;
      volumeSeriesRef.current = null;
      rsiSeriesRef.current = null;
      rsi5SeriesRef.current = null;
      overboughtLineRef.current = null;
      oversoldLineRef.current = null;
      atrTrailingRef.current = null;
      vwapHighestRef.current = null;
      vwapLowestRef.current = null;
      bvcSeriesRef.current = null;
      zeroLineRef.current = null;
      yzVolatilitySeriesRef.current = null;
      kalmanZscoreSeriesRef.current = null;
      kalmanZscoreUpperRef.current = null;
      kalmanZscoreLowerRef.current = null;
      rsRating20SeriesRef.current = null;
      rsRating20EmaSeriesRef.current = null;
      rsRating50SeriesRef.current = null;
      rsRating252SeriesRef.current = null;
      markersRef.current = null;
      setIsChartReady(false);
    };
  }, []); // Empty dependency array since this should only run once

  // Setup legend and crosshair handler
  useEffect(() => {
    if (!chartRef.current || !chartContainerRef.current) return;

    // Create and style the legend and tooltip elements
    const legendElement = document.createElement('div');
    const toolTipElement = document.createElement('div');

    legendElement.style.cssText = `
      position: absolute;
      left: 12px;
      top: 12px;
      z-index: 2;
      font-size: 13px;
      font-family: 'SF Mono', 'Fira Code', 'Monaco', monospace;
      line-height: 20px;
      font-weight: 400;
      width: ${legendWidth}px;
      padding: 12px 16px;
      background: linear-gradient(135deg, rgba(30, 30, 46, 0.95) 0%, rgba(24, 24, 36, 0.98) 100%);
      color: #e2e8f0;
      border-radius: 8px;
      border: 1px solid rgba(99, 102, 241, 0.25);
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(99, 102, 241, 0.1);
      backdrop-filter: blur(12px);
    `;

    toolTipElement.style.cssText = `
      width: ${toolTipWidth}px;
      position: absolute;
      display: none;
      padding: 12px 16px;
      box-sizing: border-box;
      font-size: 12px;
      text-align: left;
      z-index: 1000;
      top: 12px;
      left: 12px;
      pointer-events: none;
      border-radius: 8px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
      font-family: 'SF Mono', 'Fira Code', 'Monaco', monospace;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      background: linear-gradient(135deg, rgba(30, 30, 46, 0.98) 0%, rgba(24, 24, 36, 0.98) 100%);
      color: #e2e8f0;
      border: 1px solid rgba(99, 102, 241, 0.3);
      backdrop-filter: blur(12px);
    `;

    // Set initial legend content
    legendElement.innerHTML = `
      <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
        <span style="font-size: 18px; font-weight: 600; background: linear-gradient(135deg, #6366f1, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">${symbol}</span>
        <span style="font-size: 11px; color: #6b7280; padding: 2px 8px; background: rgba(99, 102, 241, 0.15); border-radius: 4px;">Loading...</span>
      </div>
      <div style="display: flex; gap: 16px; flex-wrap: wrap;">
        <div style="display: flex; gap: 12px;">
          <span style="color: #6b7280;">O</span> <span style="color: #9ca3af;">--</span>
          <span style="color: #6b7280;">H</span> <span style="color: #9ca3af;">--</span>
          <span style="color: #6b7280;">L</span> <span style="color: #9ca3af;">--</span>
          <span style="color: #6b7280;">C</span> <span style="color: #9ca3af;">--</span>
        </div>
        <div style="color: #6b7280;">Chg <span style="color: #9ca3af;">--</span></div>
      </div>
    `;

    chartContainerRef.current.appendChild(legendElement);
    chartContainerRef.current.appendChild(toolTipElement);

    // Subscribe to crosshair move
    const subscription = chartRef.current.subscribeCrosshairMove((param: any) => {
      // Update legend with OHLC data
      const candleData = param.seriesData.get(candlestickSeriesRef.current);
      if (candleData) {
        const { open, high, low, close, time } = candleData;
        
        // Get previous day's data
        const series = candlestickSeriesRef.current;
        const dataPoints = series.data();
        const currentIndex = dataPoints.findIndex((d: any) => d.time === time);
        const prevClose = currentIndex > 0 ? dataPoints[currentIndex - 1].close : open;
        
        const percentChange = calculatePercentageChange(prevClose, close);
        const isPositive = close >= prevClose;
        const color = isPositive ? '#22c55e' : '#ef4444';
        const arrow = isPositive ? '▲' : '▼';
        
        legendElement.innerHTML = `
          <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px;">
            <span style="font-size: 18px; font-weight: 600; background: linear-gradient(135deg, #6366f1, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">${symbol}</span>
            <span style="font-size: 11px; color: #9ca3af; padding: 2px 8px; background: rgba(99, 102, 241, 0.1); border-radius: 4px;">${formatDate(time)}</span>
          </div>
          <div style="display: flex; gap: 16px; align-items: center;">
            <div><span style="color: #6b7280; font-size: 11px;">O</span> <span style="color: ${color}; font-weight: 500;">${formatPrice(open)}</span></div>
            <div><span style="color: #6b7280; font-size: 11px;">H</span> <span style="color: ${color}; font-weight: 500;">${formatPrice(high)}</span></div>
            <div><span style="color: #6b7280; font-size: 11px;">L</span> <span style="color: ${color}; font-weight: 500;">${formatPrice(low)}</span></div>
            <div><span style="color: #6b7280; font-size: 11px;">C</span> <span style="color: ${color}; font-weight: 600; font-size: 14px;">${formatPrice(close)}</span></div>
            <div style="padding: 3px 8px; background: ${isPositive ? 'rgba(34, 197, 94, 0.15)' : 'rgba(239, 68, 68, 0.15)'}; border-radius: 4px; border: 1px solid ${isPositive ? 'rgba(34, 197, 94, 0.3)' : 'rgba(239, 68, 68, 0.3)'};">
              <span style="color: ${color}; font-weight: 600; font-size: 12px;">${arrow} ${percentChange}</span>
            </div>
          </div>
        `;
      }

      if (
        param.point === undefined ||
        !param.time ||
        param.point.x < 0 ||
        param.point.x > chartContainerRef.current!.clientWidth ||
        param.point.y < 0 ||
        param.point.y > chartContainerRef.current!.clientHeight
      ) {
        toolTipElement.style.display = 'none';
      } else {
        const hoveredReport = reports.find((report: Report) => {
          if (!report.ngaykn) return false;
          if (!param.time) return false;
          const reportDate = new Date(report.ngaykn);
          const diff = differenceInDays(reportDate, new Date((param?.time as number) * 1000));
          return diff >= -3 && diff <= 3;
        });

        if (hoveredReport) {
          toolTipElement.style.display = 'block';
          toolTipElement.innerHTML = `
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
              <span style="font-size: 16px;">📄</span>
              <span style="color: #a855f7; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Research Report</span>
            </div>
            <div style="font-size: 13px; margin-bottom: 8px; color: #e2e8f0; font-weight: 500; line-height: 1.4;">
              ${hoveredReport.tenbaocao}
            </div>
            <div style="display: flex; gap: 8px; align-items: center;">
              <span style="color: #6366f1; font-size: 11px; padding: 2px 6px; background: rgba(99, 102, 241, 0.15); border-radius: 4px;">${hoveredReport.nguon}</span>
              <span style="color: #6b7280; font-size: 11px;">${new Date(hoveredReport.ngaykn || '').toLocaleDateString()}</span>
            </div>
          `;

          let left = param.point.x;
          const timeScaleWidth = chartRef.current.timeScale().width();
          const priceScaleWidth = chartRef.current.priceScale('left').width();
          const halfTooltipWidth = toolTipWidth / 2;
          const newLeft = Math.max(
            Math.min(
              left + priceScaleWidth - halfTooltipWidth,
              priceScaleWidth + timeScaleWidth - toolTipWidth
            ),
            priceScaleWidth
          );

          toolTipElement.style.left = newLeft + 'px';
          toolTipElement.style.top = '0px';
        } else {
          toolTipElement.style.display = 'none';
        }
      }
    });

    return () => {
      if (chartContainerRef.current) {
        if (chartContainerRef.current.contains(legendElement)) {
          chartContainerRef.current.removeChild(legendElement);
        }
        if (chartContainerRef.current.contains(toolTipElement)) {
          chartContainerRef.current.removeChild(toolTipElement);
        }
      }
      if (chartRef.current) {
        chartRef.current.unsubscribeCrosshairMove(subscription);
      }
    };
  }, [symbol, reports]); // Depend on symbol and reports

  // Fetch data and reports
  useEffect(() => {
    if (!symbol || !chartRef.current) return;

    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);

        // Clear existing markers
        if (markersRef.current) {
          markersRef.current = markersRef.current.setMarkers([]);
        }

        // Fetch both data and reports in parallel
        const [result, reportsData] = await Promise.all([
          fetchTimeseries(symbol, {
          interval: "1d",
          ...getDateRange(360 * 5),
          indicators: [
            { name: "rsi", params: { period: 14 } },
            { name: "atr_trailing" },
            { name: "vwap", params: { window: 200 } },
            { name: "bvc", params: { window: 20, kappa: 0.1 } },
            { name: "kalman_zscore", params: { window: 20 } },
            { name: "yz_volatility", params: { window: 30, periods: 252 } },
            { name: "rs_rating" }
          ]
        }),
          fetchReports(symbol)
        ]);

        // Update reports state
        setReports(reportsData);

        // Format data for the chart
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
          color: result.timeseries.close[i] >= result.timeseries.open[i] ? 'rgba(34, 197, 94, 0.5)' : 'rgba(239, 68, 68, 0.5)'
        }));

        // Update the series
        candlestickSeriesRef.current?.setData(candleData);
        volumeSeriesRef.current?.setData(volumeData);

        // Only update additional indicators if chart is ready
        if (isChartReady) {
          // Create markers for reports
          const markers: SeriesMarker<UTCTimestamp>[] = reports
            .filter(report => report.ngaykn)
            .map(report => ({
              time: formatChartTime(report.ngaykn || '') as UTCTimestamp,
              position: 'aboveBar',
              color: '#2196F3',
              text: '📄',
              shape: 'circle',
              size: 2,
              title: `${report.tenbaocao}\n${report.nguon}\n${new Date(report.ngaykn || '').toLocaleDateString()}`,
            }));

          if (markers.length > 0) {
            markersRef.current = createSeriesMarkers(candlestickSeriesRef.current, markers);
          }
          
          // Format and update all indicators
          const rsiChartData = formatIndicatorData(result.timestamps, result.indicators?.rsi ?? []);
          rsiSeriesRef.current?.setData(rsiChartData);
          const rsi5ChartData = formatIndicatorData(result.timestamps, result.indicators?.rsi_5 ?? []);
          rsi5SeriesRef.current?.setData(rsi5ChartData);
          
          const timeRange = createConstantLine(rsiChartData, 70);
          const timeRange2 = createConstantLine(rsiChartData, 30);
          const zeroLineData = createConstantLine(rsiChartData, 0);
          overboughtLineRef.current?.setData(timeRange);
          oversoldLineRef.current?.setData(timeRange2);
          zeroLineRef.current?.setData(zeroLineData);

          const atrTrailingData = formatIndicatorData(result.timestamps, result.indicators?.atr_trailing ?? []);
          atrTrailingRef.current?.setData(atrTrailingData);

          const vwapHighestData = formatIndicatorData(result.timestamps, result.indicators?.vwap_highest ?? []);
          const vwapLowestData = formatIndicatorData(result.timestamps, result.indicators?.vwap_lowest ?? []);
          vwapHighestRef.current?.setData(vwapHighestData);
          vwapLowestRef.current?.setData(vwapLowestData);

          const bvcData = formatIndicatorData(result.timestamps, result.indicators?.bvc ?? []);
          bvcSeriesRef.current?.setData(bvcData);

          const yzVolatilityData = formatIndicatorData(result.timestamps, result.indicators?.yz_volatility ?? []);
          yzVolatilitySeriesRef.current?.setData(yzVolatilityData);

          const kalmanZscoreData = formatIndicatorData(result.timestamps, result.indicators?.kalman_zscore ?? []);
          kalmanZscoreSeriesRef.current?.setData(kalmanZscoreData);
          const kalmanUpperBound = createConstantLine(kalmanZscoreData, 2);
          const kalmanLowerBound = createConstantLine(kalmanZscoreData, -2);
          kalmanZscoreUpperRef.current?.setData(kalmanUpperBound);
          kalmanZscoreLowerRef.current?.setData(kalmanLowerBound);

          // Handle RS Rating indicators
          const rsRating20Data = formatIndicatorData(result.timestamps, result.indicators?.rs_rating_20 ?? []);
          const rsRating20EmaData = formatIndicatorData(result.timestamps, result.indicators?.rs_rating_20_ema ?? []);
          const rsRating50Data = formatIndicatorData(result.timestamps, result.indicators?.rs_rating_50 ?? []);
          const rsRating252Data = formatIndicatorData(result.timestamps, result.indicators?.rs_rating_252 ?? []);
          
          rsRating20SeriesRef.current?.setData(rsRating20Data);
          rsRating20EmaSeriesRef.current?.setData(rsRating20EmaData);
          rsRating50SeriesRef.current?.setData(rsRating50Data);
          rsRating252SeriesRef.current?.setData(rsRating252Data);

          // Fit content to show all data and auto-scale price
          const timeScale = chartRef.current?.timeScale();
          if (timeScale) {
            timeScale.fitContent();
          }
          
          // Re-apply pane heights after data is loaded
          setTimeout(() => applyPaneHeights(), 50);
        } else {
          setIsChartReady(true);
        }

      } catch (error) {
        console.error('Error fetching data:', error);
        setError(error instanceof Error ? error.message : 'An error occurred');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [symbol, isChartReady]);

  // Handle resize
  useEffect(() => {
    const handleResize = () => {
      if (chartRef.current) {
        chartRef.current.resize(
          chartContainerRef.current?.clientWidth || 0,
          chartContainerRef.current?.clientHeight || 0
        );
      }
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return (
    <Box sx={{ 
        width: '100%', 
        height: chartConfig.totalHeight,
        position: 'relative',
        bgcolor: '#0a0a0f',
        borderRadius: 2,
        overflow: 'hidden',
      }}
    >
      <div
        ref={chartContainerRef as React.RefObject<HTMLDivElement>}
        style={{
          width: '100%',
          height: '100%',
        }}
      ></div>
      
      {/* Overlay loading states */}
      {loading && (
        <Box sx={{ 
          position: 'absolute', 
          top: '50%', 
          left: '50%', 
          transform: 'translate(-50%, -50%)',
          background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.95) 0%, rgba(24, 24, 36, 0.95) 100%)',
          p: 3,
          borderRadius: 2,
          border: '1px solid rgba(99, 102, 241, 0.3)',
          boxShadow: '0 8px 32px rgba(0, 0, 0, 0.4)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 2,
        }}>
          <CircularProgress 
            size={40}
            sx={{ 
              color: '#6366f1',
              '& .MuiCircularProgress-circle': {
                strokeLinecap: 'round',
              }
            }} 
          />
          <Typography 
            variant="body2" 
            sx={{ 
              color: '#9ca3af',
              fontFamily: "'SF Mono', monospace",
            }}
          >
            Loading chart data...
          </Typography>
        </Box>
      )}
      {error && (
        <Box sx={{ 
          position: 'absolute', 
          top: '50%', 
          left: '50%', 
          transform: 'translate(-50%, -50%)',
          background: 'linear-gradient(135deg, rgba(30, 30, 46, 0.95) 0%, rgba(24, 24, 36, 0.95) 100%)',
          p: 3,
          borderRadius: 2,
          border: '1px solid rgba(239, 68, 68, 0.3)',
          boxShadow: '0 8px 32px rgba(0, 0, 0, 0.4)',
          maxWidth: 400,
          textAlign: 'center',
        }}>
          <Typography 
            sx={{ 
              color: '#ef4444',
              fontFamily: "'SF Mono', monospace",
              fontSize: '0.875rem',
            }}
          >
            ⚠️ {error}
          </Typography>
        </Box>
      )}
    </Box>
  );
}