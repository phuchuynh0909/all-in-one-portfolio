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

  // Panel height configuration - 70% main chart, 30% indicators
  // 
  // Current distribution:
  // ┌─────────────────────────────┐
  // │ Panel 0: Main Chart (70%)   │ ← 840px (candlesticks, volume, VWAP, ATR)
  // │         (840px)             │
  // ├─────────────────────────────┤
  // │ Panel 1: RSI (7.5%)         │ ← 90px (RSI 5, RSI 14, bounds)
  // ├─────────────────────────────┤
  // │ Panel 2: BVC (7.5%)         │ ← 90px (BVC + zero line)
  // ├─────────────────────────────┤
  // │ Panel 3: Volatility (7.5%)  │ ← 90px (Yang-Zhang + Kalman)
  // ├─────────────────────────────┤
  // │ Panel 4: RS Rating (7.5%)   │ ← 90px (RS Rating 20/50/252 + EMAs)
  // └─────────────────────────────┘
  // Total: 1200px (100%)
  //
  // To adjust: Modify the panelRatios values below (must sum to 1.0)
  // 
  // Example alternative configurations:
  // - More focus on RS Rating: { main: 0.65, rsi: 0.07, bvc: 0.07, volatility: 0.07, rsRating: 0.14 }
  // - Minimal indicators: { main: 0.80, rsi: 0.05, bvc: 0.05, volatility: 0.05, rsRating: 0.05 }
  // - Equal indicators: { main: 0.70, rsi: 0.075, bvc: 0.075, volatility: 0.075, rsRating: 0.075 }
  const chartConfig = {
    totalHeight: 1200, // Total chart height in pixels
    panelRatios: {
      main: 0.70,        // 70% - Main price chart (840px)
      rsi: 0.075,        // 7.5% - RSI indicators (90px)
      bvc: 0.075,        // 7.5% - BVC indicator (90px)
      volatility: 0.075, // 7.5% - Volatility indicators (90px)
      rsRating: 0.075,   // 7.5% - RS Rating indicators (90px)
    },
    globalScaleMargins: { top: 0.02, bottom: 0.02 },
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
      },
      height: chartConfig.totalHeight,
      width: chartContainerRef.current.clientWidth,
      layout: {
        background: { color: '#0f0f0f' },
        textColor: '#b8b8b8',
      },
      grid: {
        vertLines: { color: '#0f0f0f' },
        horzLines: { color: '#0f0f0f' },
      },
      crosshair: {
        mode: 0,
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
        borderColor: '#2B2B43',
        scaleMargins: chartConfig.globalScaleMargins,
      },
      timeScale: {
        borderColor: '#2B2B43',
        timeVisible: true,
        secondsVisible: false,
      },
    });

    // Create the candlestick series
    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#4CAF50',
      downColor: '#F44336',
      borderVisible: false,
      wickUpColor: '#4CAF50',
      wickDownColor: '#F44336',
    });

    // Create the volume series
    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: '#26a69a',
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
      color: '#4caf50',
      lineWidth: 2,
      lineStyle: 2,
      title: 'Trailing Stop',
      priceFormat: {
        type: 'price',
      },
    });

    // Create VWAP series
    const vwapHighest = chart.addSeries(LineSeries, {
      color: '#2196F3',  // Blue
      lineWidth: 2,
      title: 'VWAP High',
      priceFormat: {
        type: 'price',
      },
      priceLineVisible: false,
    });

    const vwapLowest = chart.addSeries(LineSeries, {
      color: '#FF5722',  // Deep Orange
      lineWidth: 2,
      title: 'VWAP Low',
      priceFormat: {
        type: 'price',
      },
      priceLineVisible: false,
    });

    // Create RSI series in a separate pane
    const rsiSeries = chart.addSeries(LineSeries, {
      color: '#2962FF',
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
      color: '#FF9800',
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
      color: '#FF9800',
      lineWidth: 1,
      title: 'Overbought (70)',
      priceScaleId: 'right',
    }, 1);

    const oversoldLine = chart.addSeries(LineSeries, {
      color: '#FF9800',
      lineWidth: 1,
      title: 'Oversold (30)',
      priceScaleId: 'right',
    }, 1);


    // Create BVC series in a separate pane
    const bvcSeries = chart.addSeries(LineSeries, {
      color: '#9C27B0',  // Purple
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
      color: '#FF9800',
      lineWidth: 1,
      title: '0',
      priceScaleId: 'right',
    }, 2);

    // Create Yang-Zhang Volatility series
    const yzVolatilitySeries = chart.addSeries(LineSeries, {
      color: '#E91E63',  // Pink
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
      color: '#00BCD4',  // Cyan
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
      color: '#FF9800',
      lineWidth: 1,
      title: 'Upper Bound (2)',
      priceScaleId: 'right',
    }, 3);

    const kalmanZscoreLower = chart.addSeries(LineSeries, {
      color: '#FF9800',
      lineWidth: 1,
      title: 'Lower Bound (-2)',
      priceScaleId: 'right',
    }, 3);

    // Create RS Rating series in a separate pane (Panel 4)
    const rsRating20Series = chart.addSeries(LineSeries, {
      color: '#FF6B35',  // Orange-red
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
      color: '#7B68EE',  // Medium Slate Blue
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
      color: '#00CED1',  // Dark Turquoise
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
      color: '#32CD32',  // Lime Green
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

    // Store references
    chartRef.current = chart;
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
      font-size: 14px;
      font-family: -apple-system, BlinkMacSystemFont, 'Trebuchet MS', Roboto, Ubuntu, sans-serif;
      line-height: 18px;
      font-weight: 300;
      width: ${legendWidth}px;
      padding: 8px 12px;
      background: rgba(18, 18, 18, 0.8);
      color: #fff;
      border-radius: 4px;
      box-shadow: 0 2px 5px 0 rgba(117, 134, 150, 0.45);
    `;

    toolTipElement.style.cssText = `
      width: ${toolTipWidth}px;
      position: absolute;
      display: none;
      padding: 8px;
      box-sizing: border-box;
      font-size: 12px;
      text-align: left;
      z-index: 1000;
      top: 12px;
      left: 12px;
      pointer-events: none;
      border-radius: 4px;
      border-bottom: none;
      box-shadow: 0 2px 5px 0 rgba(117, 134, 150, 0.45);
      font-family: -apple-system, BlinkMacSystemFont, 'Trebuchet MS', Roboto, Ubuntu, sans-serif;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      background: rgba(255, 255, 255, 0.25);
      color: black;
      border-color: rgba(239, 83, 80, 1);
    `;

    // Set initial legend content
    legendElement.innerHTML = `
      <div style="font-size: 16px; margin-bottom: 4px;">${symbol}</div>
      <div style="font-size: 12px; margin-bottom: 8px; opacity: 0.8;">Loading...</div>
      <div style="display: grid; grid-template-columns: auto 1fr; gap: 8px;">
        <div>
          O: <span>--</span>
          H: <span>--</span>
          L: <span>--</span>
          C: <span>--</span>
        </div>
        <div>Change: <span>--</span></div>
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
        const color = close >= prevClose ? '#4CAF50' : '#F44336';
        
        legendElement.innerHTML = `
          <div style="font-size: 16px; margin-bottom: 4px;">${symbol}</div>
          <div style="font-size: 12px; margin-bottom: 8px; opacity: 0.8;">${formatDate(time)}</div>
          <div style="display: grid; grid-template-columns: auto 1fr; gap: 8px;">
            <div>
              O: <span style="color: ${color}">${formatPrice(open)}</span>
              H: <span style="color: ${color}">${formatPrice(high)}</span>
              L: <span style="color: ${color}">${formatPrice(low)}</span>
              C: <span style="color: ${color}">${formatPrice(close)}</span>
            </div>
            <div>Change: <span style="color: ${color}">${percentChange}</span></div>
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
            <div style="color: rgba(239, 83, 80, 1)">📄 Research Report</div>
            <div style="font-size: 14px; margin: 4px 0px; color: black">
              ${hoveredReport.tenbaocao}
            </div>
            <div style="color: black">
              ${hoveredReport.nguon} - ${new Date(hoveredReport.ngaykn || '').toLocaleDateString()}
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
          color: result.timeseries.close[i] >= result.timeseries.open[i] ? '#4CAF50' : '#F44336'
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

          // Set visible range
          const timeScale = chartRef.current?.timeScale();
          if (timeScale) {
            const endTime = Math.floor(Date.now() / 1000);
            const startTime = endTime - 365 * 24 * 60 * 60;
            timeScale.setVisibleRange({
              from: startTime as UTCTimestamp,
              to: endTime as UTCTimestamp,
            });
          }
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
        bgcolor: '#121212'
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
          bgcolor: 'rgba(18, 18, 18, 0.8)',
          p: 2,
          borderRadius: 1
        }}>
          <CircularProgress />
        </Box>
      )}
      {error && (
        <Box sx={{ 
          position: 'absolute', 
          top: '50%', 
          left: '50%', 
          transform: 'translate(-50%, -50%)',
          bgcolor: 'rgba(18, 18, 18, 0.8)',
          p: 2,
          borderRadius: 1
        }}>
          <Typography color="error">{error}</Typography>
        </Box>
      )}
    </Box>
  );
}