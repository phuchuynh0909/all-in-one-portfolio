import { useEffect, useRef, useState } from 'react';
import { Box, Typography, Paper, Stack, CircularProgress, Alert } from '@mui/material';
import { createChart, CandlestickSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts';
import { fetchFutureOhlc } from '../lib/services/future';
import type { FutureOhlcResponse } from '../lib/services/future';
import BsiPanel from '../components/chart/panels/BsiPanel';
import ZScorePanel from '../components/chart/panels/ZScorePanel';
import KamaPanel from '../components/chart/panels/KamaPanel';

const SYMBOL = 'VN30F1M';
const THRESHOLD = 2.0;

export default function Future() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const [data, setData] = useState<FutureOhlcResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true);
        const response = await fetchFutureOhlc(SYMBOL, {
          kappa: 0.1,
          hp_period: 45,
          lp_period: 11
        });
        setData(response);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch future data');
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, []);

  useEffect(() => {
    if (!chartContainerRef.current) return;
    const chart = createChart(chartContainerRef.current, {
      layout: { background: { color: '#1e1e1e' }, textColor: '#fff' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
      width: chartContainerRef.current.clientWidth,
      height: 600,
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a', downColor: '#ef5350',
      borderUpColor: '#26a69a', borderDownColor: '#ef5350',
      wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    }, 0);
    candleSeriesRef.current = candleSeries;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!candleSeriesRef.current || !data) return;

    const candleData = data.timestamps.map((ts, i) => ({
      time: (new Date(ts).getTime() / 1000) as UTCTimestamp,
      open: data.ohlc.open[i], high: data.ohlc.high[i],
      low: data.ohlc.low[i], close: data.ohlc.close[i],
    }));
    candleSeriesRef.current.setData(candleData);

    const markers = [];
    const bsiNorm = data.indicators.bsi_norm;

    for (let i = 1; i < bsiNorm.length; i++) {
      const prev = bsiNorm[i - 1];
      const curr = bsiNorm[i];
      if (prev === null || curr === null) continue;

      const time = (new Date(data.timestamps[i]).getTime() / 1000) as UTCTimestamp;

      if (prev < THRESHOLD && curr >= THRESHOLD) {
        markers.push({
          time,
          position: 'aboveBar' as const,
          color: '#ef5350',
          shape: 'arrowDown' as const,
          text: 'Overbought',
        });
      }
      else if (prev > -THRESHOLD && curr <= -THRESHOLD) {
        markers.push({
          time,
          position: 'belowBar' as const,
          color: '#26a69a',
          shape: 'arrowUp' as const,
          text: 'Oversold',
        });
      }
    }
    candleSeriesRef.current.setMarkers(markers);

    chartRef.current?.timeScale().fitContent();
  }, [data]);

  return (
    <Box sx={{ p: 3 }}>
      <Stack spacing={2}>
        <Typography variant="h4">⚡ Future — {SYMBOL} (5M)</Typography>
        {loading && <CircularProgress />}
        {error && <Alert severity="error">{error}</Alert>}
        <Paper sx={{ p: 0 }}>
          <div ref={chartContainerRef} style={{ width: '100%' }} />
          {chartRef.current && data && (
            <>
              <KamaPanel chart={chartRef.current} data={data} />
              <BsiPanel chart={chartRef.current} data={data} />
              <ZScorePanel chart={chartRef.current} data={data} threshold={THRESHOLD} />
            </>
          )}
        </Paper>
      </Stack>
    </Box>
  );
}
