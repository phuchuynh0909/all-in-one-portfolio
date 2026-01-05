import { useEffect, useRef } from 'react';
import { IChartApi, ISeriesApi, LineSeries } from 'lightweight-charts';
import {
    formatIndicatorData,
    createConstantLine
} from '../../../lib/services/timeseries';

type RsiPanelProps = {
    chart: IChartApi;
    data: any; // Indicators data
    timestamps: string[];
};

export default function RsiPanel({ chart, data, timestamps }: RsiPanelProps) {
    const rsiSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const rsi5SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const overboughtLineRef = useRef<ISeriesApi<"Line"> | null>(null);
    const oversoldLineRef = useRef<ISeriesApi<"Line"> | null>(null);

    useEffect(() => {
        if (!chart) return;

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
        rsiSeriesRef.current = rsiSeries;

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
        rsi5SeriesRef.current = rsi5Series;

        // Shared config for helper/reference lines
        const defaultFixedLineConfig = {
            priceScaleId: 'right',
            priceLineVisible: true,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
        } as const;

        // Add horizontal lines for overbought/oversold levels
        const overboughtLine = chart.addSeries(LineSeries, {
            color: 'rgba(239, 68, 68, 0.5)',
            lineWidth: 1,
            ...defaultFixedLineConfig,
        }, 1);
        overboughtLineRef.current = overboughtLine;

        const oversoldLine = chart.addSeries(LineSeries, {
            color: 'rgba(34, 197, 94, 0.5)',
            lineWidth: 1,
            ...defaultFixedLineConfig,
        }, 1);
        oversoldLineRef.current = oversoldLine;

        return () => {
            if (chart) {
                try {
                    chart.removeSeries(rsiSeries);
                    chart.removeSeries(rsi5Series);
                    chart.removeSeries(overboughtLine);
                    chart.removeSeries(oversoldLine);
                } catch (e) {
                    console.warn('Error removing series:', e);
                }
            }
            rsiSeriesRef.current = null;
            rsi5SeriesRef.current = null;
            overboughtLineRef.current = null;
            oversoldLineRef.current = null;
        };
    }, [chart]);

    // Update data
    useEffect(() => {
        if (!data || !timestamps) return;

        const rsiChartData = formatIndicatorData(timestamps, data.rsi ?? []);
        rsiSeriesRef.current?.setData(rsiChartData);

        const rsi5ChartData = formatIndicatorData(timestamps, data.rsi_5 ?? []);
        rsi5SeriesRef.current?.setData(rsi5ChartData);

        const timeRange = createConstantLine(rsiChartData, 70);
        const timeRange2 = createConstantLine(rsiChartData, 30);
        overboughtLineRef.current?.setData(timeRange);
        oversoldLineRef.current?.setData(timeRange2);
    }, [data, timestamps]);

    return null;
}
