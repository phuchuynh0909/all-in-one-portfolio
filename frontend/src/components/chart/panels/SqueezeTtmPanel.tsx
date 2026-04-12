import { useEffect, useRef } from 'react';
import { HistogramSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts';
import { formatChartTime } from '../../../lib/services/timeseries';

type SqueezeTtmPanelProps = {
  chart: IChartApi;
  data: any;
  timestamps: string[];
  paneIndex: number;
  visible?: boolean;
};

export default function SqueezeTtmPanel({
  chart,
  data,
  timestamps,
  paneIndex,
  visible,
}: SqueezeTtmPanelProps) {
  const histogramSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  // Toggle series visibility
  useEffect(() => {
    histogramSeriesRef.current?.applyOptions({ visible: visible !== false });
  }, [visible]);

  useEffect(() => {
    if (!chart) return;

    const histogramSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: 'right',
      title: 'Squeeze TTM',
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => price.toFixed(2),
      },
    }, paneIndex);
    histogramSeries.moveToPane(paneIndex);
    histogramSeriesRef.current = histogramSeries;

    return () => {
      const series = histogramSeriesRef.current;
      histogramSeriesRef.current = null;
      if (!series) return;
      try {
        chart.removeSeries(series);
      } catch (e) {
        console.warn('Error removing Squeeze TTM series:', e);
      }
    };
  }, [chart, paneIndex]);

  useEffect(() => {
    if (!data || !timestamps) return;

    const histogram = data.squeeze_ttm?.histogram ?? [];
    const squeezeOn = data.squeeze_ttm?.squeeze_on ?? [];

    const histogramData = timestamps.map((timestamp: string, i: number) => {
      const value = histogram[i];
      if (typeof value !== 'number') return null;
      const isSqueeze = !!squeezeOn[i];
      const color = isSqueeze ? 'rgba(239, 68, 68, 0.7)' : 'rgba(34, 197, 94, 0.7)';
      return {
        time: formatChartTime(timestamp) as UTCTimestamp,
        value,
        color,
      };
    }).filter(Boolean) as { time: UTCTimestamp; value: number; color: string }[];

    histogramSeriesRef.current?.setData(histogramData);
  }, [data, timestamps]);

  return null;
}
