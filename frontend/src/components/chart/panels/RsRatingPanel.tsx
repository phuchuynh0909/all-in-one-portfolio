import { useEffect, useRef } from 'react';
import { useChartTheme } from '../../../theme';
import { LineSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi } from 'lightweight-charts';
import { formatIndicatorData } from '../../../lib/services/timeseries';

type RsRatingPanelProps = {
    chart: IChartApi;
    data: any; // Indicators data
    timestamps: string[];
};

export default function RsRatingPanel({ chart, data, timestamps }: RsRatingPanelProps) {
    const ct = useChartTheme();
    const rsRating20SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const rsRating20EmaSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const rsRating50SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const rsRating252SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

    useEffect(() => {
        if (!chart) return;

        // Create RS Rating series in a separate pane (Panel 4)
        const rsRating20Series = chart.addSeries(LineSeries, {
            color: ct.seriesColor(7),  // Orange
            lineWidth: 2,
            priceFormat: {
                type: 'custom',
                formatter: (price: number) => price.toFixed(0),
            },
            title: 'RS Rating 20',
            priceScaleId: 'right',
        }, 4);
        rsRating20Series.moveToPane(4);
        rsRating20SeriesRef.current = rsRating20Series;

        // Create RS Rating EMA series in the same pane
        const rsRating20EmaSeries = chart.addSeries(LineSeries, {
            color: ct.seriesColor(2),  // Violet
            lineWidth: 2,
            priceFormat: {
                type: 'custom',
                formatter: (price: number) => price.toFixed(0),
            },
            title: 'RS Rating 20 EMA',
            priceScaleId: 'right',
        }, 4);
        rsRating20EmaSeries.moveToPane(4);
        rsRating20EmaSeriesRef.current = rsRating20EmaSeries;

        // Create RS Rating 50 series in the same pane
        const rsRating50Series = chart.addSeries(LineSeries, {
            color: ct.seriesColor(8),  // Teal
            lineWidth: 2,
            priceFormat: {
                type: 'custom',
                formatter: (price: number) => price.toFixed(0),
            },
            title: 'RS Rating 50',
            priceScaleId: 'right',
        }, 4);
        rsRating50Series.moveToPane(4);
        rsRating50SeriesRef.current = rsRating50Series;

        // Create RS Rating 252 series in the same pane
        const rsRating252Series = chart.addSeries(LineSeries, {
            color: ct.seriesColor(3),  // Green
            lineWidth: 2,
            priceFormat: {
                type: 'custom',
                formatter: (price: number) => price.toFixed(0),
            },
            title: 'RS Rating 252',
            priceScaleId: 'right',
        }, 4);
        rsRating252Series.moveToPane(4);
        rsRating252SeriesRef.current = rsRating252Series;

        return () => {
            const rs20 = rsRating20SeriesRef.current;
            const rs20Ema = rsRating20EmaSeriesRef.current;
            const rs50 = rsRating50SeriesRef.current;
            const rs252 = rsRating252SeriesRef.current;
            rsRating20SeriesRef.current = null;
            rsRating20EmaSeriesRef.current = null;
            rsRating50SeriesRef.current = null;
            rsRating252SeriesRef.current = null;
            if (!chart) return;
            try {
                if (rs20) chart.removeSeries(rs20);
                if (rs20Ema) chart.removeSeries(rs20Ema);
                if (rs50) chart.removeSeries(rs50);
                if (rs252) chart.removeSeries(rs252);
            } catch (e) {
                console.warn('Error removing series:', e);
            }
        };
    }, [chart]);

    // Update data
    useEffect(() => {
        if (!data || !timestamps) return;

        const rsRating20Data = formatIndicatorData(timestamps, data.rs_rating_20 ?? []);
        const rsRating20EmaData = formatIndicatorData(timestamps, data.rs_rating_20_ema ?? []);
        const rsRating50Data = formatIndicatorData(timestamps, data.rs_rating_50 ?? []);
        const rsRating252Data = formatIndicatorData(timestamps, data.rs_rating_252 ?? []);

        rsRating20SeriesRef.current?.setData(rsRating20Data);
        rsRating20EmaSeriesRef.current?.setData(rsRating20EmaData);
        rsRating50SeriesRef.current?.setData(rsRating50Data);
        rsRating252SeriesRef.current?.setData(rsRating252Data);
    }, [data, timestamps]);

    return null;
}
