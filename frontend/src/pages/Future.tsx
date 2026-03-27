import { useEffect, useRef, useState } from 'react';
import { Box, Typography, Paper, Stack, CircularProgress, Alert, Slider } from '@mui/material';
import { createChart, CandlestickSeries } from 'lightweight-charts';
import type { IChartApi, UTCTimestamp } from 'lightweight-charts';
import { fetchFutureOhlc } from '../lib/services/future';
import type { FutureOhlcResponse } from '../lib/services/future';
import BsiPanel from '../components/chart/panels/BsiPanel';
import ZScorePanel from '../components/chart/panels/ZScorePanel';
import KamaPanel from '../components/chart/panels/KamaPanel';

const SYMBOL = 'VN30F1M';

export default function Future() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [data, setData] = useState<FutureOhlcResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(2.0);

  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true);
        const response = await fetchFutureOhlc(SYMBOL);
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
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current || !data) return;
    const candleSeries = chartRef.current.addSeries(CandlestickSeries, {
      upColor: '#26a69a', downColor: '#ef5350',
      borderUpColor: '#26a69a', borderDownColor: '#ef5350',
      wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    }, 0);
    const candleData = data.timestamps.map((ts, i) => ({
      time: (new Date(ts).getTime() / 1000) as UTCTimestamp,
      open: data.ohlc.open[i], high: data.ohlc.high[i],
      low: data.ohlc.low[i], close: data.ohlc.close[i],
    }));
    candleSeries.setData(candleData);
    chartRef.current.timeScale().fitContent();
  }, [data]);

  return (
    <Box sx={{ p: 3 }}>
      <Stack spacing={2}>
        <Typography variant="h4">⚡ Future — {SYMBOL} (1H)</Typography>
        <Paper sx={{ p: 2 }}>
          <Typography variant="caption">Z-Score Threshold: {threshold.toFixed(1)}</Typography>
          <Slider value={threshold} min={0.5} max={4} step={0.5}
                  onChange={(_, v) => setThreshold(v as number)}
                  sx={{ width: 200, ml: 2 }} />
        </Paper>
        {loading && <CircularProgress />}
        {error && <Alert severity="error">{error}</Alert>}
        <Paper sx={{ p: 0 }}>
          <div ref={chartContainerRef} style={{ width: '100%' }} />
          {chartRef.current && data && (
            <>
              <KamaPanel chart={chartRef.current} data={data} />
              <BsiPanel chart={chartRef.current} data={data} />
              <ZScorePanel chart={chartRef.current} data={data} threshold={threshold} />
            </>
          )}
        </Paper>
      </Stack>
    </Box>
  );
}
