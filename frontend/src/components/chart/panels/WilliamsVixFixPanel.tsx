import { useEffect, useRef } from 'react';
import {
  HistogramSeries,
  LineSeries,
  createSeriesMarkers,
} from 'lightweight-charts';
import type { IChartApi, ISeriesApi, SeriesMarker, UTCTimestamp } from 'lightweight-charts';

type WilliamsVixFixPanelProps = {
  chart: IChartApi;
  data: any;
  timestamps: string[];
  paneIndex: number;
  visible?: boolean;
};

export default function WilliamsVixFixPanel({
  chart,
  data,
  timestamps,
  paneIndex,
  visible,
}: WilliamsVixFixPanelProps) {
  const wvfSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const markerSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const markersRef = useRef<any>(null);

  // Toggle series visibility
  useEffect(() => {
    const show = visible !== false;
    wvfSeriesRef.current?.applyOptions({ visible: show });
    markerSeriesRef.current?.applyOptions({ visible: show });
  }, [visible]);

  useEffect(() => {
    if (!chart) return;

    const wvfSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: 'right',
      title: 'WVF',
      priceFormat: {
        type: 'custom',
        formatter: (price: number) => price.toFixed(2),
      },
    }, paneIndex);
    wvfSeries.moveToPane(paneIndex);
    wvfSeriesRef.current = wvfSeries;

    const markerSeries = chart.addSeries(LineSeries, {
      color: 'transparent',
      lineWidth: 1,
      lineVisible: false,
      priceScaleId: 'right',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    }, paneIndex);
    markerSeries.moveToPane(paneIndex);
    markerSeriesRef.current = markerSeries;

    return () => {
      const series = wvfSeriesRef.current;
      const markerSeries = markerSeriesRef.current;
      wvfSeriesRef.current = null;
      markerSeriesRef.current = null;
      markersRef.current = null;
      if (!series && !markerSeries) return;
      try {
        if (series) chart.removeSeries(series);
        if (markerSeries) chart.removeSeries(markerSeries);
      } catch (e) {
        console.warn('Error removing WVF series:', e);
      }
    };
  }, [chart, paneIndex]);

  useEffect(() => {
    if (!data || !timestamps) {
      markersRef.current?.setMarkers([]);
      return;
    }

    const wvf = data.williams_vix_fix?.wvf ?? [];
    const filtered = data.williams_vix_fix?.filtered ?? [];
    const condFe = data.williams_vix_fix?.cond_fe ?? [];

    const histogramData = timestamps.map((timestamp: string, i: number) => {
      const value = wvf[i];
      if (typeof value !== 'number') return null;
      const color = filtered[i] ? 'rgba(255, 255, 0, 0.7)' : 'rgba(34, 197, 94, 0.7)';
      return {
        time: (new Date(timestamp).getTime() / 1000) as UTCTimestamp,
        value,
        color,
      };
    }).filter(Boolean) as { time: UTCTimestamp; value: number; color: string }[];

    wvfSeriesRef.current?.setData(histogramData);

    const markerSeriesData = timestamps.map((timestamp: string, i: number) => ({
      time: (new Date(timestamp).getTime() / 1000) as UTCTimestamp,
      value: wvf[i] ?? 0,
    })).filter(d => d.value !== 0);
    markerSeriesRef.current?.setData(markerSeriesData);

    const markers = timestamps.map((timestamp: string, i: number) => {
      if (!condFe[i]) return null;
      return {
        time: (new Date(timestamp).getTime() / 1000) as UTCTimestamp,
        position: 'aboveBar',
        color: '#3b82f6',
        shape: 'arrowUp',
        text: 'FE',
        size: 1,
      } satisfies SeriesMarker<UTCTimestamp>;
    }).filter(Boolean) as SeriesMarker<UTCTimestamp>[];

    if (markerSeriesRef.current) {
      if (!markersRef.current) {
        markersRef.current = createSeriesMarkers(markerSeriesRef.current, markers);
      } else {
        markersRef.current.setMarkers(markers);
      }
    }
  }, [data, timestamps]);

  return null;
}
