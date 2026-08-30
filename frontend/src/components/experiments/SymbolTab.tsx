import { useEffect, useMemo, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Box, Stack, Typography } from '@mui/material';
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, SeriesMarker, UTCTimestamp } from 'lightweight-charts';
import { fetchTimeseries, formatChartTime } from '../../lib/services/timeseries';
import { getTrades } from '../../lib/experiments/queries';
import type { RunMeta, TradeRow } from '../../lib/experiments/types';

export default function SymbolTab({ run, symbol }: { run: RunMeta; symbol: string | null }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markersRef = useRef<ReturnType<typeof createSeriesMarkers> | null>(null);

  const bars = useQuery({
    queryKey: ['experiments', 'ohlc', symbol, run.data_start, run.data_end],
    queryFn: () =>
      fetchTimeseries(symbol as string, {
        interval: '1d',
        start_date: run.data_start,
        end_date: run.data_end,
      }),
    enabled: Boolean(symbol),
  });

  const trades = useQuery({
    queryKey: ['experiments', run.run_id, 'trades'],
    queryFn: () => getTrades(run),
    enabled: Boolean(symbol),
  });

  const candles = useMemo(() => {
    if (!bars.data) return [];
    const { timestamps, timeseries } = bars.data;
    return timestamps.map((ts, i) => ({
      time: formatChartTime(ts),
      open: timeseries.open[i],
      high: timeseries.high[i],
      low: timeseries.low[i],
      close: timeseries.close[i],
    }));
  }, [bars.data]);

  const markers = useMemo<SeriesMarker<UTCTimestamp>[]>(() => {
    const rows = ((trades.data ?? []) as TradeRow[]).filter((t) => t.symbol === symbol);
    return rows
      .flatMap((t) => {
        const entry: SeriesMarker<UTCTimestamp> = {
          time: formatChartTime(String(t.entry_dt)),
          position: 'belowBar',
          color: '#22c55e',
          shape: 'arrowUp',
          text: `BUY @${Number(t.entry_price).toFixed(2)}`,
        };
        if (!t.exit_dt) return [entry];
        const net = Number(t.net_return ?? 0);
        const exit: SeriesMarker<UTCTimestamp> = {
          time: formatChartTime(String(t.exit_dt)),
          position: 'aboveBar',
          color: net >= 0 ? '#3b82f6' : '#ef4444',
          shape: 'arrowDown',
          text: `${(net * 100).toFixed(1)}%`,
        };
        return [entry, exit];
      })
      .sort((a, b) => Number(a.time) - Number(b.time));
  }, [trades.data, symbol]);

  useEffect(() => {
    if (!containerRef.current || chartRef.current) return;
    const chart = createChart(containerRef.current, { height: 460 });
    chartRef.current = chart;
    seriesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      markersRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !candles.length) return;
    series.setData(candles);
    markersRef.current = createSeriesMarkers(series, markers);
    chartRef.current?.timeScale().fitContent();
  }, [candles, markers]);

  if (!symbol) return <Alert severity="info">Pick a symbol from Overview or Trades.</Alert>;
  if (bars.error) return <Alert severity="error">{(bars.error as Error).message}</Alert>;
  if (trades.error) return <Alert severity="error">{(trades.error as Error).message}</Alert>;

  return (
    <Stack spacing={1}>
      <Typography variant="subtitle2">
        {symbol} — {markers.length} markers across {candles.length} bars
      </Typography>
      <Box ref={containerRef} />
    </Stack>
  );
}
