import { useEffect, useRef, useState } from 'react';
import { Box, CircularProgress, Typography } from '@mui/material';
import { createChart, ISeriesApi } from 'lightweight-charts';
import {
  fetchTimeseries,
  getDateRange,
} from '../../lib/services/timeseries';
import { differenceInDays } from 'date-fns';

import type { Report } from '../../lib/services/report';
import { fetchReports } from '../../lib/services/report';

// Import Panel Components
import PricePanel from './panels/PricePanel';
import RsiPanel from './panels/RsiPanel';
import BvcPanel from './panels/BvcPanel';
import VolatilityPanel from './panels/VolatilityPanel';
// import RsRatingPanel from './panels/RsRatingPanel';
import MatrixSeriesPanel from './panels/MatrixSeriesPanel';
import WilliamsVixFixPanel from './panels/WilliamsVixFixPanel';
import SqueezeTtmPanel from './panels/SqueezeTtmPanel';

type StockChartProps = {
  symbol: string;
  onReportClick?: (report: Report) => void;
  height?: number;
};

export default function StockChart({ symbol, onReportClick, height }: StockChartProps) {
  const [reports, setReports] = useState<Report[]>([]);
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);

  // State for data
  const [timeseriesData, setTimeseriesData] = useState<any>(null);
  const [indicatorsData, setIndicatorsData] = useState<any>(null);
  const [timestamps, setTimestamps] = useState<string[]>([]);

  // Ref for main candlestick series (needed for Legend)
  const candlestickSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isChartReady, setIsChartReady] = useState(false);
  const toolTipWidth = 200;
  const legendWidth = 450;

  // Chart configuration with pane stretch factors (relative ratios)
  // Using setStretchFactor instead of setHeight due to v5 bug
  const resolvedHeight = height ?? 800;
  const chartConfig = {
    totalHeight: resolvedHeight,
    paneStretchFactors: [
      5,   // Panel 0: Main price chart (largest)
      1,   // Panel 1: RSI indicators
      1,   // Panel 2: BVC indicator
      1,   // Panel 3: Volatility indicators
      // 1,   // Panel 4: RS Rating indicators
      2,   // Panel 4: Matrix Series indicator
      2,   // Panel 5: Squeeze TTM
      1,   // Panel 6: Williams Vix Fix
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

  // Helper function to format date
  const formatDate = (timestamp: number): string => {
    const date = new Date(timestamp * 1000);
    const day = date.getDate().toString().padStart(2, '0');
    const month = (date.getMonth() + 1).toString().padStart(2, '0');
    const year = date.getFullYear();
    return `${day}/${month}/${year}`;
  };

  const formatPrice = (price: number): string => {
    return price.toFixed(2);
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
        mode: 3, // Magnet mode
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

    // Configure global chart options
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

    // Signal that chart is ready
    setIsChartReady(true);

    return () => {
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
      candlestickSeriesRef.current = null;
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
      if (candlestickSeriesRef.current) {
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
              <span style="color: #6b7280; font-size: 11px;">${new Date(hoveredReport.ngaykn || '').toLocaleDateString('en-GB', { timeZone: 'Asia/Ho_Chi_Minh' })}</span>
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

        // Fetch timeseries data first
        const result = await fetchTimeseries(symbol, {
          interval: "1d",
          ...getDateRange(360 * 5),
          indicators: [
            { name: "rsi", params: { period: 14 } },
            { name: "atr_trailing" },
            { name: "vwap", params: { window: 200 } },
            { name: "bvc", params: { window: 20, kappa: 0.1 } },
            { name: "kalman_zscore", params: { window: 20 } },
            { name: "yz_volatility", params: { window: 30, periods: 252 } },
            // { name: "rs_rating" },
            { name: "matrix_series", params: { price_period: 20, sup_res_period: 50, sup_res_percentage: 100, smoother: 5 } },
            { name: "williams_vix_fix" },
            { name: "squeeze_ttm" }
          ]
        });

        // Fetch reports after timeseries succeeds (ignore failures)
        try {
          const reportsData = await fetchReports(symbol);
          setReports(reportsData);
        } catch (reportError) {
          console.warn('Report list fetch failed:', reportError);
          setReports([]);
        }

        // Set data state
        setTimeseriesData(result);
        setIndicatorsData(result.indicators);
        setTimestamps(result.timestamps);

        // Fit content to show all data and auto-scale price
        const timeScale = chartRef.current?.timeScale();
        if (timeScale) {
          timeScale.fitContent();
        }

        // Re-apply pane heights after data is loaded
        setTimeout(() => applyPaneHeights(), 50);

      } catch (error) {
        console.error('Error fetching data:', error);
        setError(error instanceof Error ? error.message : 'An error occurred');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [symbol, isChartReady]);

  useEffect(() => {
    if (!chartRef.current || !timestamps.length) return;
    const fitChart = () => {
      chartRef.current?.timeScale().fitContent();
      applyPaneHeights();
    };
    const timeoutId = window.setTimeout(fitChart, 0);
    return () => window.clearTimeout(timeoutId);
  }, [timestamps, isChartReady]);

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

  useEffect(() => {
    if (!chartRef.current || !chartContainerRef.current) return;
    chartRef.current.resize(chartContainerRef.current.clientWidth || 0, resolvedHeight);
  }, [resolvedHeight]);

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

      {/* Render Panels */}
      {isChartReady && chartRef.current && (
        <>
          <PricePanel
            chart={chartRef.current}
            data={timeseriesData}
            indicators={indicatorsData}
            reports={reports}
            isChartReady={isChartReady}
            onSeriesReady={(series) => { candlestickSeriesRef.current = series; }}
          />
          <RsiPanel
            chart={chartRef.current}
            data={indicatorsData}
            timestamps={timestamps}
          />
          <BvcPanel
            chart={chartRef.current}
            data={indicatorsData}
            timestamps={timestamps}
          />
          <VolatilityPanel
            chart={chartRef.current}
            data={indicatorsData}
            timestamps={timestamps}
          />
          {/* <RsRatingPanel
            chart={chartRef.current}
            data={indicatorsData}
            timestamps={timestamps}
          /> */}
          <MatrixSeriesPanel
            chart={chartRef.current}
            data={indicatorsData}
            timestamps={timestamps}
          />
          <SqueezeTtmPanel
            chart={chartRef.current}
            data={indicatorsData}
            timestamps={timestamps}
            paneIndex={5}
          />
          <WilliamsVixFixPanel
            chart={chartRef.current}
            data={indicatorsData}
            timestamps={timestamps}
            paneIndex={6}
          />
        </>
      )}

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
